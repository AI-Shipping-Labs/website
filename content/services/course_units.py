"""Course unit access and page-context helpers.

The course unit detail view has a few policy branches that need to stay
stable: preview units are public, legacy free-course units still nudge
anonymous visitors to sign up, registered-walled units require verified
accounts, paid units require tier access, and cohort drip locks apply
after tier access has already been granted.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from urllib.parse import urlencode

from django.utils import timezone
from django.utils.html import strip_tags

from content.access import (
    LEVEL_MAIN,
    LEVEL_OPEN,
    build_gating_context,
    can_access,
    get_gated_reason,
    get_required_tier_name,
    get_user_level,
)
from content.models import CohortEnrollment, Unit, UserCourseProgress
from content.templatetags.video_utils import get_video_thumbnail_url
from content.utils.teaser import first_sentence, truncate_to_words

TEASER_WORD_LIMIT = 150

ACCESS_GRANTED = 'access_granted'
ACCESS_GRANTED_PREVIEW = 'preview'
ACCESS_DENIED_LEGACY_SIGNIN = 'legacy_anonymous_signin_required'
ACCESS_DENIED_AUTHENTICATION = 'authentication_required'
ACCESS_DENIED_INSUFFICIENT_TIER = 'insufficient_tier'
ACCESS_DENIED_UNVERIFIED_EMAIL = 'unverified_email'


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
        not getattr(user, 'is_authenticated', False)
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


def decide_course_unit_drip_lock(
    user,
    unit: Unit,
    *,
    today: datetime.date | None = None,
) -> CourseUnitDripDecision:
    """Return whether cohort drip scheduling currently locks ``unit``."""
    if (
        not getattr(user, 'is_authenticated', False)
        or unit.available_after_days is None
    ):
        return CourseUnitDripDecision(is_locked=False)

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
    if enrollment is None:
        return CourseUnitDripDecision(is_locked=False)

    available_date = enrollment.cohort.start_date + datetime.timedelta(
        days=unit.available_after_days,
    )
    today = today or timezone.now().date()
    if today < available_date:
        return CourseUnitDripDecision(
            is_locked=True,
            available_date=available_date,
        )
    return CourseUnitDripDecision(is_locked=False, available_date=available_date)


def build_gated_course_unit_context(user, course, module, unit, decision):
    """Build template context for a denied course-unit request."""
    gating = build_gating_context(user, unit, 'unit')
    is_unverified_gate = decision.gated_reason == ACCESS_DENIED_UNVERIFIED_EMAIL
    is_auth_required_gate = decision.gated_reason == ACCESS_DENIED_AUTHENTICATION

    if is_auth_required_gate:
        unit_url = unit.get_absolute_url()
        login_qs = urlencode({'next': unit_url})
        tier_name = None
        cta_message = 'Sign in to read this lesson'
        pricing_url = f'/accounts/login/?{login_qs}'
        cta_label = 'Sign In'
        cta_description = (
            'This lesson is free with a sign-in. Create a free '
            'account in seconds to keep reading.'
        )
    elif not getattr(user, 'is_authenticated', False):
        if course.required_level == 0:
            cta_message = 'Sign in to access this lesson'
            tier_name = None
            pricing_url = (
                f'/accounts/signup/?'
                f'{urlencode({"next": unit.get_absolute_url()})}'
            )
            cta_label = 'Sign Up'
            cta_description = 'Create a free account to access this course.'
        else:
            cta_message = 'Sign in to access this lesson'
            tier_name = get_required_tier_name(course.required_level)
            pricing_url = (
                f'/accounts/login/?'
                f'{urlencode({"next": unit.get_absolute_url()})}'
            )
            cta_label = 'View Pricing'
            cta_description = (
                'Get full access to this course and more with a membership.'
            )
    else:
        tier_name = get_required_tier_name(course.required_level)
        if is_unverified_gate:
            cta_message = ''
            pricing_url = '/pricing'
            cta_label = ''
            cta_description = ''
        else:
            cta_message = f'Upgrade to {tier_name} to access this lesson'
            pricing_url = '/pricing'
            cta_label = 'View Pricing'
            cta_description = (
                'Get full access to this course and more with a membership.'
            )

    teaser_body_html = None
    if unit.body_html:
        teaser_body_html = truncate_to_words(unit.body_html, TEASER_WORD_LIMIT)

    homework_teaser = ''
    if unit.homework_html:
        homework_text = strip_tags(unit.homework_html).strip()
        homework_teaser = first_sentence(homework_text)

    signup_cta_url = ''
    signup_cta_label = ''
    if not getattr(user, 'is_authenticated', False) and (
        course.required_level > 0 or is_auth_required_gate
    ):
        unit_url = unit.get_absolute_url()
        signup_qs = urlencode({'next': unit_url})
        signup_cta_url = f'/accounts/signup/?{signup_qs}'
        signup_cta_label = (
            'Create a free account'
            if is_auth_required_gate
            else 'Sign in or create a free account'
        )

    context = {
        'course': course,
        'module': module,
        'unit': unit,
        'is_gated': True,
        'required_tier_name': tier_name,
        'cta_message': cta_message,
        'pricing_url': pricing_url,
        'cta_label': cta_label,
        'cta_description': cta_description,
        'teaser_body_html': teaser_body_html,
        'homework_teaser': homework_teaser,
        'video_thumbnail_url': get_video_thumbnail_url(unit.video_url),
        'has_video': bool(unit.video_url),
        'signup_cta_url': signup_cta_url,
        'signup_cta_label': signup_cta_label,
        'user_authenticated': getattr(user, 'is_authenticated', False),
        'current_user_state': _current_access_state(user, course.required_level),
        'gated_card_testid': 'teaser-cta',
        'gated_icon': 'lock',
        'gated_heading': cta_message,
        'gated_description': cta_description,
        'gated_cta_url': pricing_url,
        'gated_cta_label': cta_label,
        'gated_cta_testid': 'teaser-upgrade-cta',
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


def build_course_unit_navigation_context(user, course, module, unit):
    """Build navigation, completion, discussion, and mobile progress context."""
    modules = course.get_syllabus()

    completed_unit_ids = set()
    is_completed = False
    if getattr(user, 'is_authenticated', False):
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

    flat_units = []
    for nav_module in modules:
        for nav_unit in nav_module.units.all():
            flat_units.append(nav_unit.pk)
    reader_progress_total = len(flat_units)
    try:
        reader_progress_current = flat_units.index(unit.pk) + 1
    except ValueError:
        reader_progress_current = 1

    return {
        'course': course,
        'module': module,
        'unit': unit,
        'modules': modules,
        'is_gated': False,
        'has_access': True,
        'completed_unit_ids': completed_unit_ids,
        'is_completed': is_completed,
        'next_unit': next_unit,
        'prev_unit': prev_unit,
        'user_authenticated': getattr(user, 'is_authenticated', False),
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
        'reader_progress_completed': len(completed_unit_ids),
    }


def get_all_units_ordered(course):
    """Return all units in course reading order."""
    return list(
        Unit.objects.filter(module__course=course)
        .select_related('module')
        .order_by('module__sort_order', 'sort_order')
    )


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


def _current_access_state(user, required_level):
    """Return signed-in user access copy for gated cards only."""
    if not getattr(user, 'is_authenticated', False):
        return ''
    user_level = get_user_level(user)
    if user_level >= required_level:
        return ''
    return f'Current access: {get_required_tier_name(user_level)} member'
