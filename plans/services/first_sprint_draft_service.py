"""Shared first-sprint draft orchestration (issue #1205)."""

import logging

from django.db import transaction
from django.utils import timezone

from crm.services.member_profile import build_member_profile_context
from integrations.config import get_config
from integrations.services import llm
from integrations.services.llm import LLMError
from plans.models import (
    NEXT_STEP_KIND_PRE_SPRINT,
    Checkpoint,
    Deliverable,
    FirstSprintPlanDraft,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Week,
)
from plans.services.first_sprint_draft import (
    FirstSprintDraftInput,
    FirstSprintDraftResult,
    OnboardingAnswer,
    RecentActivity,
    draft_first_sprint,
)
from questionnaires.onboarding import get_onboarding_response

logger = logging.getLogger(__name__)


class FirstSprintDraftSourceMissing(Exception):
    """Raised when a plan has no submitted onboarding response."""


def _submitted_onboarding_response(member):
    response = get_onboarding_response(member)
    if response is None or response.status != 'submitted':
        raise FirstSprintDraftSourceMissing(
            'A submitted onboarding response is required.'
        )
    return response


def _build_draft_input(*, plan, source_response):
    """Assemble plain LLM input from member profile and onboarding answers."""
    _ = source_response
    context = build_member_profile_context(plan.member)
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
    return FirstSprintDraftInput(
        member_label=plan.member.email,
        sprint_name=plan.sprint.name,
        sprint_duration_weeks=plan.sprint.duration_weeks,
        persona=context['persona'],
        onboarding_answers=onboarding_answers,
        recent_activity=recent_activity,
        crm_summary=context['summary'],
        crm_next_steps=context['next_steps'],
    )


def draft_first_sprint_plan(*, plan, actor=None):
    """Create/regenerate the held-aside first-sprint draft for ``plan``.

    Returns a dict with ``llm_enabled``, ``draft``, ``draft_result``,
    ``draft_error``, and ``source_response``. No live plan fields are mutated.
    """
    source_response = _submitted_onboarding_response(plan.member)
    llm_enabled = llm.is_enabled()
    result = {
        'llm_enabled': llm_enabled,
        'draft': None,
        'draft_result': None,
        'draft_error': False,
        'source_response': source_response,
    }
    if not llm_enabled:
        return result

    draft_input = _build_draft_input(
        plan=plan,
        source_response=source_response,
    )
    try:
        draft_result = draft_first_sprint(draft_input)
    except LLMError:
        logger.exception('First-sprint draft failed for plan %s', plan.pk)
        result['draft_error'] = True
        return result

    draft, _ = FirstSprintPlanDraft.objects.update_or_create(
        plan=plan,
        defaults={
            'result_json': draft_result.model_dump(),
            'source_response': source_response,
            'model_name': get_config('LLM_MODEL', 'claude-sonnet-4-5'),
            'generated_by': actor,
            'generated_at': timezone.now(),
        },
    )
    result['draft'] = draft
    result['draft_result'] = draft_result
    return result


def _trim_list(values):
    return [
        str(value).strip()
        for value in (values or [])
        if str(value).strip()
    ]


def _replace_weeks(plan, result):
    weeks_by_number = {
        week.week_number: week
        for week in plan.weeks.all()
    }
    Checkpoint.objects.filter(week__plan=plan).delete()
    for index, draft_week in enumerate(result.weeks):
        week = weeks_by_number.get(draft_week.week_number)
        if week is None:
            week = Week.objects.create(
                plan=plan,
                week_number=draft_week.week_number,
                position=index,
                theme=draft_week.theme or '',
            )
        else:
            week.position = index
            week.theme = draft_week.theme or ''
            week.save(update_fields=['position', 'theme', 'updated_at'])
        for position, description in enumerate(_trim_list(draft_week.checkpoints)):
            Checkpoint.objects.create(
                week=week,
                description=description,
                position=position,
            )


def _replace_resources(plan, result):
    plan.resources.all().delete()
    for position, resource in enumerate(result.resources or []):
        title = (resource.title or '').strip()
        if not title:
            continue
        Resource.objects.create(
            plan=plan,
            title=title,
            url=(resource.url or '').strip(),
            note=(resource.note or '').strip(),
            position=position,
        )


def _replace_deliverables(plan, result):
    plan.deliverables.all().delete()
    for position, description in enumerate(_trim_list(result.deliverables)):
        Deliverable.objects.create(
            plan=plan,
            description=description,
            position=position,
        )


def _replace_next_steps(plan, result):
    plan.next_steps.all().delete()
    for position, description in enumerate(_trim_list(result.next_steps)):
        NextStep.objects.create(
            plan=plan,
            kind=NEXT_STEP_KIND_PRE_SPRINT,
            description=description,
            position=position,
        )


def _write_internal_note(plan, result, *, actor=None):
    body = (result.internal_notes or '').strip()
    if not body:
        return None
    return InterviewNote.objects.create(
        member=plan.member,
        plan=plan,
        visibility='internal',
        kind='general',
        body=body,
        created_by=actor,
    )


def apply_first_sprint_draft(*, draft, actor=None):
    """Atomically write the current first-sprint draft into live plan rows."""
    result = FirstSprintDraftResult.model_validate(draft.result_json or {})
    with transaction.atomic():
        plan = (
            Plan.objects
            .select_related('member', 'sprint')
            .prefetch_related('weeks')
            .select_for_update()
            .get(pk=draft.plan_id)
        )
        plan.title = (result.title or '').strip() or plan.fallback_title()
        plan.goal = result.goal or ''
        plan.summary_current_situation = result.summary_current_situation or ''
        plan.summary_goal = result.summary_goal or ''
        plan.summary_main_gap = result.summary_main_gap or ''
        plan.summary_weekly_hours = result.summary_weekly_hours or ''
        plan.summary_why_this_plan = result.summary_why_this_plan or ''
        plan.focus_main = result.focus_main or ''
        plan.focus_supporting = _trim_list(result.focus_supporting)
        plan.accountability = result.accountability or ''
        plan.save(update_fields=[
            'title',
            'goal',
            'summary_current_situation',
            'summary_goal',
            'summary_main_gap',
            'summary_weekly_hours',
            'summary_why_this_plan',
            'focus_main',
            'focus_supporting',
            'accountability',
            'updated_at',
        ])
        _replace_weeks(plan, result)
        _replace_resources(plan, result)
        _replace_deliverables(plan, result)
        _replace_next_steps(plan, result)
        _write_internal_note(plan, result, actor=actor)
        draft.delete()
    return plan
