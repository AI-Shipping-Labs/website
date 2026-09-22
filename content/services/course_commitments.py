"""Read-only learner commitments for one authorized course and selected cohort."""

from __future__ import annotations

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
from content.services.course_home import _can_open_unit
from content.services.course_units import decide_course_unit_drip_lock
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES
from events.services.display_time import format_event_time_range, resolve_event_display_timezone


def _row(kind, title, *, when=None, status='', url='', action='', detail='', complete=False,
         closed=False, timezone_name='Europe/Berlin'):
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
        ))
    return rows


def _project_rows(course, user, cohort, timezone_name, now):
    projects = list(CourseProject.objects.filter(course=course).filter(
        Q(cohort=cohort) | Q(cohort__isnull=True),
    ).order_by('submission_due_at', 'pk'))
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
            ))
        else:
            open_for_submission = now < project.submission_due_at
            rows.append(_row(
                'Project', project.title, when=project.submission_due_at,
                status='Not submitted' if open_for_submission else 'Closed',
                url=submit_url if open_for_submission else '',
                action='Submit project' if open_for_submission else '',
                closed=not open_for_submission, timezone_name=timezone_name,
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
        ))
    return rows


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

    next_event = next(
        (row for row in events if row['status'] in ('Live now', 'In progress', 'Upcoming')),
        None,
    )
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
    return {
        'coming_up': upcoming,
        'schedule_rows': schedule,
        'past_events': [row for row in events if row['complete'] and row['action']],
        'open_assignments': open_assignments,
        'completed_assignments': completed_assignments,
        'commitment_timezone': timezone_name,
    }
