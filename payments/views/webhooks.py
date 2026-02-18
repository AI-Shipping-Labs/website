"""Stripe webhook endpoint.

POST /api/webhooks/payments — receives Stripe events, validates the
signature, and dispatches to the appropriate handler in services.py.
"""

import json
import logging

import stripe
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.services import (
    handle_checkout_completed,
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_event_already_processed,
    record_processed_event,
    verify_webhook_signature,
)

logger = logging.getLogger(__name__)

# Map of Stripe event types to handler functions
EVENT_HANDLERS = {
    "checkout.session.completed": handle_checkout_completed,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
    "invoice.payment_failed": handle_invoice_payment_failed,
}


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """Handle incoming Stripe webhook events.

    Validates the webhook signature, checks for idempotency (duplicate
    events), and dispatches to the correct handler.

    Returns:
        200 on success (including already-processed events).
        400 on invalid signature or payload.
    """
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    # Verify webhook signature
    try:
        event = verify_webhook_signature(payload, sig_header)
    except (stripe.SignatureVerificationError, ValueError) as e:
        logger.warning("Webhook signature verification failed: %s", e)
        return JsonResponse({"error": "Invalid signature"}, status=400)

    event_id = event.get("id", "") if isinstance(event, dict) else getattr(event, "id", "")
    event_type = event.get("type", "") if isinstance(event, dict) else getattr(event, "type", "")
    event_data = event.get("data", {}) if isinstance(event, dict) else getattr(event, "data", {})
    event_object = event_data.get("object", {}) if isinstance(event_data, dict) else getattr(event_data, "object", {})

    # Idempotency check: skip if we already processed this event
    if is_event_already_processed(event_id):
        logger.info("Webhook event already processed: %s (%s)", event_id, event_type)
        return JsonResponse({"status": "already_processed"}, status=200)

    # Dispatch to handler
    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        # Unknown event type - acknowledge but don't process
        logger.info("Unhandled webhook event type: %s", event_type)
        return JsonResponse({"status": "ignored"}, status=200)

    try:
        # Convert event_object to dict if it's a Stripe object
        if hasattr(event_object, "to_dict"):
            obj_dict = event_object.to_dict()
        elif isinstance(event_object, dict):
            obj_dict = event_object
        else:
            obj_dict = dict(event_object)

        handler(obj_dict)
    except Exception:
        logger.exception(
            "Error processing webhook event %s (%s)", event_id, event_type
        )
        # Still record the event to avoid re-processing on retry
        # Stripe will retry, and we want to avoid duplicate processing
        record_processed_event(event_id, event_type, {"error": True})
        return JsonResponse({"error": "Processing failed"}, status=500)

    # Record the successfully processed event
    try:
        payload_json = json.loads(payload) if isinstance(payload, bytes) else payload
    except (json.JSONDecodeError, TypeError):
        payload_json = {}

    record_processed_event(event_id, event_type, payload_json)

    return JsonResponse({"status": "ok"}, status=200)
