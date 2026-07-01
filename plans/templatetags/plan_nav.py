"""Template tags for sprint-related shortcuts in the public header."""

from __future__ import annotations

from dataclasses import dataclass

from django import template
from django.urls import reverse
from django.utils import timezone

from accounts.gating import is_newsletter_only_user
from plans.models import Plan, SprintEnrollment

register = template.Library()

VISIBLE_MEMBER_SPRINT_STATUSES = ('active', 'completed')


@dataclass(frozen=True)
class HeaderSprintShortcut:
    """Resolved member-facing sprint shortcut for the account menu."""

    label: str
    href: str


def _plan_url(plan):
    return reverse(
        'my_plan_detail',
        kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
    )


def _cohort_board_url(sprint):
    return reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})


def _sprint_detail_url(sprint):
    return reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})


def _is_current(sprint, today):
    return sprint.start_date <= today <= sprint.end_date


def _is_upcoming(sprint, today):
    return today < sprint.start_date


def _created_timestamp(obj):
    value = getattr(obj, 'created_at', None)
    return value.timestamp() if value is not None else 0


def _enrolled_timestamp(enrollment):
    value = getattr(enrollment, 'enrolled_at', None)
    return value.timestamp() if value is not None else 0


def resolve_header_sprint_shortcut(user, *, today=None):
    """Return the current/next sprint shortcut for ``user`` or ``None``.

    The global header is a member-facing surface, so it points only to the
    signed-in user's own current or upcoming work. Ended-only history is left
    to the Community Sprints archive.
    """
    if user is None or not user.is_authenticated:
        return None
    if is_newsletter_only_user(user):
        return None

    if today is None:
        today = timezone.localdate()

    plans = list(
        Plan.objects
        .filter(
            member=user,
            sprint__status__in=VISIBLE_MEMBER_SPRINT_STATUSES,
        )
        .select_related('sprint')
    )

    current_plans = [
        plan for plan in plans if _is_current(plan.sprint, today)
    ]
    if current_plans:
        plan = max(
            current_plans,
            key=lambda item: (
                item.sprint.start_date,
                _created_timestamp(item),
                item.pk,
            ),
        )
        return HeaderSprintShortcut(label='Plan', href=_plan_url(plan))

    upcoming_plans = [
        plan for plan in plans if _is_upcoming(plan.sprint, today)
    ]
    if upcoming_plans:
        plan = min(
            upcoming_plans,
            key=lambda item: (
                item.sprint.start_date,
                -_created_timestamp(item),
                -item.pk,
            ),
        )
        return HeaderSprintShortcut(label='Plan', href=_plan_url(plan))

    enrollments = list(
        SprintEnrollment.objects
        .filter(
            user=user,
            sprint__status__in=VISIBLE_MEMBER_SPRINT_STATUSES,
        )
        .select_related('sprint')
    )

    current_enrollments = [
        enrollment
        for enrollment in enrollments
        if _is_current(enrollment.sprint, today)
    ]
    if current_enrollments:
        enrollment = max(
            current_enrollments,
            key=lambda item: (
                item.sprint.start_date,
                _enrolled_timestamp(item),
                item.pk,
            ),
        )
        return HeaderSprintShortcut(
            label='Cohort',
            href=_cohort_board_url(enrollment.sprint),
        )

    upcoming_enrollments = [
        enrollment
        for enrollment in enrollments
        if _is_upcoming(enrollment.sprint, today)
    ]
    if upcoming_enrollments:
        enrollment = min(
            upcoming_enrollments,
            key=lambda item: (
                item.sprint.start_date,
                -_enrolled_timestamp(item),
                -item.pk,
            ),
        )
        return HeaderSprintShortcut(
            label='Sprint',
            href=_sprint_detail_url(enrollment.sprint),
        )

    return None


@register.simple_tag(takes_context=True)
def current_user_sprint_shortcut(context):
    """Return the header sprint shortcut for the current request user."""
    request = context.get('request')
    if request is None:
        return None
    user = getattr(request, 'user', None)
    return resolve_header_sprint_shortcut(user)
