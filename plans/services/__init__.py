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

from plans.services.accountability import (
    accountability_partners_by_user,
    assign_accountability_partners,
    clear_accountability_for_member,
    randomize_accountability_partners,
    remove_accountability_partners,
)
from plans.services.next_sprint_draft_service import draft_next_sprint_plan

# Re-export the model names the original ``plans/services.py`` carried in
# its namespace so existing patch targets like
# ``plans.services.Deliverable.objects.create`` keep resolving after the
# module became this package.
from plans.services.plan_lifecycle import (
    Checkpoint,
    Deliverable,
    MoveUnfinishedItemsError,
    NextStep,
    Plan,
    SprintEnrollment,
    Week,
    carry_over_unfinished_tasks,
    count_total_unfinished,
    count_unfinished_carry_over_items,
    create_plan_for_enrollment,
    distribute_sprint_feedback,
    eligible_move_target_sprints,
    find_carry_over_source_plan,
    move_unfinished_items_to_sprint,
    unfinished_plan_item_counts,
)
from plans.services.plan_ready_emails import (
    preview_plan_ready_emails,
    send_plan_ready_email_for_plan,
    send_plan_ready_emails,
)
from plans.services.progress import annotate_plan_progress

__all__ = [
    'Checkpoint',
    'Deliverable',
    'MoveUnfinishedItemsError',
    'NextStep',
    'Plan',
    'SprintEnrollment',
    'Week',
    'annotate_plan_progress',
    'accountability_partners_by_user',
    'assign_accountability_partners',
    'carry_over_unfinished_tasks',
    'clear_accountability_for_member',
    'count_total_unfinished',
    'count_unfinished_carry_over_items',
    'create_plan_for_enrollment',
    'distribute_sprint_feedback',
    'draft_next_sprint_plan',
    'eligible_move_target_sprints',
    'find_carry_over_source_plan',
    'move_unfinished_items_to_sprint',
    'preview_plan_ready_emails',
    'randomize_accountability_partners',
    'remove_accountability_partners',
    'send_plan_ready_email_for_plan',
    'send_plan_ready_emails',
    'unfinished_plan_item_counts',
]
