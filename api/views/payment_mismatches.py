import json

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import parse_json_body, require_methods
from community.models import CommunityAuditLog
from payments.models import PaymentAccountMismatch


def _actor_label(request):
    token = getattr(request, "auth_token", None)
    if token is None:
        return "unknown"
    if token.name:
        return token.name
    return token.key_prefix


def _user_payload(user):
    if user is None:
        return None
    return {"id": user.pk, "email": user.email}


def _dt(value):
    return value.isoformat() if value else None


def _serialize_mismatch(mismatch):
    return {
        "id": mismatch.pk,
        "stripe_session_id": mismatch.stripe_session_id,
        "stripe_customer_id": mismatch.stripe_customer_id,
        "stripe_subscription_id": mismatch.stripe_subscription_id,
        "stripe_email": mismatch.stripe_email,
        "paid_user": _user_payload(mismatch.paid_user),
        "candidate_user": _user_payload(mismatch.candidate_user),
        "reason": mismatch.reason,
        "status": mismatch.status,
        "details": mismatch.details,
        "created_at": _dt(mismatch.created_at),
        "updated_at": _dt(mismatch.updated_at),
        "resolved_at": _dt(mismatch.resolved_at),
        "resolved_by": _user_payload(mismatch.resolved_by),
        "resolution_note": mismatch.resolution_note,
    }


def _base_queryset():
    return PaymentAccountMismatch.objects.select_related(
        "paid_user",
        "candidate_user",
        "resolved_by",
    ).order_by("-created_at")


@token_required
@csrf_exempt
@require_methods("GET")
@openapi_spec(
    tag="Users",
    summary="List payment account mismatches",
    methods={
        "GET": {
            "summary": "List payment account mismatches",
            "description": (
                "Staff-token queue of Stripe checkout identity conflicts. "
                "Filters are optional and can be combined. This endpoint is "
                "diagnostic only and never merges accounts."
            ),
            "query": {
                "status": {
                    "type": "string",
                    "enum": ["open", "resolved", "ignored"],
                    "required": False,
                },
                "email": {"type": "string", "required": False},
                "stripe_customer_id": {"type": "string", "required": False},
                "stripe_session_id": {"type": "string", "required": False},
            },
            "responses": {
                200: {
                    "description": "Payment mismatch rows.",
                    "example": {
                        "payment_mismatches": [
                            {
                                "id": 42,
                                "stripe_session_id": "cs_123",
                                "stripe_customer_id": "cus_123",
                                "stripe_subscription_id": "sub_123",
                                "stripe_email": "billing@example.com",
                                "paid_user": {
                                    "id": 7,
                                    "email": "member@example.com",
                                },
                                "candidate_user": {
                                    "id": 8,
                                    "email": "billing@example.com",
                                },
                                "reason": "primary_email_collision",
                                "status": "open",
                            }
                        ]
                    },
                },
            },
        }
    },
)
def payment_mismatches_collection(request):
    qs = _base_queryset()

    status = (request.GET.get("status") or "").strip()
    if status:
        allowed = {
            PaymentAccountMismatch.STATUS_OPEN,
            PaymentAccountMismatch.STATUS_RESOLVED,
            PaymentAccountMismatch.STATUS_IGNORED,
        }
        if status not in allowed:
            return error_response(
                "Invalid status",
                "invalid_status",
                status=422,
                details={"field": "status", "allowed": sorted(allowed)},
            )
        qs = qs.filter(status=status)

    email = (request.GET.get("email") or "").strip()
    if email:
        qs = qs.filter(
            Q(stripe_email__icontains=email)
            | Q(paid_user__email__icontains=email)
            | Q(candidate_user__email__icontains=email)
        )

    stripe_customer_id = (request.GET.get("stripe_customer_id") or "").strip()
    if stripe_customer_id:
        qs = qs.filter(stripe_customer_id=stripe_customer_id)

    stripe_session_id = (request.GET.get("stripe_session_id") or "").strip()
    if stripe_session_id:
        qs = qs.filter(stripe_session_id=stripe_session_id)

    return JsonResponse({
        "payment_mismatches": [_serialize_mismatch(row) for row in qs[:200]]
    })


@token_required
@csrf_exempt
@require_methods("PATCH")
@openapi_spec(
    tag="Users",
    summary="Update payment account mismatch status",
    methods={
        "PATCH": {
            "summary": "Mark payment mismatch resolved or ignored",
            "description": (
                "Non-destructive status update for a payment mismatch. "
                "Requires ``status`` of ``resolved`` or ``ignored`` and a "
                "non-empty ``resolution_note``. It never merges accounts."
            ),
            "request_body": {
                "required": ["status", "resolution_note"],
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["resolved", "ignored"],
                    },
                    "resolution_note": {"type": "string"},
                },
                "example": {
                    "status": "resolved",
                    "resolution_note": "Merged through safe merge preview.",
                },
            },
            "responses": {
                200: {
                    "description": "Updated mismatch row.",
                    "example": {
                        "id": 42,
                        "status": "resolved",
                        "resolution_note": "Merged through safe merge preview.",
                    },
                },
                404: {
                    "description": "Mismatch not found.",
                    "example": {
                        "error": "Payment mismatch not found",
                        "code": "not_found",
                    },
                },
                422: {
                    "description": "Invalid status, unknown field, or empty note.",
                    "example": {
                        "error": "resolution_note is required",
                        "code": "validation_error",
                        "details": {"field": "resolution_note"},
                    },
                },
            },
        }
    },
)
def payment_mismatch_detail(request, mismatch_id):
    mismatch = _base_queryset().filter(pk=mismatch_id).first()
    if mismatch is None:
        return error_response(
            "Payment mismatch not found",
            "not_found",
            status=404,
        )

    data, parse_error = parse_json_body(request)
    if parse_error is not None:
        return parse_error
    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )
    for key in data:
        if key not in {"status", "resolution_note"}:
            return error_response(
                f"Unknown field: {key}",
                "unknown_field",
                status=422,
                details={"field": key},
            )

    status = data.get("status")
    if status not in PaymentAccountMismatch.TERMINAL_STATUSES:
        return error_response(
            "status must be resolved or ignored",
            "invalid_status",
            status=422,
            details={
                "field": "status",
                "allowed": sorted(PaymentAccountMismatch.TERMINAL_STATUSES),
            },
        )
    note = str(data.get("resolution_note") or "").strip()
    if not note:
        return error_response(
            "resolution_note is required",
            "validation_error",
            status=422,
            details={"field": "resolution_note"},
        )

    actor = _actor_label(request)
    with transaction.atomic():
        mismatch.mark_terminal(status=status, note=note, actor=request.user)
        mismatch.save(update_fields=[
            "status",
            "resolution_note",
            "resolved_by",
            "resolved_at",
            "updated_at",
        ])
        CommunityAuditLog.objects.create(
            user=mismatch.paid_user,
            action="payment_mismatch_updated",
            details=json.dumps({
                "actor_token": actor,
                "mismatch_id": mismatch.pk,
                "status": status,
                "stripe_session_id": mismatch.stripe_session_id,
                "resolution_note": note,
            }, sort_keys=True),
        )

    return JsonResponse(_serialize_mismatch(mismatch))
