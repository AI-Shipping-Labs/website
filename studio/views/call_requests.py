"""Studio views for Call Requests review interface (#1953)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone as dj_timezone
from django.views.decorators.http import require_GET, require_POST

from booking.models import (
    STATUS_APPROVED,
    STATUS_DECLINED,
    STATUS_PENDING,
    Booking,
    CallRequest,
)
from content.access import LEVEL_TO_TIER_NAME, get_user_level
from crm.models import CRMRecord
from studio.decorators import staff_required


@staff_required
@require_GET
def studio_call_requests_list(request):
    """List member call requests with status filters."""
    status_filter = request.GET.get("status", "all").strip().lower()

    qs = CallRequest.objects.select_related("member", "host").order_by("-created_at")

    counts = {
        "all": qs.count(),
        "pending": qs.filter(status=STATUS_PENDING).count(),
        "approved": qs.filter(status=STATUS_APPROVED).count(),
        "declined": qs.filter(status=STATUS_DECLINED).count(),
    }

    if status_filter in (STATUS_PENDING, STATUS_APPROVED, STATUS_DECLINED):
        requests_list = qs.filter(status=status_filter)
    else:
        status_filter = "all"
        requests_list = qs

    # Attach tier names for display
    enriched = []
    for req in requests_list:
        try:
            level = get_user_level(req.member)
            tier_name = LEVEL_TO_TIER_NAME.get(level, "Free")
        except Exception:
            tier_name = ""
        enriched.append({
            "request": req,
            "tier_name": tier_name,
        })

    context = {
        "call_requests": enriched,
        "counts": counts,
        "active_status": status_filter,
    }
    return render(request, "studio/call_requests/list.html", context)


@staff_required
@require_GET
def studio_call_request_detail(request, request_id):
    """View details of a member's call request with CRM links and booking history."""
    call_request = get_object_or_404(
        CallRequest.objects.select_related("member", "host", "reviewed_by"),
        pk=request_id,
    )

    member = call_request.member
    try:
        level = get_user_level(member)
        tier_name = LEVEL_TO_TIER_NAME.get(level, "Free")
    except Exception:
        tier_name = ""

    crm_record = CRMRecord.objects.filter(user=member).first()

    previous_bookings = Booking.objects.filter(member=member).select_related("host").order_by("-start_datetime")

    context = {
        "call_request": call_request,
        "member": member,
        "tier_name": tier_name,
        "crm_record": crm_record,
        "previous_bookings": previous_bookings,
    }
    return render(request, "studio/call_requests/detail.html", context)


@staff_required
@require_POST
def studio_call_request_approve(request, request_id):
    """Approve a call request, granting +1 booking allowance."""
    call_request = get_object_or_404(CallRequest.objects.select_related("member"), pk=request_id)

    review_notes = request.POST.get("review_notes", "").strip()
    if review_notes:
        call_request.review_notes = review_notes

    call_request.status = STATUS_APPROVED
    call_request.reviewed_by = request.user
    call_request.reviewed_at = dj_timezone.now()
    call_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])

    messages.success(
        request,
        f"Call request approved for {call_request.member.email}. (+1 booking allowance granted)",
    )
    return redirect("studio_call_request_detail", request_id=call_request.pk)


@staff_required
@require_POST
def studio_call_request_decline(request, request_id):
    """Decline a call request, recording review notes."""
    call_request = get_object_or_404(CallRequest.objects.select_related("member"), pk=request_id)

    review_notes = request.POST.get("review_notes", "").strip()
    call_request.review_notes = review_notes
    call_request.status = STATUS_DECLINED
    call_request.reviewed_by = request.user
    call_request.reviewed_at = dj_timezone.now()
    call_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])

    messages.success(
        request,
        f"Call request declined for {call_request.member.email}.",
    )
    return redirect("studio_call_request_detail", request_id=call_request.pk)
