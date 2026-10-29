"""Staff API endpoints for booking call requests (#1953)."""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone as dj_timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.utils import (
    parse_json_body,
    require_methods,
)
from booking.models import (
    STATUS_APPROVED,
    STATUS_CANCELED,
    STATUS_DECLINED,
    Booking,
    CallRequest,
)
from booking.services.booking_service import cancel_booking

TAG = "Booking"


def _iso_or_none(dt) -> str | None:
    if dt:
        return dt.isoformat()
    return None


def serialize_booking(booking: Booking) -> dict:
    host_slug = ""
    if booking.host:
        host_slug = booking.host.slug
    return {
        "id": booking.id,
        "host_id": booking.host_id,
        "host_slug": host_slug,
        "member_id": booking.member_id,
        "invitee_name": booking.invitee_name,
        "invitee_email": booking.invitee_email,
        "invitee_timezone": booking.invitee_timezone,
        "start_datetime": _iso_or_none(booking.start_datetime),
        "end_datetime": _iso_or_none(booking.end_datetime),
        "duration_minutes": booking.duration_minutes,
        "status": booking.status,
        "discussion_topic": booking.discussion_topic,
        "discussion_notes": booking.discussion_notes,
        "company": booking.company,
        "zoom_meeting_id": booking.zoom_meeting_id,
        "zoom_join_url": booking.zoom_join_url,
        "google_event_id": booking.google_event_id,
        "canceled_at": _iso_or_none(booking.canceled_at),
        "created_at": _iso_or_none(booking.created_at),
        "updated_at": _iso_or_none(booking.updated_at),
    }


@token_required(structured_errors=True)
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    methods={
        "GET": {
            "summary": "List member bookings",
            "description": "List and filter member 1:1 bookings by status or invitee email.",
            "responses": {
                200: {
                    "description": "Filtered list of bookings.",
                },
            },
        },
    },
)
def booking_bookings_collection(request):
    """List and filter member 1:1 bookings."""
    qs = Booking.objects.select_related("host", "member").order_by("-start_datetime")

    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())

    email_filter = request.GET.get("email")
    if email_filter:
        qs = qs.filter(invitee_email__iexact=email_filter.strip())

    data = [serialize_booking(b) for b in qs]
    return JsonResponse({"bookings": data, "count": len(data)})


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    methods={
        "POST": {
            "summary": "Cancel a member booking",
            "description": (
                "Cancel a 1:1 booking through cancel_booking, releasing the slot "
                "and deleting its Zoom meeting and Google Calendar event without "
                "sending a platform cancellation email to the member."
            ),
            "responses": {
                200: {
                    "description": "Canceled booking details.",
                },
                404: {
                    "description": "Booking not found.",
                },
            },
        },
    },
)
def booking_booking_cancel(request, booking_id):
    """Cancel a booking via the standard cancel_booking service."""
    booking = get_object_or_404(
        Booking.objects.select_related("host", "member", "booked_call"),
        pk=booking_id,
    )
    previous_status = booking.status
    if booking.status != STATUS_CANCELED:
        cancel_booking(booking)
    payload = serialize_booking(booking)
    payload["previous_status"] = previous_status
    payload["email_sent"] = False
    return JsonResponse(payload)


def serialize_call_request(call_request: CallRequest) -> dict:
    return {
        "id": call_request.id,
        "member_id": call_request.member_id,
        "member_email": call_request.member.email,
        "member_name": getattr(call_request.member, "name", "") or "",
        "host_id": call_request.host_id,
        "host_slug": call_request.host.slug if call_request.host else "",
        "topic": call_request.topic,
        "message": call_request.message,
        "status": call_request.status,
        "review_notes": call_request.review_notes,
        "reviewed_by_id": call_request.reviewed_by_id,
        "reviewed_at": call_request.reviewed_at.isoformat() if call_request.reviewed_at else None,
        "created_at": call_request.created_at.isoformat() if call_request.created_at else None,
        "updated_at": call_request.updated_at.isoformat() if call_request.updated_at else None,
    }


@token_required(structured_errors=True)
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    methods={
        "GET": {
            "summary": "List member call requests",
            "description": "List and filter member call requests by status or member.",
            "responses": {
                200: {
                    "description": "Filtered list of call requests.",
                },
            },
        },
    },
)
def booking_call_requests_collection(request):
    """List and filter call requests."""
    qs = CallRequest.objects.select_related("member", "host", "reviewed_by").order_by("-created_at")

    status_filter = request.GET.get("status")
    if status_filter:
        qs = qs.filter(status=status_filter.strip().lower())

    member_filter = request.GET.get("member")
    if member_filter:
        member_val = member_filter.strip()
        if member_val.isdigit():
            qs = qs.filter(member_id=int(member_val))
        else:
            qs = qs.filter(member__email__iexact=member_val)

    data = [serialize_call_request(r) for r in qs]
    return JsonResponse({"call_requests": data, "count": len(data)})


@token_required(structured_errors=True)
@require_methods("GET", structured_errors=True)
@openapi_spec(
    tag=TAG,
    methods={
        "GET": {
            "summary": "Get call request details",
            "description": "Fetch a single call request by ID.",
            "responses": {
                200: {
                    "description": "Call request details.",
                },
                404: {
                    "description": "Call request not found.",
                },
            },
        },
    },
)
def booking_call_request_detail(request, request_id):
    """Retrieve details for a single call request."""
    call_request = get_object_or_404(
        CallRequest.objects.select_related("member", "host", "reviewed_by"),
        pk=request_id,
    )
    return JsonResponse(serialize_call_request(call_request))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    methods={
        "POST": {
            "summary": "Approve a call request",
            "description": "Staff approves a call request, setting status='approved' and incrementing allowance.",
            "responses": {
                200: {
                    "description": "Updated call request.",
                },
                404: {
                    "description": "Call request not found.",
                },
            },
        },
    },
)
def booking_call_request_approve(request, request_id):
    """Staff approves a call request."""
    call_request = get_object_or_404(
        CallRequest.objects.select_related("member", "host", "reviewed_by"),
        pk=request_id,
    )

    body, _ = parse_json_body(request)
    if isinstance(body, dict) and "review_notes" in body:
        call_request.review_notes = str(body["review_notes"]).strip()

    call_request.status = STATUS_APPROVED
    call_request.reviewed_by = request.user
    call_request.reviewed_at = dj_timezone.now()
    call_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])

    return JsonResponse(serialize_call_request(call_request))


@token_required(structured_errors=True)
@csrf_exempt
@require_methods("POST", structured_errors=True)
@openapi_spec(
    tag=TAG,
    methods={
        "POST": {
            "summary": "Decline a call request",
            "description": "Staff declines a call request, recording review notes without granting allowance.",
            "responses": {
                200: {
                    "description": "Updated call request.",
                },
                404: {
                    "description": "Call request not found.",
                },
            },
        },
    },
)
def booking_call_request_decline(request, request_id):
    """Staff declines a call request."""
    call_request = get_object_or_404(
        CallRequest.objects.select_related("member", "host", "reviewed_by"),
        pk=request_id,
    )

    body, _ = parse_json_body(request)
    if isinstance(body, dict) and "review_notes" in body:
        call_request.review_notes = str(body["review_notes"]).strip()

    call_request.status = STATUS_DECLINED
    call_request.reviewed_by = request.user
    call_request.reviewed_at = dj_timezone.now()
    call_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])

    return JsonResponse(serialize_call_request(call_request))
