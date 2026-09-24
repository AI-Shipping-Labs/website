"""Read-only learner commitments for one authorized course and selected cohort."""

from __future__ import annotations

import datetime
from collections import defaultdict
from urllib.parse import urlencode

from community_base.homework_steps.models import HomeworkDraft
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format

from content.access import get_user_level
from content.models import CohortEnrollment, CourseAccess, Unit
from content.models.homework import Homework, Submission
from content.models.peer_review import CourseProject, PeerReview, ProjectSubmission
from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK, UNIT_KIND_LESSON
from content.services.course_home import (
    _all_module_units,
    _can_open_unit,
    _current_cohort_module,
    _is_orientation_module,
    _ordered_modules,
)
from content.services.course_units import (
    build_module_week_dates,
    decide_course_unit_access,
    decide_course_unit_drip_lock,
)
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES
from events.services.display_time import format_event_time_range, resolve_event_display_timezone


def _row(kind, title, *, when=None, status='', url='', action='', detail='', complete=False,
         closed=False, timezone_name='Europe/Berlin', unit_content_id='',
         series_position=None, project_group_id=None, project_group_title=''):
    if status in ('Submitted', 'Completed', 'Live now'):
        status_tone = 'success'
    elif status in ('Upcoming', 'In progress', 'Draft saved'):
        status_tone = 'info'
    elif status in ('Closed', 'Past', 'Waiting for reviews'):
        status_tone = 'muted'
    elif status.endswith(' complete'):
        status_tone = 'warning'
    else:
        status_tone = 'neutral'
    return {
        'kind': kind,
        'title': title,
        'when': when,
        'when_label': format_event_time_range(when, None, timezone_name) if when else '',
        'status': status,
        'status_tone': status_tone,
        'url': url,
        'action': action,
        'detail': detail,
        'complete': complete,
        'closed': closed,
        'unit_content_id': unit_content_id,
        'series_position': series_position,
        'project_group_id': project_group_id,
        'project_group_title': project_group_title,
    }


def _event_rows(cohort, timezone_name, now):
    if cohort is None or not cohort.event_series_id:
        return []
    events = Event.objects.filter(
        event_series_id=cohort.event_series_id,
        status__in=PUBLIC_EVENT_STATUSES,
    ).order_by('start_datetime', 'pk')
    rows = []
    for event in events:
        if event.is_past:
            if event.recap_is_published:
                url, action = event.get_recap_url(), 'Read recap'
            elif event.has_recording:
                url, action = event.get_recording_url(), 'Watch recording'
            else:
                url, action = '', ''
            status = 'Past'
        elif event.can_show_zoom_link():
            url, action, status = event.get_join_url(), 'Join now', 'Live now'
        elif event.start_datetime <= now:
            url, action, status = event.get_absolute_url(), 'View session', 'In progress'
        else:
            url, action, status = event.get_absolute_url(), 'View session', 'Upcoming'
        rows.append(_row(
            'Live session', event.title, when=event.start_datetime,
            status=status, url=url, action=action,
            complete=event.is_past, timezone_name=timezone_name,
            series_position=event.series_position,
        ))
    return rows


def _homework_rows(course, user, cohort, timezone_name, today):
    if cohort is None:
        return []
    homeworks = list(Homework.objects.filter(cohort=cohort).select_related('cohort').order_by(
        'due_date', 'pk',
    ))
    if not homeworks:
        return []
    units_by_content_id = {
        unit.content_id: unit
        for unit in Unit.objects.filter(
            module__course=course, kind='homework',
            content_id__in=[homework.content_id for homework in homeworks],
        ).select_related('module__course', 'module__parent').order_by(
            'module__sort_order', 'sort_order',
        )
    }
    user_level = get_user_level(user)
    individual_access = CourseAccess.objects.filter(user=user, course=course).exists()
    submissions = set(Submission.objects.filter(
        student=user, homework__in=homeworks,
    ).values_list('homework_id', flat=True))
    drafts = dict(HomeworkDraft.objects.filter(
        user=user,
        assignment_key__in=[f'aisl:homework:{homework.pk}' for homework in homeworks],
    ).values_list('assignment_key', 'revision'))
    suffix = (
        f'?{urlencode({"cohort": cohort.external_key})}'
        if cohort.mode == 'cohort' and cohort.external_key else ''
    )
    rows = []
    for homework in homeworks:
        unit = units_by_content_id.get(homework.content_id)
        url = unit.get_absolute_url() + suffix if unit else ''
        submitted = homework.pk in submissions
        accepting = homework.is_accepting_submissions
        unit_access = bool(unit and _can_open_unit(
            unit, user_level=user_level, individual_access=individual_access,
            entitlement_mode=course.access_mode == 'entitlement',
            is_staff=user.is_staff or user.is_superuser,
            verified=user.email_verified,
        ))
        drip = (
            decide_course_unit_drip_lock(user, unit, today=today, cohort=cohort)
            if unit_access else None
        )
        can_open = unit_access and not drip.is_locked
        if submitted:
            status, action = 'Submitted', 'View submission' if can_open else ''
        elif not accepting:
            status, action = 'Closed', ''
        elif not can_open:
            status, action = 'Not submitted', ''
        elif drafts.get(f'aisl:homework:{homework.pk}', 0) > 0:
            status, action = 'Draft saved', 'Continue homework' if url else ''
        else:
            status, action = 'Not submitted', 'Start homework' if url else ''
        detail = ''
        if homework.is_self_paced:
            detail = 'No scheduled deadline'
        elif not submitted and not unit_access:
            detail = 'This homework unit is locked.'
        elif not submitted and drip and drip.is_locked and drip.available_date:
            detail = f'Available {date_format(drip.available_date, "F j, Y")}'
        rows.append(_row(
            'Homework', homework.title,
            when=None if homework.is_self_paced else homework.due_date,
            status=status, url=url if action else '', action=action,
            detail=detail,
            complete=submitted, closed=not accepting and not submitted,
            timezone_name=timezone_name,
            unit_content_id=str(homework.content_id) if homework.content_id else '',
        ))
    return rows


def _project_rows(course, user, cohort, timezone_name, now):
    projects = list(CourseProject.objects.filter(course=course).filter(
        Q(cohort=cohort) | Q(cohort__isnull=True),
    ).select_related('module__parent').order_by('submission_due_at', 'pk'))
    if not projects:
        return []
    submissions = {
        submission.course_project_id: submission
        for submission in ProjectSubmission.objects.filter(
            user=user, course_project__in=projects,
        )
    }
    reviews_by_project = defaultdict(list)
    for review in PeerReview.objects.filter(
        reviewer=user, submission__course_project__in=projects,
    ).select_related('submission'):
        reviews_by_project[review.submission.course_project_id].append(review)
    rows = []
    for project in projects:
        submission = submissions.get(project.pk)
        submit_url = f'/courses/{course.slug}/projects/{project.slug}/submit'
        reviews_url = f'/courses/{course.slug}/projects/{project.slug}/reviews'
        if submission:
            rows.append(_row(
                'Project', project.title, when=project.submission_due_at,
                status='Submitted', url=submit_url, action='View project',
                complete=True, timezone_name=timezone_name,
                project_group_id=project.module.parent_id or project.module_id
                if project.module_id else project.title,
                project_group_title=(
                    project.module.parent.title if project.module_id and project.module.parent_id
                    else project.module.title if project.module_id else project.title
                ),
            ))
        else:
            open_for_submission = now < project.submission_due_at
            rows.append(_row(
                'Project', project.title, when=project.submission_due_at,
                status='Not submitted' if open_for_submission else 'Closed',
                url=submit_url if open_for_submission else '',
                action='Submit project' if open_for_submission else '',
                closed=not open_for_submission, timezone_name=timezone_name,
                project_group_id=project.module.parent_id or project.module_id
                if project.module_id else project.title,
                project_group_title=(
                    project.module.parent.title if project.module_id and project.module.parent_id
                    else project.module.title if project.module_id else project.title
                ),
            ))

        if not submission or not course.peer_review_enabled:
            continue
        reviews = reviews_by_project[project.pk]
        completed = sum(review.is_complete for review in reviews)
        unfinished_assigned = any(not review.is_complete for review in reviews)
        target = project.peer_review_count or course.peer_review_count
        remaining = max(target - completed, 0)
        review_open = now < project.review_due_at
        if not reviews and remaining:
            status = 'Waiting for reviews' if review_open else 'Closed'
            action = ''
        elif remaining and review_open and unfinished_assigned:
            status, action = f'{completed} of {target} complete', 'Continue reviews'
        elif remaining and review_open:
            status, action = 'Waiting for reviews', ''
        elif remaining:
            status, action = 'Closed', ''
        else:
            status, action = 'Completed', 'View reviews'
        rows.append(_row(
            'Peer reviews', project.title, when=project.review_due_at,
            status=status, url=reviews_url if action else '', action=action,
            detail=f'{completed} of {target} reviews completed',
            complete=remaining == 0, closed=remaining > 0 and not review_open,
            timezone_name=timezone_name,
            project_group_id=project.module.parent_id or project.module_id
            if project.module_id else project.title,
            project_group_title=(
                project.module.parent.title if project.module_id and project.module.parent_id
                else project.module.title if project.module_id else project.title
            ),
        ))
    return rows


def _focus_work_items(course, user, cohort, homework_rows, *, now):
    """Keep authored homework visible even when no submission form was synced."""
    modules = _ordered_modules(course)
    unscheduled_cohort = cohort is None or cohort.mode == 'self_paced'
    if unscheduled_cohort:
        # Unlinked learners and self-paced members have no calendar week.
        # Keep their authored work visible in the first instructional module
        # without inventing a due date or a cohort schedule.
        orientation_ids = {
            modules[0].pk
        } if modules and _is_orientation_module(modules[0]) else set()
        module = next((
            candidate for candidate in modules
            if not candidate.is_bonus and candidate.pk not in orientation_ids
            and any(
                unit.kind in (UNIT_KIND_LESSON, UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK)
                and not unit.effective_is_bonus
                for unit in _all_module_units(candidate)
            )
        ), None)
    elif cohort.start_date is None:
        return []
    else:
        week_dates = build_module_week_dates(
            modules, cohort, extend_final_to_cohort_end=course.slug == 'ai-buildcamp',
        )
        module = _current_cohort_module(modules, week_dates, cohort, now.date())
    if module is None:
        return []

    user_level = get_user_level(user)
    individual_access = CourseAccess.objects.filter(user=user, course=course).exists()
    homework_by_content_id = {
        row['unit_content_id']: row
        for row in homework_rows if row.get('unit_content_id')
    }
    suffix = (
        f'?{urlencode({"cohort": cohort.external_key})}'
        if cohort and cohort.mode == 'cohort' and cohort.external_key else ''
    )
    items = []
    for unit in _all_module_units(module):
        if unit.kind != UNIT_KIND_HOMEWORK or unit.effective_is_bonus:
            continue
        unit_content_id = str(unit.content_id) if unit.content_id else ''
        commitment = homework_by_content_id.get(unit_content_id)
        unit_access = _can_open_unit(
            unit, user_level=user_level, individual_access=individual_access,
            entitlement_mode=course.access_mode == 'entitlement',
            is_staff=user.is_staff or user.is_superuser,
            verified=user.email_verified,
        ) and decide_course_unit_access(user, unit).has_access
        drip = (
            decide_course_unit_drip_lock(user, unit, today=now.date(), cohort=cohort)
            if unit_access else None
        )
        can_open = unit_access and not drip.is_locked
        unit_url = unit.get_absolute_url() + suffix if can_open else ''
        url = commitment['url'] if commitment and commitment['url'] else unit_url
        action = commitment['action'] if commitment and commitment['action'] else (
            'Review homework' if commitment and commitment['closed']
            else 'Open project step' if 'capstone' in unit.title.casefold()
            else 'Open homework'
        )
        if commitment and commitment['when']:
            due_soon = 0 <= (commitment['when'] - now).total_seconds() <= 7 * 24 * 60 * 60
        else:
            due_soon = False
        items.append({
            'unit': unit,
            'content_id': unit_content_id,
            'commitment': commitment,
            'url': url,
            'action': action if url else '',
            'available_date': drip.available_date if drip and drip.is_locked else None,
            'due_soon': due_soon,
        })
    return items


def _next_linked_session(course, events):
    """Return one event that is backed by an event unit in this course."""
    modules = _ordered_modules(course)
    units_by_position = {}
    for module in modules:
        for unit in _all_module_units(module):
            if unit.kind != UNIT_KIND_EVENT or unit.session_position is None:
                continue
            units_by_position.setdefault(unit.session_position, unit)
    for row in events:
        position = row['series_position']
        if position in units_by_position:
            row['session_unit'] = units_by_position[position]

    linked = [row for row in events if row.get('session_unit')]
    upcoming = [row for row in linked if row['status'] in ('Live now', 'In progress', 'Upcoming')]
    if upcoming:
        return min(upcoming, key=lambda row: row['when'])
    recoverable = [row for row in linked if row['action']]
    return max(recoverable, key=lambda row: row['when']) if recoverable else None


def _home_project_commitment_rows(assignments):
    """Collapse alternate submission windows to one current project action."""
    grouped = defaultdict(list)
    for row in assignments:
        if row['kind'] in ('Project', 'Peer reviews') and row['project_group_id'] is not None:
            grouped[row['project_group_id']].append(row)

    collapsed = []
    for rows in grouped.values():
        project_rows = [row for row in rows if row['kind'] == 'Project']
        submitted = any(row['status'] == 'Submitted' for row in project_rows)
        if submitted:
            actionable = [
                row for row in rows
                if row['kind'] == 'Peer reviews' and row['action'] and row['when']
            ]
        else:
            actionable = [
                row for row in project_rows if row['action'] and row['when']
            ]
        if actionable:
            chosen = min(actionable, key=lambda row: row['when'])
            chosen = dict(chosen)
            chosen['title'] = chosen['project_group_title'] or chosen['title']
            if chosen['kind'] == 'Project' and len(project_rows) > 1:
                chosen['detail'] = 'Alternative submission windows for one project.'
            collapsed.append(chosen)
    return collapsed


def build_course_commitments(course, user, cohort, *, now=None):
    """Build schedule and tasks without mixing data from another cohort."""
    now = now or timezone.now()
    if cohort is not None and (
        cohort.course_id != course.pk
        or not (user.is_staff or CohortEnrollment.objects.filter(
            user=user, cohort=cohort,
        ).exists())
    ):
        raise PermissionDenied('Cohort is not available to this learner')

    timezone_name = resolve_event_display_timezone(user)
    events = _event_rows(cohort, timezone_name, now)
    assignments = _homework_rows(course, user, cohort, timezone_name, now.date())
    assignments += _project_rows(course, user, cohort, timezone_name, now)
    open_assignments = [row for row in assignments if not row['complete']]
    completed_assignments = [row for row in assignments if row['complete']]

    homework_rows = [row for row in assignments if row['kind'] == 'Homework']
    focus_work_items = _focus_work_items(course, user, cohort, homework_rows, now=now)
    focus_work_content_ids = {item['content_id'] for item in focus_work_items}
    for item in focus_work_items:
        item['due_soon'] = bool(item['due_soon'])

    next_live_session = _next_linked_session(course, events)
    if next_live_session:
        session_unit = next_live_session['session_unit']
        next_live_session['module_title'] = (
            session_unit.module.parent.title if session_unit.module.parent_id
            else session_unit.module.title
        )

    next_event = next(
        (row for row in events if row['status'] in ('Live now', 'In progress', 'Upcoming')),
        None,
    )
    featured_live_session = next_event or next_live_session
    if featured_live_session is None:
        featured_live_session = next(
            (row for row in reversed(events) if row['action']), None,
        )
    upcoming_sessions = [row for row in events if not row['complete']]
    past_sessions = [row for row in events if row['complete']]
    live_session_schedule = sorted(
        upcoming_sessions, key=lambda row: row['when'],
    ) + sorted(past_sessions, key=lambda row: row['when'], reverse=True)
    for row in live_session_schedule:
        row['featured'] = row is featured_live_session
    deadline_tasks = sorted(
        (row for row in open_assignments
         if row['when'] and row['when'] >= now and row['action']),
        key=lambda row: row['when'],
    )
    upcoming = ([next_event] if next_event else []) + deadline_tasks[:2 if next_event else 3]
    schedule = sorted(
        [row for row in events if not row['complete']]
        + [row for row in open_assignments if row['when'] and not row['closed']],
        key=lambda row: row['when'],
    )
    urgent_candidates = [
        row for row in open_assignments
        if row['when'] and row['action'] and now <= row['when'] <= now + datetime.timedelta(days=7)
        and not (row['kind'] == 'Homework' and row['unit_content_id'] in focus_work_content_ids)
    ]
    urgent_candidates = [
        row for row in urgent_candidates if row['kind'] not in ('Project', 'Peer reviews')
    ] + [
        row for row in _home_project_commitment_rows(assignments)
        if row['when'] and now <= row['when'] <= now + datetime.timedelta(days=7)
    ]
    urgent_commitment = (
        min(urgent_candidates, key=lambda row: row['when']) if urgent_candidates else None
    )

    return {
        'coming_up': upcoming,
        'schedule_rows': schedule,
        'live_session_schedule': live_session_schedule,
        'past_events': [row for row in events if row['complete'] and row['action']],
        'open_assignments': open_assignments,
        'completed_assignments': completed_assignments,
        'commitment_timezone': timezone_name,
        'focus_work_items': focus_work_items,
        'next_live_session': next_live_session,
        'urgent_commitment': urgent_commitment,
    }
