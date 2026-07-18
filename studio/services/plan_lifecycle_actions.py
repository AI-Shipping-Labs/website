"""Shared Studio context for sprint-plan lifecycle actions."""

from django.urls import reverse

from plans.services import (
    count_unfinished_carry_over_items,
    find_carry_over_source_plan,
)


def build_plan_lifecycle_action_context(plan):
    """Build action URLs and confirmation copy for detail and editor."""
    source = find_carry_over_source_plan(destination_plan=plan)
    target_name = plan.sprint.name
    if source is None:
        carry_confirmation = (
            f'No prior sprint plan is available to carry into {target_name}. '
            'Continue?'
        )
        draft_confirmation = (
            f'Draft the {target_name} sprint plan with the LLM? No prior '
            'plan is available for carry-over. The draft is held for '
            'review, not published. Continue?'
        )
    else:
        count = count_unfinished_carry_over_items(
            source_plan=source,
            destination_plan=plan,
        )
        label = 'task' if count == 1 else 'tasks'
        carry_confirmation = (
            f'Carry {count} unfinished {label} from {source.sprint.name} '
            f'into {target_name}? This cannot be undone.'
        )
        draft_confirmation = (
            f'Draft the {target_name} sprint plan with the LLM and carry '
            f'{count} unfinished {label} from {source.sprint.name}? '
            'The draft is held for review, not published. Continue?'
        )
    return {
        'carry_over_confirmation': carry_confirmation,
        'draft_next_sprint_confirmation': draft_confirmation,
        'plan_editor_return_to': reverse(
            'studio_plan_edit', kwargs={'plan_id': plan.pk},
        ),
    }


def lifecycle_return_url(request, plan):
    """Accept only this plan's Studio editor as a POST return target."""
    editor_url = reverse('studio_plan_edit', kwargs={'plan_id': plan.pk})
    return (
        editor_url
        if request.POST.get('return_to') == editor_url
        else reverse('studio_plan_detail', kwargs={'plan_id': plan.pk})
    )
