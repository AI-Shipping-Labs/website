"""Staff-only Maven occurrence inspection and safe per-step retry."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from community.models import CommunityAuditLog
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import STEP_NAMES, run_occurrence_steps
from integrations.services.maven_attention import (
    failed_occurrences,
    needs_attention_occurrences,
    occurrence_attention_reasons,
)
from studio.decorators import staff_required
from studio.utils import studio_pagination_context


@staff_required
def maven_event_list(request):
    status_filter = request.GET.get("status", "all")
    if status_filter not in {"all", "failed", "needs_attention"}:
        status_filter = "all"
    base = MavenEnrollmentEvent.objects.select_related("user").order_by("-created_at")
    has_occurrences = MavenEnrollmentEvent.objects.exists()
    if status_filter == "failed":
        events = failed_occurrences(base)
    elif status_filter == "needs_attention":
        events = needs_attention_occurrences(base)
    else:
        events = base
    pager = studio_pagination_context(request, events)
    page_events = list(pager["page"].object_list)
    attention_ids = set(
        needs_attention_occurrences(
            MavenEnrollmentEvent.objects.filter(pk__in=[event.pk for event in page_events])
        ).values_list("pk", flat=True)
    )
    for event in page_events:
        event.needs_attention = event.pk in attention_ids
    pager["page"].object_list = page_events
    return render(
        request,
        "studio/maven_events/list.html",
        {
            "events": page_events,
            "status_filter": status_filter,
            "has_occurrences": has_occurrences,
            "filters_active": status_filter != "all",
            "clear_url": reverse("studio_maven_event_list"),
            **pager,
        },
    )


@staff_required
def maven_event_detail(request, pk):
    event = get_object_or_404(MavenEnrollmentEvent.objects.select_related("user"), pk=pk)
    attention_reasons = occurrence_attention_reasons(event)
    steps = [
        {
            "name": name,
            "status": getattr(event, f"{name}_status"),
            "attempts": getattr(event, f"{name}_attempts"),
            "attempted_at": getattr(event, f"{name}_attempted_at"),
            "completed_at": getattr(event, f"{name}_completed_at"),
            "error": getattr(event, f"{name}_error"),
            "attention_reason": attention_reasons.get(name, ""),
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
    attempts_before = getattr(event, f"{step}_attempts")
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
    event.refresh_from_db()
    status = getattr(event, f"{step}_status")
    attempts_after = getattr(event, f"{step}_attempts")
    if status == MavenEnrollmentEvent.STEP_SUCCEEDED:
        messages.success(request, f"Maven {step} step recovered.")
        if step == "override":
            run_occurrence_steps(event)
    elif status == MavenEnrollmentEvent.STEP_SKIPPED:
        messages.info(request, f"Maven {step} step was skipped; no retry was needed.")
    elif status == MavenEnrollmentEvent.STEP_FAILED:
        messages.error(request, f"Maven {step} step failed again. Fix the cause before retrying.")
    elif status == MavenEnrollmentEvent.STEP_RUNNING and attempts_after == attempts_before:
        messages.warning(request, f"Maven {step} step is already running and was not repeated.")
    else:
        messages.warning(request, f"Maven {step} step was not completed; review its current state before retrying.")
    return redirect("studio_maven_event_detail", pk=pk)
