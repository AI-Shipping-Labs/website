"""Shared next-sprint draft orchestration (issue #891, Phase 3).

:func:`draft_next_sprint_plan` is the ONE code path behind both the Studio
"Draft next sprint plan" button and the
``POST /api/plans/<id>/draft-next-sprint`` endpoint. It composes two
existing primitives without reinventing them:

- Carry-over of unfinished tasks: ``find_carry_over_source_plan`` +
  ``carry_over_unfinished_tasks`` (issue #808) — copies real plan rows.
- An LLM draft of the next-sprint narrative: the pure, Django-independent
  :func:`plans.services.next_sprint_draft.draft_next_sprint` callable,
  whose result is held aside in a :class:`plans.models.NextSprintPlanDraft`
  row (NEVER written into the plan's live fields).

Graceful degradation is the contract:

- LLM off: carry-over still runs; no ``NextSprintPlanDraft`` is written;
  the result reports the draft was skipped.
- LLM failure (any ``LLMError``): the carry-over result stands (it already
  committed); no partial ``NextSprintPlanDraft`` is written.
- No source plan: carry-over is skipped silently; an LLM draft (if on) is
  still produced from the destination plan's current state only.
- Empty recent updates: the draft leans on plan state only.

This module IS Django-dependent — it does the ORM reads and persists the
draft row. The pure LLM logic stays in ``next_sprint_draft`` so it can be
driven against a real provider without the request layer. The callable is
imported lazily so importing ``plans.services`` never breaks the callable's
import-isolation seam.
"""

import logging

from django.utils import timezone

from crm.services.member_profile import build_member_profile_context
from crm.services.slack_updates import threads_for_member, threads_for_plan
from integrations.config import get_config
from integrations.services import llm
from integrations.services.llm import LLMError
from plans.models import NextSprintPlanDraft
from plans.services.next_sprint_draft import (
    NextSprintDraftInput,
    OnboardingAnswer,
    RecentActivity,
    RecentUpdate,
    draft_next_sprint,
)
from plans.services.plan_lifecycle import (
    carry_over_unfinished_tasks,
    find_carry_over_source_plan,
)

logger = logging.getLogger(__name__)

# IntegrationSetting key (default ON) gating profile injection into the
# draft. When off, the draft input carries no profile fields and the
# rendered user message has no member-profile block (pre-#913 behaviour).
PROFILE_INJECTION_KEY = 'NEXT_SPRINT_DRAFT_USE_PROFILE'


def _profile_injection_enabled():
    """True when the member profile should be fed into the draft.

    Defaults ON when unset (mirrors ``ai_onboarding_available`` and the
    ``ONBOARDING_AI_ENABLED`` flag): the key exists to turn profile
    injection OFF without a redeploy, so only an explicit falsey value
    disables it. Read via ``get_config`` from the IntegrationSetting
    framework — never raw ``os.environ`` / ``settings.X``.
    """
    raw = get_config(PROFILE_INJECTION_KEY, 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')


def _split_done(items):
    """Return ``(done_descriptions, not_done_descriptions)`` for ``items``."""
    done = []
    not_done = []
    for item in items:
        bucket = done if item.done_at is not None else not_done
        bucket.append(item.description)
    return done, not_done


def _collect_recent_updates(threads, *, max_threads=5):
    """Flatten captured threads into ``RecentUpdate`` rows, newest first.

    ``threads`` is a queryset/iterable of ``SlackThread`` ordered newest
    first (as returned by the Phase-1 selectors). For each thread we emit
    its messages (root + replies) in chronological order. Returns the list
    of :class:`RecentUpdate` and the total message count (provenance).
    """
    updates = []
    for thread in list(threads)[:max_threads]:
        for message in thread.messages.all():
            updates.append(
                RecentUpdate(
                    author_display=message.author_display,
                    posted_at=(
                        message.posted_at.isoformat()
                        if message.posted_at else ''
                    ),
                    text=message.text,
                )
            )
    return updates, len(updates)


def _build_profile_fields(member):
    """Return the member-profile fields for the draft input.

    Reuses the #883 ``build_member_profile_context`` rather than re-querying
    onboarding/CRM, and maps its dict onto the plain (ORM-free) fields the
    pure callable expects. Only ANSWERED onboarding rows are passed so the
    prompt stays tight. Gated by the ``NEXT_SPRINT_DRAFT_USE_PROFILE``
    IntegrationSetting key (default ON); when off, returns empty fields so
    no profile block is rendered.

    A member with no onboarding response and/or no CRM record yields empty
    fields — ``build_member_profile_context`` always returns a fully
    populated dict with empty markers, so no extra guarding is needed.
    """
    if not _profile_injection_enabled():
        return {
            'persona': '',
            'crm_summary': '',
            'crm_next_steps': '',
            'onboarding_answers': [],
            'recent_activity': [],
        }

    context = build_member_profile_context(member)
    onboarding_answers = [
        OnboardingAnswer(prompt=row['prompt'], answer=row['display'])
        for row in context['onboarding_answers']
        if row['answered']
    ]
    recent_activity = [
        RecentActivity(
            occurred_at=row['occurred_at'].date().isoformat(),
            category=row['category_label'],
            type_label=row['type_label'],
            label=row['label'],
        )
        for row in context['recent_activity']
    ]
    return {
        'persona': context['persona'],
        'crm_summary': context['summary'],
        'crm_next_steps': context['next_steps'],
        'onboarding_answers': onboarding_answers,
        'recent_activity': recent_activity,
    }


def _build_draft_input(*, destination_plan, source_plan, recent_updates):
    """Assemble the plain ``NextSprintDraftInput`` from ORM reads."""
    checkpoints = []
    for week in destination_plan.weeks.all():
        checkpoints.extend(week.checkpoints.all())
    done_cp, not_done_cp = _split_done(checkpoints)
    done_del, not_done_del = _split_done(destination_plan.deliverables.all())
    done_ns, not_done_ns = _split_done(destination_plan.next_steps.all())

    current_sprint_name = (
        source_plan.sprint.name if source_plan is not None else ''
    )

    profile_fields = _build_profile_fields(destination_plan.member)

    return NextSprintDraftInput(
        member_label=destination_plan.member.email,
        current_sprint_name=current_sprint_name,
        next_sprint_name=destination_plan.sprint.name,
        next_sprint_duration_weeks=destination_plan.sprint.duration_weeks,
        goal=destination_plan.goal,
        summary_current_situation=destination_plan.summary_current_situation,
        summary_goal=destination_plan.summary_goal,
        summary_main_gap=destination_plan.summary_main_gap,
        summary_weekly_hours=destination_plan.summary_weekly_hours,
        done_checkpoints=done_cp,
        not_done_checkpoints=not_done_cp,
        done_deliverables=done_del,
        not_done_deliverables=not_done_del,
        done_next_steps=done_ns,
        not_done_next_steps=not_done_ns,
        recent_updates=recent_updates,
        **profile_fields,
    )


def draft_next_sprint_plan(*, destination_plan, actor=None):
    """Run carry-over + (optionally) an LLM draft for ``destination_plan``.

    The single shared service path for the Studio view and the plans API.

    Steps:
      1. Resolve the carry-over source (the member's most-recent prior
         plan). If one exists, copy unfinished tasks into the destination
         and capture the count; otherwise skip silently.
      2. If ``llm.is_enabled()``: assemble the draft input from the
         destination plan's current state + the source plan's recent
         ``#plan-sprints`` threads (falling back to the member's threads
         when there is no source), call the pure draft callable, and upsert
         a single :class:`NextSprintPlanDraft` via ``update_or_create``
         (regenerate overwrites). On any ``LLMError`` the carry-over stands
         and NO draft row is written. The draft is held aside — the plan's
         own fields are never mutated.

    Args:
        destination_plan: the ``Plan`` being prepared for the next sprint.
        actor: the staff ``User`` who triggered the run (stored as
            ``generated_by``). Optional.

    Returns:
        dict: ``{
            'carried_over': int,
            'source_plan': Plan | None,
            'llm_enabled': bool,
            'draft': NextSprintPlanDraft | None,
            'draft_result': NextSprintDraftResult | None,
            'draft_error': bool,
            'update_count': int,
        }``
    """
    source_plan = find_carry_over_source_plan(destination_plan=destination_plan)
    carried_over = 0
    if source_plan is not None:
        carried_over = carry_over_unfinished_tasks(
            source_plan=source_plan,
            destination_plan=destination_plan,
        )

    llm_enabled = llm.is_enabled()
    result = {
        'carried_over': carried_over,
        'source_plan': source_plan,
        'llm_enabled': llm_enabled,
        'draft': None,
        'draft_result': None,
        'draft_error': False,
        'update_count': 0,
    }

    if not llm_enabled:
        # Degrade: carry-over already ran; write no draft row.
        return result

    if source_plan is not None:
        threads = threads_for_plan(source_plan)
    else:
        threads = threads_for_member(destination_plan.member)
    recent_updates, update_count = _collect_recent_updates(threads)
    result['update_count'] = update_count

    draft_input = _build_draft_input(
        destination_plan=destination_plan,
        source_plan=source_plan,
        recent_updates=recent_updates,
    )

    try:
        draft_result = draft_next_sprint(draft_input)
    except LLMError:
        # Carry-over stands; no partial draft row is written.
        logger.exception(
            'Next-sprint draft failed for plan %s', destination_plan.pk,
        )
        result['draft_error'] = True
        return result

    draft, _ = NextSprintPlanDraft.objects.update_or_create(
        plan=destination_plan,
        defaults={
            'result_json': draft_result.model_dump(),
            'source_plan': source_plan,
            'update_count': update_count,
            'model_name': get_config('LLM_MODEL', 'claude-sonnet-4-5'),
            'generated_by': actor,
            'generated_at': timezone.now(),
        },
    )
    result['draft'] = draft
    result['draft_result'] = draft_result
    return result
