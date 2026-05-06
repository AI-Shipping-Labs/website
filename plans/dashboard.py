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

from django.db.models import Count, Q

from plans.models import Plan, SprintEnrollment


def build_sprint_plan_card_context(user):
    """Return the four context keys for the "Your sprint plan" card.

    Returns a dict with keys:

    - ``plan``: the user's most recently created :class:`Plan`, with
      ``progress_total`` / ``progress_done`` annotations, or ``None``.
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
    if user is None or not getattr(user, 'is_authenticated', False):
        return {
            'plan': None,
            'plan_progress_total': 0,
            'plan_progress_done': 0,
            'cohort_has_other_members': False,
        }

    plan = (
        Plan.objects
        .filter(member=user)
        .select_related('sprint')
        .annotate(
            progress_total=Count('weeks__checkpoints', distinct=True),
            progress_done=Count(
                'weeks__checkpoints',
                filter=Q(weeks__checkpoints__done_at__isnull=False),
                distinct=True,
            ),
        )
        .order_by('-created_at')
        .first()
    )

    if plan is None:
        return {
            'plan': None,
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
        'plan_progress_total': plan.progress_total,
        'plan_progress_done': plan.progress_done,
        'cohort_has_other_members': cohort_has_other_members,
    }
