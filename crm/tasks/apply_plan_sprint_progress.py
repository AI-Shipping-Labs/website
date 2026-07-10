"""Parse + auto-apply `#plan-sprints` progress to plan items (issue #890, Phase 2).

The ORM-aware caller for the Django-independent parse callable in
``crm/services/plan_sprint_parse.py``. For one captured :class:`SlackThread`
matched to a member + active-sprint plan, this:

1. Skips entirely (no LLM call, no mutation) when the thread has gained no
   new messages since its last apply — the ``source_message_ts`` watermark.
2. Assembles the plain parse input from the thread messages + the member's
   current plan items (each with a stable id).
3. Calls the parse callable, drops any hallucinated id not on the plan.
4. For each parsed completion whose item is still ``done_at IS NULL``, sets
   ``done_at = now()`` and records one :class:`AppliedProgressChange` with
   ``previous_done_at = None``. Items already done are skipped and never
   recorded (so an undo can never un-complete a human/earlier completion).
5. Upserts the single :class:`IngestedProgressEvent` for the thread
   (``update_or_create(thread=...)``), storing summary/blockers/watermark.

The LLM call is kept OUT of the apply transaction; only the apply runs in a
transaction. A per-thread failure is the caller's concern to log without
failing the whole run.
"""

import logging

from django.db import transaction
from django.utils import timezone

from crm.models import AppliedProgressChange, IngestedProgressEvent
from crm.services.plan_sprint_parse import (
    PlanSprintParseInput,
    PlanSprintParseUnavailable,
    _PlanItem,
    parse_plan_sprint_thread,
)
from integrations.config import get_config
from integrations.services.llm import LLMError
from plans.models import Checkpoint, Deliverable, NextStep

logger = logging.getLogger(__name__)

ITEM_KIND_CHECKPOINT = 'checkpoint'
ITEM_KIND_DELIVERABLE = 'deliverable'
ITEM_KIND_NEXT_STEP = 'next_step'


def _collect_plan_items(plan):
    """Return ``{(kind, id): item}`` for every Checkpoint/Deliverable/NextStep.

    Checkpoints span all weeks of the plan; deliverables and next steps are
    plan-level. The (kind, id) key is the stable identifier the model echoes.
    """
    items = {}
    checkpoints = Checkpoint.objects.filter(week__plan=plan)
    for cp in checkpoints:
        items[(ITEM_KIND_CHECKPOINT, cp.id)] = cp
    for d in Deliverable.objects.filter(plan=plan):
        items[(ITEM_KIND_DELIVERABLE, d.id)] = d
    for n in NextStep.objects.filter(plan=plan):
        items[(ITEM_KIND_NEXT_STEP, n.id)] = n
    return items


def _build_parse_input(thread, plan, plan_items, messages):
    """Map the thread + plan items onto the ORM-free parse input."""
    member_name = ''
    if thread.member_id is not None and thread.member is not None:
        member_name = (
            thread.member.get_full_name() or thread.member.get_username()
        )
    return PlanSprintParseInput(
        member_name=member_name,
        plan_goal=plan.goal or '',
        messages=[
            (
                m.author_display or m.slack_user_id or 'Unknown',
                m.posted_at.isoformat() if m.posted_at else '',
                m.text or '',
            )
            for m in messages
        ],
        plan_items=[
            _PlanItem(
                item_kind=kind,
                item_id=item.id,
                description=item.description,
                already_done=item.done_at is not None,
            )
            for (kind, _id), item in plan_items.items()
        ],
    )


def _latest_message_ts(messages):
    """The lexically-largest ts among the thread messages (Slack ts compare)."""
    latest = ''
    for m in messages:
        if m.ts and m.ts > latest:
            latest = m.ts
    return latest


def apply_thread_progress(thread, *, ingest=None):
    """Parse + auto-apply progress for one matched thread. Returns the event or None.

    Skips (returns None, no LLM call) when:
    - the thread has no member/plan, or the plan's sprint is not active;
    - the thread has gained no new messages since the last apply (watermark).

    Raises:
        PlanSprintParseUnavailable: when the LLM is disabled. The caller
            treats this as "skip the whole parse step" (not a run failure).
        LLMError: on a parse/LLM failure. The caller logs it per-thread and
            keeps going.
    """
    if thread.member_id is None or thread.plan_id is None:
        return None
    plan = thread.plan
    sprint = plan.sprint
    if sprint is None or sprint.status != 'active':
        return None

    messages = list(thread.messages.all())
    if not messages:
        return None
    latest_ts = _latest_message_ts(messages)

    # Watermark guard: no new messages since the last apply -> no LLM call,
    # no mutation. This is the daily-rerun idempotency guard.
    existing = IngestedProgressEvent.objects.filter(thread=thread).first()
    if existing is not None and existing.source_message_ts == latest_ts:
        return existing

    plan_items = _collect_plan_items(plan)

    parse_input = _build_parse_input(thread, plan, plan_items, messages)
    # LLM call OUTSIDE any transaction.
    parsed = parse_plan_sprint_thread(parse_input)

    model_name = get_config('LLM_MODEL', 'claude-sonnet-4-5')
    blockers = list(parsed.blockers or [])

    new_changes = []
    with transaction.atomic():
        event, _created = IngestedProgressEvent.objects.update_or_create(
            thread=thread,
            defaults={
                'plan': plan,
                'ingest': ingest,
                'summary': parsed.summary or '',
                'blockers': blockers,
                'model_name': model_name,
                'source_message_ts': latest_ts,
            },
        )

        now = timezone.now()
        # Dedupe completions by (kind, id) so a thread that lists an item
        # twice does not attempt two flips.
        seen = set()
        for completion in parsed.completed_items:
            key = (completion.item_kind, completion.item_id)
            if key in seen:
                continue
            seen.add(key)
            # Drop hallucinated ids: only items on this member's plan apply.
            item = plan_items.get(key)
            if item is None:
                continue
            # Already done (human or earlier ingest) -> skip, record nothing.
            if item.done_at is not None:
                continue
            item.done_at = now
            item.save(update_fields=['done_at'])
            change = AppliedProgressChange(
                event=event,
                item_kind=completion.item_kind,
                previous_done_at=None,
            )
            if completion.item_kind == ITEM_KIND_CHECKPOINT:
                change.checkpoint = item
            elif completion.item_kind == ITEM_KIND_DELIVERABLE:
                change.deliverable = item
            elif completion.item_kind == ITEM_KIND_NEXT_STEP:
                change.next_step = item
            change.save()
            new_changes.append(change)

    if new_changes:
        from plans.services.sprint_cadence import (  # noqa: PLC0415
            create_slack_progress_delivery,
        )

        create_slack_progress_delivery(event, new_changes)

    return event


def _restore_change(change):
    """Restore one change's plan item ``done_at`` to its ``previous_done_at``.

    No-ops when the item was hard-deleted (FK now null). Does NOT delete the
    change row — the caller decides that.
    """
    item = change.item
    if item is None:
        return
    item.done_at = change.previous_done_at
    item.save(update_fields=['done_at'])


def reverse_event(event):
    """Undo a whole auto-apply: restore every change, then delete the event.

    Restores each :class:`AppliedProgressChange`'s item to its
    ``previous_done_at`` (always None for recorded changes) BEFORE deleting
    the event (which cascades the change rows). Items the event never
    recorded — including manual completions — are untouched.
    """
    with transaction.atomic():
        for change in event.changes.all():
            _restore_change(change)
        event.delete()


def reverse_change(change):
    """Undo a single change: restore its item, then delete only that row.

    Leaves the rest of the event's changes applied and the event row in
    place (it still carries the summary/blockers context).
    """
    with transaction.atomic():
        _restore_change(change)
        change.delete()


def apply_progress_for_threads(threads, *, ingest=None):
    """Parse + apply across threads. Returns (events_created, errors).

    Skips the entire step when the LLM is disabled (the first
    :class:`PlanSprintParseUnavailable` short-circuits — there is no point
    trying every thread). A per-thread :class:`LLMError` is logged and does
    not block the remaining threads.
    """
    events = 0
    errors = 0
    for thread in threads:
        try:
            event = apply_thread_progress(thread, ingest=ingest)
        except PlanSprintParseUnavailable:
            logger.info(
                'plan-sprints parse skipped: LLM not configured',
            )
            return events, errors
        except LLMError:
            errors += 1
            logger.exception(
                'plan-sprints parse failed for thread %s', thread.pk,
            )
            continue
        if event is not None:
            events += 1
    return events, errors
