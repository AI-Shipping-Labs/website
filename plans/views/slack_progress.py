"""Owner-only member undo for Slack-applied sprint progress."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from crm.models import IngestedProgressEvent
from crm.tasks.apply_plan_sprint_progress import reverse_event
from plans.models import Plan


@login_required
@require_POST
def undo_slack_progress(request, sprint_slug, plan_id, event_id):
    """Undo one Slack auto-apply event from the member-owned plan page."""
    plan = get_object_or_404(
        Plan.objects.filter(
            pk=plan_id,
            member=request.user,
            sprint__slug=sprint_slug,
        ).select_related('sprint'),
    )
    event = get_object_or_404(
        IngestedProgressEvent.objects.filter(pk=event_id, plan=plan),
    )
    reverse_event(event)
    messages.success(request, 'Slack-applied updates were undone.')
    return redirect(
        'my_plan_detail',
        sprint_slug=plan.sprint.slug,
        plan_id=plan.pk,
    )
