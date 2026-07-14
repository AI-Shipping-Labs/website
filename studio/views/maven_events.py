"""Staff-only Maven occurrence inspection and safe per-step retry."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from community.models import CommunityAuditLog
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import STEP_NAMES, run_occurrence_steps
from studio.decorators import staff_required


@staff_required
def maven_event_list(request):
    events = MavenEnrollmentEvent.objects.select_related("user").order_by("-created_at")[:200]
    return render(request, "studio/maven_events/list.html", {"events": events})


@staff_required
def maven_event_detail(request, pk):
    event = get_object_or_404(MavenEnrollmentEvent.objects.select_related("user"), pk=pk)
    steps = [
        {
            "name": name,
            "status": getattr(event, f"{name}_status"),
            "attempts": getattr(event, f"{name}_attempts"),
            "attempted_at": getattr(event, f"{name}_attempted_at"),
            "completed_at": getattr(event, f"{name}_completed_at"),
            "error": getattr(event, f"{name}_error"),
        }
        for name in STEP_NAMES
    ]
    return render(request, "studio/maven_events/detail.html", {"event": event, "steps": steps})


@staff_required
@require_POST
def maven_event_retry(request, pk, step):
    if step not in STEP_NAMES:
        return redirect("studio_maven_event_detail", pk=pk)
    event = get_object_or_404(MavenEnrollmentEvent.objects.select_related("user"), pk=pk)
    run_occurrence_steps(event, step=step, force=True)
    audit_subject = event.user or request.user
    CommunityAuditLog.objects.create(
        user=audit_subject,
        action="maven_step_retry",
        details=(
            f"occurrence={event.pk} step={step} actor_staff_id={request.user.pk} "
            f"member_user_id={event.user_id or 'unknown'}"
        ),
    )
    messages.success(request, f"Retried Maven {step} step.")
    return redirect("studio_maven_event_detail", pk=pk)
