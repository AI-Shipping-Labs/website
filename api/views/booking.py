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
    STATUS_DECLINED,
    CallRequest,
)

TAG = "Booking"


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
