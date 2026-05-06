"""Shared helper for the "Your sprint plan" dashboard card (issue #442).

Two member-facing surfaces render the same sprint-plan card: the Account
page (``/account/``) and the authenticated home dashboard (``/`` rendered
from ``content/dashboard.html``). Both surfaces need the same context
keys with identical semantics, so we centralise the lookup here.

The visibility literal ``'cohort'`` lives in this module (and at the
queryset boundary) rather than in the view bodies. The regression test
in ``plans/tests/test_view_layer_no_visibility_literals.py`` is scoped
to ``plans/views/cohort.py`` only -- the literal here is the
single-presence-probe exception documented in #440.
"""

from django.db.models import Count, Q

from plans.models import Plan


def build_sprint_plan_card_context(user):
    """Return the four context keys for the "Your sprint plan" card.

    Returns a dict with keys:

    - ``plan``: the user's most recently created :class:`Plan`, with
      ``progress_total`` / ``progress_done`` annotations, or ``None``.
    - ``plan_progress_total``: total checkpoint count on the plan
      (``0`` when there is no plan).
    - ``plan_progress_done``: completed checkpoint count
      (``0`` when there is no plan).
    - ``cohort_has_other_shared_plans``: ``True`` iff at least one OTHER
      plan in the same sprint has cohort visibility. Used to gate the
      "View cohort" CTA so we never link to a board that would render
      empty for the viewer.

    Anonymous / unauthenticated callers receive an all-empty payload
    (``plan`` is ``None``); both calling templates omit the card when
    ``plan`` is falsy.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return {
            'plan': None,
            'plan_progress_total': 0,
            'plan_progress_done': 0,
            'cohort_has_other_shared_plans': False,
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
            'cohort_has_other_shared_plans': False,
        }

    cohort_has_other_shared_plans = (
        Plan.objects
        .filter(sprint=plan.sprint, visibility='cohort')
        .exclude(member=user)
        .exists()
    )

    return {
        'plan': plan,
        'plan_progress_total': plan.progress_total,
        'plan_progress_done': plan.progress_done,
        'cohort_has_other_shared_plans': cohort_has_other_shared_plans,
    }
