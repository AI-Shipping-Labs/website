"""Shared helper for the "Your sprint plan" dashboard card (issue #442).

Two member-facing surfaces render the same sprint-plan card: the Account
page (``/account/``) and the authenticated home dashboard (``/`` rendered
from ``content/dashboard.html``). Both surfaces need the same context
keys with identical semantics, so we centralise the lookup here.

Issue #461 widens the gate that controls the "View cohort" CTA. The
previous flag (``cohort_has_other_shared_plans``) only fired when at
least one OTHER plan in the sprint had ``visibility='cohort'``. With
the cohort progress board now rendering every enrolled member's progress
(including private-plan members as counts-only rows and no-plan members
as "No plan yet" stubs), the board is useful as soon as the sprint has
ANY other enrolled member -- so the renamed flag
``cohort_has_other_members`` checks ``SprintEnrollment`` rather than
plan visibility.
"""

from django.urls import reverse
from django.utils import timezone

from accounts.utils.user_checks import is_authenticated_user
from plans.models import Plan, Sprint, SprintEnrollment
from plans.services import annotate_plan_progress


def _created_timestamp(obj):
    value = getattr(obj, 'created_at', None)
    return value.timestamp() if value is not None else 0


def _is_current(sprint, today):
    return sprint.start_date <= today <= sprint.end_date


def _is_upcoming(sprint, today):
    return today < sprint.start_date


def _select_dashboard_shared_plan(plans, *, today=None):
    shared_plans = [
        plan for plan in plans if plan.shared_at is not None
    ]
    if not shared_plans:
        return None

    if today is None:
        today = timezone.localdate()

    current_plans = [
        plan for plan in shared_plans if _is_current(plan.sprint, today)
    ]
    if current_plans:
        return max(
            current_plans,
            key=lambda item: (
                item.sprint.start_date,
                _created_timestamp(item),
                item.pk,
            ),
        )

    upcoming_plans = [
        plan for plan in shared_plans if _is_upcoming(plan.sprint, today)
    ]
    if upcoming_plans:
        return min(
            upcoming_plans,
            key=lambda item: (
                item.sprint.start_date,
                -_created_timestamp(item),
                -item.pk,
            ),
        )

    return max(
        shared_plans,
        key=lambda item: (_created_timestamp(item), item.pk),
    )


def build_sprint_plan_card_context(user):
    """Return context keys for the "Your sprint plan" dashboard card.

    Returns a dict with keys:

    - ``plan``: the user's selected shared :class:`Plan`, with
      ``progress_total`` / ``progress_done`` annotations, or ``None``.
      Current shared plans beat upcoming and ended shared plans; unshared
      plans are staff drafts and must not power member CTAs.
    - ``has_any_plan``: whether any plan row exists for the member, including
      unshared staff drafts.
    - ``plan_progress_total``: total checkpoint count on the plan
      (``0`` when there is no plan).
    - ``plan_progress_done``: completed checkpoint count
      (``0`` when there is no plan).
    - ``cohort_has_other_members``: ``True`` iff the plan's sprint has
      at least one OTHER enrolled member (with or without a plan, with
      any visibility). Used to gate the "View cohort" CTA so the card
      surfaces it only when the cohort board would render at least one
      other row.

    Anonymous / unauthenticated callers receive an all-empty payload
    (``plan`` is ``None``); both calling templates omit the card when
    ``plan`` is falsy.
    """
    if not is_authenticated_user(user):
        return {
            'plan': None,
            'has_any_plan': False,
            'plan_progress_total': 0,
            'plan_progress_done': 0,
            'cohort_has_other_members': False,
        }

    plans = list(
        annotate_plan_progress(Plan.objects.filter(member=user))
        .select_related('sprint')
    )
    has_any_plan = bool(plans)
    plan = _select_dashboard_shared_plan(plans)

    if plan is None:
        return {
            'plan': None,
            'has_any_plan': has_any_plan,
            'plan_progress_total': 0,
            'plan_progress_done': 0,
            'cohort_has_other_members': False,
        }

    cohort_has_other_members = (
        SprintEnrollment.objects
        .filter(sprint=plan.sprint)
        .exclude(user=user)
        .exists()
    )

    return {
        'plan': plan,
        'has_any_plan': has_any_plan,
        'plan_progress_total': plan.progress_total,
        'plan_progress_done': plan.progress_done,
        'cohort_has_other_members': cohort_has_other_members,
    }


def build_active_sprint_opportunities_context(
    user,
    user_level,
    plan=None,
    has_any_plan=None,
):
    """Return active sprint/cohort opportunities for the dashboard.

    The dashboard is a discovery surface, not a sprint catalog, so this
    returns at most two active sprints the user can access. Enrolled users
    without a plan go straight to the cohort board; otherwise the CTA points
    at the existing public sprint detail route. Unshared member plans are
    still staff drafts, so their sprints stay hidden from the member-facing
    opportunity list until a shared plan exists. Broader discovery links use
    the shipped ``/activities`` page.
    """
    if not is_authenticated_user(user):
        return {
            'active_sprint_opportunities': [],
            'active_sprint_discovery_url': '/activities',
        }

    current_plan_sprint_id = plan.sprint_id if plan is not None else None
    if has_any_plan is False:
        blocked_sprint_ids = set()
    else:
        blocked_sprint_ids = set(
            Plan.objects
            .filter(member=user, shared_at__isnull=True)
            .values_list('sprint_id', flat=True),
        )
    if current_plan_sprint_id is not None:
        blocked_sprint_ids.add(current_plan_sprint_id)
    enrollments = list(
        SprintEnrollment.objects
        .filter(user=user, sprint__status='active')
        .select_related('sprint')
        .order_by('sprint__start_date')
    )
    enrolled_sprint_ids = {enrollment.sprint_id for enrollment in enrollments}

    opportunities = []
    for enrollment in enrollments:
        sprint = enrollment.sprint
        if sprint.id in blocked_sprint_ids:
            continue
        opportunities.append({
            'sprint': sprint,
            'url': reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
            'cta_label': 'View cohort',
            'enrolled': True,
        })

    remaining_slots = max(0, 2 - len(opportunities))
    if remaining_slots:
        joinable_sprints = (
            Sprint.objects
            .filter(status='active', min_tier_level__lte=user_level)
            .exclude(id__in=enrolled_sprint_ids | blocked_sprint_ids)
            .order_by('start_date')[:remaining_slots]
        )
        for sprint in joinable_sprints:
            opportunities.append({
                'sprint': sprint,
                'url': reverse(
                    'sprint_detail',
                    kwargs={'sprint_slug': sprint.slug},
                ),
                'cta_label': 'View sprint',
                'enrolled': False,
            })

    return {
        'active_sprint_opportunities': opportunities[:2],
        'active_sprint_discovery_url': '/activities',
    }
