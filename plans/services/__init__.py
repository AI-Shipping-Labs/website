"""Service helpers for the plans app.

This package re-exports the plan-lifecycle helpers (originally a single
``plans/services.py`` module — see :mod:`plans.services.plan_lifecycle`)
so existing ``from plans.services import X`` imports keep working, and
adds the Phase-3 next-sprint draft orchestration
(:func:`draft_next_sprint_plan`).

The pure, Django-independent LLM draft callable lives in
:mod:`plans.services.next_sprint_draft`; its import-isolation test inspects
that module's own source to confirm it never reaches Django ORM models. It
is safe for the Django-dependent orchestration helper (and this package)
to import the callable — the seam is about what the callable itself
imports, not about who imports it.
"""

from plans.services.next_sprint_draft_service import draft_next_sprint_plan

# Re-export the model names the original ``plans/services.py`` carried in
# its namespace so existing patch targets like
# ``plans.services.Deliverable.objects.create`` keep resolving after the
# module became this package.
from plans.services.plan_lifecycle import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    SprintEnrollment,
    Week,
    carry_over_unfinished_tasks,
    count_total_unfinished,
    count_unfinished_carry_over_items,
    create_plan_for_enrollment,
    distribute_sprint_feedback,
    find_carry_over_source_plan,
)
from plans.services.progress import annotate_plan_progress

__all__ = [
    'Checkpoint',
    'Deliverable',
    'NextStep',
    'Plan',
    'SprintEnrollment',
    'Week',
    'annotate_plan_progress',
    'carry_over_unfinished_tasks',
    'count_total_unfinished',
    'count_unfinished_carry_over_items',
    'create_plan_for_enrollment',
    'distribute_sprint_feedback',
    'draft_next_sprint_plan',
    'find_carry_over_source_plan',
]
