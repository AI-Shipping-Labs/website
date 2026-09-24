"""Course unit access and page-context helpers.

The course unit detail view has a few policy branches that need to stay
stable: preview units are public, legacy free-course units still nudge
anonymous visitors to sign up, registered-walled units require verified
accounts, paid units require tier access, and cohort drip locks apply
after tier access has already been granted.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass

from django.db import models
from django.template.defaultfilters import date as django_date
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from accounts.utils.user_checks import is_authenticated_user
from content.access import (
    LEVEL_MAIN,
    LEVEL_OPEN,
    build_gated_access_copy,
    build_gating_context,
    can_access,
    get_gated_reason,
    get_user_level,
)
from content.models import Cohort, CohortEnrollment, Unit, UserCourseProgress
from content.models.cohort import COHORT_MODE_COHORT, COHORT_MODE_SELF_PACED
from content.models.course import UNIT_KIND_EVENT, UNIT_KIND_HOMEWORK, non_bonus_units
from content.models.homework import Homework, QuestionType, Submission
from content.templatetags.video_utils import get_video_thumbnail_url
from content.utils.teaser import first_sentence, truncate_to_words
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES
from events.services.display_time import (
    build_event_time_display,
    format_event_time_range,
    resolve_event_display_timezone,
)
from events.services.series_entitlement import is_entitled_for_series

TEASER_WORD_LIMIT = 150
_UNSPECIFIED_DRIP_COHORT = object()
_SESSION_INTERNAL_DESCRIPTION_NOTE = re.compile(
    r'\s*(?:hidden series|internal note|operator note|staff note|'
    r'registration operations|registration setup)\s*:[^.!?]*(?:[.!?]|$)',
    re.IGNORECASE,
)

ACCESS_GRANTED = 'access_granted'
ACCESS_GRANTED_PREVIEW = 'preview'
ACCESS_DENIED_LEGACY_SIGNIN = 'legacy_anonymous_signin_required'
ACCESS_DENIED_AUTHENTICATION = 'authentication_required'
ACCESS_DENIED_INSUFFICIENT_TIER = 'insufficient_tier'
ACCESS_DENIED_UNVERIFIED_EMAIL = 'unverified_email'
ACCESS_DENIED_ENTITLEMENT_REQUIRED = 'entitlement_required'


@dataclass(frozen=True)
class CourseUnitAccessDecision:
    """Policy decision for opening a course unit page."""

    has_access: bool
    reason: str
    effective_level: int
    gated_reason: str = ''
    status_code: int = 200


@dataclass(frozen=True)
class CourseUnitDripDecision:
    """Drip-schedule decision after tier/unit access has been granted."""

    is_locked: bool
    available_date: datetime.date | None = None


def decide_course_unit_access(user, unit: Unit) -> CourseUnitAccessDecision:
    """Return whether ``user`` may read ``unit`` before drip rules apply.

    This intentionally preserves the legacy anonymous behavior for units
    that inherit ``Course.required_level == LEVEL_OPEN`` without an
    explicit unit or course default access override: anonymous visitors
    still hit the sign-up nudge so completion tracking can be attached to
    an account.
    """
    course = unit.module.course
    effective_level = unit.effective_required_level

    if unit.is_preview:
        return CourseUnitAccessDecision(
            has_access=True,
            reason=ACCESS_GRANTED_PREVIEW,
            effective_level=effective_level,
        )

    if (
        not is_authenticated_user(user)
        and effective_level == LEVEL_OPEN
        and unit.required_level is None
        and course.default_unit_required_level is None
    ):
        return CourseUnitAccessDecision(
            has_access=False,
            reason=ACCESS_DENIED_LEGACY_SIGNIN,
            effective_level=effective_level,
            status_code=403,
        )

    if can_access(user, unit):
        return CourseUnitAccessDecision(
            has_access=True,
            reason=ACCESS_GRANTED,
            effective_level=effective_level,
        )

    gated_reason = get_gated_reason(user, unit)
    status_code = 200 if gated_reason == ACCESS_DENIED_UNVERIFIED_EMAIL else 403
    return CourseUnitAccessDecision(
        has_access=False,
        reason=gated_reason or ACCESS_DENIED_INSUFFICIENT_TIER,
        effective_level=effective_level,
        gated_reason=gated_reason,
        status_code=status_code,
    )


def _effective_drip_offset_days(unit: Unit) -> int | None:
    """Resolve the effective drip offset for ``unit`` (issue #1674).

    "Most specific wins" cascade, the same pattern #465 established for
    ``required_level``/``default_unit_required_level``:

    1. ``Unit.available_after_days`` (existing per-unit override).
    2. The unit's own (leaf) ``Module.available_after_days``.
    3. That module's parent ``Module.available_after_days`` (only
       relevant when the unit's module is a submodule).
    4. ``None`` when none of those is set — unchanged legacy behaviour
       (not locked, no cohort lookup even attempted).
    """
    if unit.available_after_days is not None:
        return unit.available_after_days
    module = unit.module
    if module.available_after_days is not None:
        return module.available_after_days
    if module.parent_id is not None and module.parent.available_after_days is not None:
        return module.parent.available_after_days
    return None


def decide_course_unit_drip_lock(
    user,
    unit: Unit,
    *,
    today: datetime.date | None = None,
    cohort=_UNSPECIFIED_DRIP_COHORT,
) -> CourseUnitDripDecision:
    """Return whether cohort drip scheduling currently locks ``unit``.

    An explicit cohort keeps a selected learner schedule consistent with the
    course home and reader URL. Omission preserves the existing lookup policy.
    """
    if not is_authenticated_user(user):
        return CourseUnitDripDecision(is_locked=False)

    offset_days = _effective_drip_offset_days(unit)
    if offset_days is None:
        return CourseUnitDripDecision(is_locked=False)

    if cohort is _UNSPECIFIED_DRIP_COHORT:
        enrollment = (
            CohortEnrollment.objects
            .filter(
                user=user,
                cohort__course=unit.module.course,
                cohort__is_active=True,
            )
            .select_related('cohort')
            .first()
        )
        cohort = enrollment.cohort if enrollment else None
    # No enrollment at all (today's implicit self-paced — a course that
    # predates mode='self_paced' Cohorts) OR a real self-paced
    # CohortEnrollment (``mode='self_paced'``, ``start_date=None`` by
    # construction) both mean "never drip-locked" — falls out of the same
    # "start_date is None" field with no separate mode branch. Issue
    # #1674 bug fix: this used to only check ``enrollment is None``, which
    # would raise ``TypeError`` (``None + timedelta``) once self-paced
    # learners got real ``CohortEnrollment`` rows.
    if cohort is None or cohort.start_date is None:
        return CourseUnitDripDecision(is_locked=False)

    available_date = cohort.start_date + datetime.timedelta(
        days=offset_days,
    )
    today = today or timezone.now().date()
    if today < available_date:
        return CourseUnitDripDecision(
            is_locked=True,
            available_date=available_date,
        )
    return CourseUnitDripDecision(is_locked=False, available_date=available_date)


def build_gated_course_unit_context(user, course, module, unit, decision):
    """Build template context for a denied course-unit request.

    Issue #1335: the banner copy comes from the one canonical copy builder
    (:func:`content.access.build_gated_access_copy`) so a gated lesson reads
    the same "Sign in to read this lesson" / "Upgrade to {tier} to read this
    lesson" wording as every other surface. The legacy free-course
    anonymous nudge is an authentication wall too (an account is needed for
    completion tracking), so it shares the sign-in copy. The drip-lock
    clock copy stays in :func:`build_drip_locked_course_unit_context`.
    """
    gating = build_gating_context(user, unit, 'unit')
    is_unverified_gate = decision.gated_reason == ACCESS_DENIED_UNVERIFIED_EMAIL
    is_auth_required_gate = decision.gated_reason == ACCESS_DENIED_AUTHENTICATION
    is_entitlement_gate = decision.gated_reason == ACCESS_DENIED_ENTITLEMENT_REQUIRED
    unit_url = unit.get_absolute_url()

    if is_unverified_gate:
        copy = {
            'gated_heading': '',
            'gated_description': '',
            'gated_cta_url': '/membership',
            'gated_cta_label': '',
            'required_tier_name': None,
            'current_user_state': '',
            'signup_cta_url': '',
            'signup_cta_label': '',
        }
    else:
        # A registered wall, or an anonymous visitor on a free course, is an
        # authentication gate. An entitlement-mode course (issue #1658)
        # denial is its own reason — the tier comparison never ran, so it
        # must not collapse into the "Upgrade to {tier}" copy. Everything
        # else (anonymous on a paid course, or a signed-in member below
        # the tier) is an upgrade gate.
        is_free_course_signin = (
            not is_authenticated_user(user) and course.required_level == 0
        )
        if is_entitlement_gate:
            reason = 'entitlement_required'
        elif is_auth_required_gate or is_free_course_signin:
            reason = 'authentication_required'
        else:
            reason = 'insufficient_tier'
        copy_kwargs = dict(
            gated_reason=reason,
            verb='read this lesson',
            noun='lesson',
            required_level=course.required_level,
            user=user,
            resource_url=unit_url,
            upgrade_description=(
                'Get full access to this course and more with a membership.'
            ),
        )
        if is_entitlement_gate:
            copy_kwargs['enroll_url'] = course.enroll_url
            copy_kwargs['program_label'] = course.program_label
        copy = build_gated_access_copy(**copy_kwargs)

    teaser_body_html = None
    if unit.body_html:
        teaser_body_html = truncate_to_words(unit.body_html, TEASER_WORD_LIMIT)

    homework_teaser = ''
    if unit.homework_html:
        homework_text = strip_tags(unit.homework_html).strip()
        homework_teaser = first_sentence(homework_text)

    context = {
        'course': course,
        'module': module,
        'unit': unit,
        'is_gated': True,
        'required_tier_name': copy['required_tier_name'],
        # Legacy aliases kept for callers/tests that still read the pre-#1335
        # key names; they mirror the canonical gated-card values.
        'cta_message': copy['gated_heading'],
        'pricing_url': copy['gated_cta_url'],
        'cta_label': copy['gated_cta_label'],
        'cta_description': copy['gated_description'],
        'teaser_body_html': teaser_body_html,
        'homework_teaser': homework_teaser,
        'video_thumbnail_url': get_video_thumbnail_url(unit.video_url),
        'has_video': bool(unit.video_url),
        'signup_cta_url': copy['signup_cta_url'],
        'signup_cta_label': copy['signup_cta_label'],
        'user_authenticated': is_authenticated_user(user),
        'current_user_state': copy['current_user_state'],
        'gated_card_testid': 'teaser-cta',
        'gated_icon': 'lock',
        'gated_heading': copy['gated_heading'],
        'gated_description': copy['gated_description'],
        'gated_cta_url': copy['gated_cta_url'],
        'gated_cta_label': copy['gated_cta_label'],
        'gated_cta_testid': 'teaser-upgrade-cta',
        'gated_reason': decision.gated_reason,
        # Issue #1658: tells _gated_access_card.html to render the "Sold
        # separately" pill and open the enroll CTA in a new tab instead
        # of the tier "Upgrade" pill/link.
        'gated_entitlement': is_entitlement_gate,
    }
    if is_unverified_gate:
        context.update(gating)
    return context


def build_drip_locked_course_unit_context(course, module, unit, decision):
    """Build template context for a cohort-drip locked unit."""
    available_date = decision.available_date
    formatted_date = available_date.strftime('%B %d, %Y')
    heading = f'This lesson will be available on {formatted_date}'
    course_url = f'/courses/{course.slug}'

    return {
        'course': course,
        'module': module,
        'unit': unit,
        'is_gated': True,
        'is_drip_locked': True,
        'drip_available_date': available_date,
        'cta_message': heading,
        'pricing_url': course_url,
        'gated_card_testid': 'drip-locked-card',
        'gated_icon': 'clock',
        'gated_heading': heading,
        'gated_description': (
            'Your membership already qualifies; the cohort schedule controls '
            'when this lesson opens.'
        ),
        'required_tier_name': '',
        'current_user_state': '',
        'gated_cta_url': course_url,
        'gated_cta_label': 'Back to Course',
        'gated_cta_testid': 'drip-back-cta',
    }


def build_course_unit_navigation_context(user, course, module, unit, *, request=None):
    """Build navigation, completion, discussion, and mobile progress context."""
    modules = course.get_syllabus()
    scoped_module = None
    previous_module = None
    next_module = None
    if course.reader_navigation_scope in ('module', 'submodule'):
        current_root_id = module.parent_id or module.pk
        for index, top_module in enumerate(modules):
            if top_module.pk == current_root_id:
                scoped_module = top_module
                previous_module = modules[index - 1] if index else None
                next_module = modules[index + 1] if index + 1 < len(modules) else None
                break
                break

    completed_unit_ids = set()
    is_completed = False
    if is_authenticated_user(user):
        completed_unit_ids = set(
            UserCourseProgress.objects.filter(
                user=user,
                unit__module__course=course,
                completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )
        is_completed = unit.pk in completed_unit_ids

    next_unit = get_next_unit(course, unit)
    prev_unit = get_prev_unit(course, unit)

    show_discussion = (
        bool(course.discussion_url)
        and course.required_level >= LEVEL_MAIN
        and get_user_level(user) >= LEVEL_MAIN
    )

    # Issue #1674: reader_progress_total/reader_progress_completed exclude
    # bonus modules/units (same denominator as total_units()/
    # completed_units(), via the one named non_bonus_units() filter);
    # event-kind units count like any other unit. reader_progress_current
    # is the unit's 1-indexed position within that same non-bonus reading
    # order — falls back to 1 if the current unit is itself bonus (not a
    # member of the filtered list).
    all_units_ordered = get_all_units_ordered(course)
    non_bonus_unit_ids = set(
        non_bonus_units(Unit.objects.filter(module__course=course))
        .values_list('pk', flat=True)
    )
    flat_units = [u.pk for u in all_units_ordered if u.pk in non_bonus_unit_ids]
    reader_progress_total = len(flat_units)
    try:
        reader_progress_current = flat_units.index(unit.pk) + 1
    except ValueError:
        reader_progress_current = 1
    reader_progress_completed = len(completed_unit_ids & non_bonus_unit_ids)

    # Issue #1674: a kind='event' unit renders a session card above the
    # body. None means either "not a kind='event' unit" or "no Event
    # resolved for any cohort yet" — the template distinguishes those with
    # unit.kind, rendering the clean empty state only for the latter.
    unit_session_entry = (
        build_unit_session_card_context(unit, user, request=request)
        if unit.kind == UNIT_KIND_EVENT else None
    )

    return {
        'course': course,
        'module': module,
        'unit': unit,
        'modules': modules,
        'scoped_module': scoped_module,
        'previous_module': previous_module,
        'next_module': next_module,
        'is_gated': False,
        'has_access': True,
        'completed_unit_ids': completed_unit_ids,
        'is_completed': is_completed,
        'next_unit': next_unit,
        'prev_unit': prev_unit,
        'user_authenticated': is_authenticated_user(user),
        'unit_content_id': str(unit.content_id) if unit.content_id else '',
        'show_discussion': show_discussion,
        'prev_item_url': prev_unit.get_absolute_url() if prev_unit else '',
        'prev_item_title': prev_unit.title if prev_unit else '',
        'next_item_url': next_unit.get_absolute_url() if next_unit else '',
        'next_item_title': next_unit.title if next_unit else '',
        'completion_kind': 'course',
        'completion_button_id': 'mark-complete-btn',
        'completion_url': f'/api/courses/{course.slug}/units/{unit.pk}/complete',
        'bottom_prev_testid': 'bottom-prev-btn',
        'bottom_next_testid': 'bottom-next-btn',
        'reader_mobile_label': 'Course Navigation',
        'reader_progress_kind': 'lesson',
        'reader_progress_current': reader_progress_current,
        'reader_progress_total': reader_progress_total,
        'reader_progress_completed': reader_progress_completed,
        'unit_session_entry': unit_session_entry,
    }


def get_all_units_ordered(course):
    """Return all units in course reading order (issue #1674).

    Depth-first, per the documented contract: top-level modules in
    ``(sort_order, id)`` order; a leaf module yields its own units with
    required lessons before bonus lessons; a parent module yields required
    child submodules before bonus submodules. Mixed content
    (direct units alongside children) is forbidden by ``Module.clean()``,
    so there is no interleaving case. This follows the syllabus grouping,
    which also places bonus content after required content at each level.
    ``kind`` does not affect reading order.

    This is the single ordering helper both ``get_next_unit``/
    ``get_prev_unit`` and the progress-percentage/``reader_progress_*``
    computation call — no second implementation of ordering anywhere.
    Reuses ``Course.get_syllabus()``'s prefetch: four queries total, one
    per tree level, not one per module.
    """
    def syllabus_order(items):
        items = list(items)
        return [item for item in items if not item.is_bonus] + [
            item for item in items if item.is_bonus
        ]

    units = []
    for module in course.get_syllabus():
        children = syllabus_order(module.children.all())
        if children:
            for child in children:
                units.extend(syllabus_order(child.units.all()))
        else:
            units.extend(syllabus_order(module.units.all()))
    return units


def get_next_unit(course, current_unit):
    """Find the next unit in course reading order."""
    all_units = get_all_units_ordered(course)
    for i, unit in enumerate(all_units):
        if unit.pk == current_unit.pk and i + 1 < len(all_units):
            return all_units[i + 1]
    return None


def get_prev_unit(course, current_unit):
    """Find the previous unit in course reading order."""
    all_units = get_all_units_ordered(course)
    for i, unit in enumerate(all_units):
        if unit.pk == current_unit.pk and i > 0:
            return all_units[i - 1]
    return None


# --- Week dates are derived per cohort, never stored (issue #1674) ---
#
# Week dates are cohort-specific (cohort 4's Week 1 is 2026-09-21; a later
# cohort's Week 1 will be a different date), while ``Module`` is curriculum
# shared across every cohort — storing a date on the module would
# reintroduce the exact coupling removed from ``Unit.event``. So dates are
# derived at render time from ``Cohort.start_date`` +
# ``Module.available_after_days``, never stored on ``Module``.


def resolve_viewer_dated_cohort(user, course):
    """Return the viewer's active ``mode='cohort'`` Cohort, or ``None``.

    Display-only — used to derive week dates, never to gate access. A
    self-paced viewer, an anonymous viewer, or a viewer with no dated
    -cohort enrollment all get ``None``, which callers treat as "show no
    date range" (not an error).
    """
    if not is_authenticated_user(user):
        return None
    enrollment = (
        CohortEnrollment.objects
        .filter(
            user=user,
            cohort__course=course,
            cohort__mode=COHORT_MODE_COHORT,
            cohort__is_active=True,
        )
        .select_related('cohort')
        .first()
    )
    return enrollment.cohort if enrollment else None


def build_module_week_dates(top_level_modules, cohort, *, extend_final_to_cohort_end=False):
    """Return ``{module_id: (week_start, week_end)}`` for dated modules.

    Only top-level modules with ``available_after_days`` set get an
    entry. ``week_start = cohort.start_date + available_after_days``.
    ``week_end`` derives from the NEXT top-level sibling's own
    ``available_after_days`` (``next_offset - 1`` day) when the sibling
    has one set; otherwise (including the last week) defaults to a fixed
    7-day block (``week_start + 6`` days). For a course whose final module
    spans multiple weeks, ``extend_final_to_cohort_end`` uses the cohort end
    date for that final dated module.

    Returns ``{}`` when ``cohort`` is ``None`` or self-paced
    (``start_date`` is ``None``) — the caller shows no date range in
    either case.
    """
    if cohort is None or cohort.start_date is None:
        return {}
    modules = list(top_level_modules)
    result = {}
    dated_modules = [module for module in modules if module.available_after_days is not None]
    for idx, module in enumerate(modules):
        if module.available_after_days is None:
            continue
        week_start = cohort.start_date + datetime.timedelta(
            days=module.available_after_days,
        )
        week_end = None
        if idx + 1 < len(modules):
            next_module = modules[idx + 1]
            if next_module.available_after_days is not None:
                week_end = cohort.start_date + datetime.timedelta(
                    days=next_module.available_after_days - 1,
                )
        if week_end is None:
            week_end = week_start + datetime.timedelta(days=6)
            if (
                extend_final_to_cohort_end
                and module == dated_modules[-1]
                and cohort.end_date is not None
                and cohort.end_date > week_end
            ):
                week_end = cohort.end_date
        result[module.pk] = (week_start, week_end)
    return result


def format_week_range(week_start, week_end):
    """Return a compact display string, e.g. ``Oct 12–18`` or
    ``Oct 29–Nov 4`` when the range crosses a month boundary."""
    if week_start.month == week_end.month and week_start.year == week_end.year:
        return f'{django_date(week_start, "M j")}–{week_end.day}'
    return f'{django_date(week_start, "M j")}–{django_date(week_end, "M j")}'


# --- Event units resolve by cohort at render time (issue #1674) ---
#
# There is no ``Unit.event`` FK (a deliberate divergence from
# ``DataTalksClub/community-base#252`` — see the issue's "Design
# reference"/"Event units resolve by cohort at render time" sections). A
# stored FK on shared curriculum would embed one cohort's event into
# curriculum every cohort reads. Instead ``Unit.session_position`` is
# resolved against ``events.Event.series_position`` per viewer at render
# time.


def resolve_session_event(unit: Unit, user) -> Event | None:
    """Resolve the ``Event`` for a ``kind='event'`` unit, or ``None``.

    1. The viewer's own ``CohortEnrollment`` for this unit's course, when
       that cohort is ``mode='cohort'`` (so it may carry an
       ``event_series``) — look up an ``Event`` at
       ``unit.session_position`` in that series.
    2. Otherwise (the viewer's cohort is ``mode='self_paced'``, its
       series has no ``Event`` at that position yet, or the viewer has no
       ``CohortEnrollment`` at all) — fall back to the most recent PAST
       ``mode='cohort'`` cohort of the same course whose series has an
       ``Event`` at that position. This is the normal self-paced path,
       not an error path.
    3. No ``mode='cohort'`` cohort, past or present, has ever had an
       ``Event`` at that position -> ``None`` (renders a clean empty
       state, never a broken link).

    Only draft/cancelled-excluded (``PUBLIC_EVENT_STATUSES``) events are
    considered a match, mirroring the course page's existing
    live-sessions block. Hidden-series matches also require the same staff
    or linked-enrollment entitlement as the event detail and recap pages.
    """
    if unit.session_position is None:
        return None
    course = unit.module.course

    if is_authenticated_user(user):
        enrollment = (
            CohortEnrollment.objects
            .filter(user=user, cohort__course=course)
            .select_related('cohort')
            .first()
        )
        if (
            enrollment is not None
            and enrollment.cohort.mode == COHORT_MODE_COHORT
            and enrollment.cohort.event_series_id
        ):
            event = Event.objects.select_related(
                'event_series', 'workshop',
            ).filter(
                event_series_id=enrollment.cohort.event_series_id,
                series_position=unit.session_position,
                status__in=PUBLIC_EVENT_STATUSES,
            ).first()
            if event is not None and _can_view_session_event(user, event):
                return event

    today = timezone.now().date()
    fallback_series_ids = (
        Cohort.objects
        .filter(
            course=course,
            mode=COHORT_MODE_COHORT,
            event_series__isnull=False,
            start_date__lt=today,
        )
        .order_by('-start_date')
        .values_list('event_series_id', flat=True)
    )
    for series_id in fallback_series_ids:
        event = Event.objects.select_related(
            'event_series', 'workshop',
        ).filter(
            event_series_id=series_id,
            series_position=unit.session_position,
            status__in=PUBLIC_EVENT_STATUSES,
        ).first()
        if event is not None and _can_view_session_event(user, event):
            return event

    return None


def _can_view_session_event(user, event):
    """Mirror the event page's hidden-series gate for in-course surfaces."""
    series = event.event_series
    return not (series and series.is_hidden) or is_entitled_for_series(user, series)


def build_unit_session_card_context(unit: Unit, user, *, request=None):
    """Build the session-card entry for a ``kind='event'`` unit, or ``None``.

    ``None`` means no ``Event`` resolved anywhere — the template renders
    the clean "not yet scheduled" empty state instead of a card. A resolved
    occurrence is shown only when the viewer may reach its hidden series.
    Recording playback is separately gated by the event and (when linked)
    workshop recording access. Private S3 URLs and Zoom meeting/download
    URLs are never added to this context.
    """
    event = resolve_session_event(unit, user)
    if event is None:
        return None
    is_past = event.is_past
    can_watch_recording = bool(
        is_past and event.has_recording and can_access(user, event)
    )
    workshop = getattr(event, 'workshop', None)
    if can_watch_recording and workshop is not None:
        can_watch_recording = workshop.user_can_access_recording(user)

    recording_playback_url = ''
    if can_watch_recording and event.recording_s3_url and request is not None:
        recording_playback_url = request.build_absolute_uri(
            reverse(
                'event_recording_stream',
                kwargs={'event_id': event.pk, 'slug': event.slug},
            )
        )

    maven_enrolled = False
    if (
        not is_past
        and getattr(user, 'is_authenticated', False)
        and event.event_series_id
    ):
        maven_enrolled = CohortEnrollment.objects.filter(
            user=user,
            cohort__course=unit.module.course,
            cohort__mode=COHORT_MODE_COHORT,
            cohort__event_series_id=event.event_series_id,
        ).exists()

    return {
        'event': event,
        'description_html': _SESSION_INTERNAL_DESCRIPTION_NOTE.sub(
            '', event.description_html or '',
        ),
        'time_display': build_event_time_display(event, user),
        'is_past': is_past,
        'show_recap': bool(is_past and event.recap_is_published),
        'show_recording': bool(is_past and event.has_recording),
        'can_watch_recording': can_watch_recording,
        'recording_playback_url': recording_playback_url,
        'maven_enrolled': maven_enrolled,
        'can_join_now': not is_past and event.can_show_zoom_link(),
        'join_url': event.get_join_url(),
        'recap_url': event.get_recap_url() if is_past and event.recap_is_published else '',
    }


# --- Homework units resolve by cohort at render time (issue #1683) ---
#
# Same reasoning as the event-unit resolution above: ``Homework.cohort``
# is cohort-specific while ``Unit`` is curriculum shared across every
# cohort, so there is no stored FK from ``Unit`` to ``Homework``. Mirrors
# ``resolve_session_event``'s exact cohort-resolution policy (the
# enrolled viewer's own dated cohort first, else the most recent past
# dated cohort) with "has an ``Event`` at this ``session_position``"
# replaced by "has a ``Homework`` row for this unit's ``content_id``".


def resolve_homework_for_unit(unit: Unit, user, *, cohort=None) -> Homework | None:
    """Resolve the ``Homework`` row backing a ``kind='homework'`` unit, or ``None``.

    A ``kind='homework'`` unit with no matching ``Homework`` row (not yet
    authored with ``questions:``, or no cohort resolves) returns ``None``
    -- callers render the unit exactly as before this feature: prose-only
    ``unit.homework_html``, no form, no error.

    Tester-confirmed bug fix (issue #1683 follow-up): the enrolled-viewer
    branch used to require ``mode='cohort'``, mirroring
    ``resolve_session_event`` exactly. That is correct for events (a
    self-paced cohort has no ``event_series``, so there is nothing to look
    up), but wrong for homework -- a self-paced cohort's ``Homework`` row
    is exactly as real as a dated cohort's, and a self-paced-only learner
    resolved nothing, permanently, with no error. Both branches below now
    accept either cohort mode; only the fallback branch's "which PAST
    cohort" ordering still needs ``start_date`` (self-paced cohorts sort
    after every dated one, since they have no date to rank by, but are
    still eligible).
    """
    if unit.kind != UNIT_KIND_HOMEWORK or not unit.content_id:
        return None
    course = unit.module.course

    if cohort is not None:
        if cohort.course_id != course.pk:
            return None
        if not user.is_staff and not CohortEnrollment.objects.filter(
            user=user, cohort=cohort,
        ).exists():
            return None
        return Homework.objects.filter(
            content_id=unit.content_id, cohort=cohort,
        ).select_related('cohort').first()

    if is_authenticated_user(user):
        enrollment = (
            CohortEnrollment.objects
            .filter(user=user, cohort__course=course)
            .select_related('cohort')
            .first()
        )
        if enrollment is not None:
            homework = (
                Homework.objects
                .filter(content_id=unit.content_id, cohort=enrollment.cohort)
                .select_related('cohort')
                .first()
            )
            if homework is not None:
                return homework

    today = timezone.now().date()
    fallback_cohort_ids = (
        Cohort.objects
        .filter(course=course)
        .filter(
            models.Q(mode=COHORT_MODE_COHORT, start_date__isnull=False, start_date__lt=today)
            | models.Q(mode=COHORT_MODE_SELF_PACED)
        )
        .order_by(models.F('start_date').desc(nulls_last=True))
        .values_list('id', flat=True)
    )
    for cohort_id in fallback_cohort_ids:
        homework = (
            Homework.objects
            .filter(content_id=unit.content_id, cohort_id=cohort_id)
            .select_related('cohort')
            .first()
        )
        if homework is not None:
            return homework

    return None


def build_homework_submission_context(user, unit, *, cohort=None):
    """Build homework submission form context for the unit detail page.

    Issue #1683 tranche 1. Returns ``{'homework': None}`` when no
    ``Homework`` row resolves for this unit/viewer -- the caller's template
    then renders the unit exactly as it did before this feature (the
    explicit backward-compatibility contract: an unauthored or
    not-yet-matching homework unit stays prose-only, no form, no error).
    """
    homework = resolve_homework_for_unit(unit, user, cohort=cohort)
    if homework is None:
        return {'homework': None}

    submission = None
    answers_by_question_id = {}
    if is_authenticated_user(user):
        submission = (
            Submission.objects
            .filter(homework=homework, student=user)
            .prefetch_related('answers')
            .first()
        )
        if submission is not None:
            answers_by_question_id = {
                answer.question_id: answer.answer_text or ''
                for answer in submission.answers.all()
            }

    question_views = []
    for question in homework.questions.all():
        raw_answer = answers_by_question_id.get(question.pk, '')
        if question.question_type == QuestionType.CHECKBOXES:
            selected = {v.strip() for v in raw_answer.split(',') if v.strip()}
        else:
            selected = {raw_answer} if raw_answer else set()
        options = [
            {'value': str(i), 'text': text, 'selected': str(i) in selected}
            for i, text in enumerate(question.options_list, start=1)
        ]
        question_views.append({
            'question': question,
            'options': options,
            'text_answer': raw_answer if question.question_type in (
                QuestionType.FREE_FORM, QuestionType.FREE_FORM_LONG,
            ) else '',
        })

    display_timezone = resolve_event_display_timezone(user)

    homework_due_date_display = ''
    if homework.due_date is not None:
        homework_due_date_display = format_event_time_range(
            homework.due_date, None, display_timezone,
        )

    return {
        'homework': homework,
        'homework_questions': question_views,
        'homework_submission': submission,
        'homework_link_value': submission.homework_link if submission else '',
        'homework_is_accepting': homework.is_accepting_submissions,
        'homework_is_self_paced': homework.is_self_paced,
        'homework_due_date_display': homework_due_date_display,
        'homework_display_timezone': display_timezone,
    }
