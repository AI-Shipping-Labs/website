"""Bounded, privacy-safe package-mail evidence for staff operators."""

from community_base.mail.models import EmailDelivery
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse

from accounts.auth import token_required
from api.openapi import openapi_spec
from api.safety import error_response
from api.utils import require_methods

DELIVERY_FIELDS = (
    "id", "purpose", "template_key", "state", "external_message_id", "created_at", "updated_at",
)
DELIVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "purpose": {"type": "string"},
        "template_key": {"type": "string"},
        "state": {"type": "string"},
        "external_message_id": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
    },
}


def _delivery_filter(query):
    key = query.get("idempotency_key", "").strip()
    recipient = query.get("recipient", "").strip()
    purpose = query.get("purpose", "").strip()
    if key and not recipient and not purpose and len(key) <= 200:
        return {"idempotency_key": key}
    if key or not recipient or not purpose or len(purpose) > 128:
        return None
    try:
        validate_email(recipient)
    except ValidationError:
        return None
    if len(recipient) > 254:
        return None
    return {"recipient_email__iexact": recipient, "purpose": purpose}


def _delivery_payload(delivery):
    return {
        "id": str(delivery.id),
        "purpose": delivery.purpose,
        "template_key": delivery.template_key,
        "state": delivery.state,
        "external_message_id": delivery.external_message_id,
        "created_at": delivery.created_at.isoformat(),
        "updated_at": delivery.updated_at.isoformat(),
    }


@token_required
@require_methods("GET")
@openapi_spec(
    tag="Diagnostics",
    methods={"GET": {
        "summary": "Inspect package mail delivery evidence",
        "description": (
            "Requires an exact idempotency_key, or both an exact recipient "
            "address and purpose. Returns at most 20 newest deliveries. "
            "Omits recipients, durable context, transport options and errors."
        ),
        "query": {
            "idempotency_key": {"type": "string", "required": False},
            "recipient": {"type": "string", "required": False},
            "purpose": {"type": "string", "required": False},
        },
        "responses": {
            200: {
                "description": "Sanitized package delivery evidence.",
                "schema": {"type": "object", "properties": {
                    "deliveries": {"type": "array", "items": DELIVERY_SCHEMA, "maxItems": 20},
                    "limit": {"type": "integer"},
                }},
            },
            401: {"description": "Missing, invalid, or non-staff token."},
            422: {"description": "Supply an exact key or recipient and purpose."},
        },
    }},
)
def mail_delivery_diagnostics(request):
    filters = _delivery_filter(request.GET)
    if filters is None:
        return error_response(
            "Supply an exact idempotency_key or both recipient and purpose.",
            "validation_error",
            status=422,
        )
    deliveries = EmailDelivery.objects.filter(**filters).only(*DELIVERY_FIELDS).order_by("-created_at", "-id")
    rows = [_delivery_payload(delivery) for delivery in deliveries[:20]]
    return JsonResponse({"deliveries": rows, "limit": 20})
