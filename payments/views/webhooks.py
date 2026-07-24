"""Stripe webhook endpoint.

POST /api/webhooks/payments — receives Stripe events, validates the
signature, and dispatches through :mod:`payments.services.webhook_dispatch`,
which persists a secret-free delivery attempt, records terminal idempotency
evidence, and returns an explicit machine-readable outcome.
"""

import logging

import stripe
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.models import StripeWebhookDeliveryAttempt
from payments.services import verify_webhook_signature
from payments.services.webhook_dispatch import is_handled_event_type, process_event

logger = logging.getLogger(__name__)

# Unmatched/transient outcomes stay retryable (500); every terminal outcome
# acknowledges with 200 so Stripe stops retrying.
_RETRYABLE_OUTCOMES = {
    StripeWebhookDeliveryAttempt.OUTCOME_UNMATCHED_USER,
    StripeWebhookDeliveryAttempt.OUTCOME_FAILED_TRANSIENT,
}


def _event_field(event, key, default=None):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle incoming Stripe webhook events.

    Verifies the Stripe signature (invalid -> 400, nothing persisted), then
    dispatches handled events through :func:`process_event`. Unknown event
    types are acknowledged with 200 and no attempt row so unauthenticated or
    noisy traffic cannot fill the attempt table.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = verify_webhook_signature(payload, sig_header)
    except (stripe.SignatureVerificationError, ValueError) as e:
        logger.warning("Webhook signature verification failed: %s", e)
        return JsonResponse({"error": "Invalid signature"}, status=400)

    event_id = _event_field(event, "id", "") or ""
    event_type = _event_field(event, "type", "") or ""
    livemode = _event_field(event, "livemode", None)
    event_data = _event_field(event, "data", {}) or {}
    event_object = (
        event_data.get("object", {})
        if isinstance(event_data, dict)
        else getattr(event_data, "object", {})
    )

    if not is_handled_event_type(event_type):
        logger.info("Unhandled webhook event type: %s", event_type)
        return JsonResponse({"status": "ignored"}, status=200)

    # Normalize the object to a plain dict.
    if hasattr(event_object, "to_dict"):
        obj_dict = event_object.to_dict()
    elif isinstance(event_object, dict):
        obj_dict = event_object
    else:
        obj_dict = dict(event_object)

    outcome, http_status = process_event(
        event_id=event_id,
        event_type=event_type,
        obj=obj_dict,
        livemode=livemode,
    )
    return JsonResponse({"status": outcome}, status=http_status)
