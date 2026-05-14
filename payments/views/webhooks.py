"""Stripe webhook endpoint.

POST /api/webhooks/payments — receives Stripe events, validates the
signature, and dispatches to the appropriate handler in services.py.
"""

import json
import logging

import stripe
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from payments.exceptions import WebhookPermanentError
from payments.models import WebhookEvent
from payments.services import (
    handle_checkout_completed,
    handle_customer_updated,
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
    "customer.updated": handle_customer_updated,
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

    Ordering is the safety property of this view:

    - A ``WebhookEvent`` row is inserted ONLY after the handler reaches
      a terminal state (clean return or ``WebhookPermanentError``).
    - On a generic ``Exception`` the handler is treated as transient:
      no row is recorded and the view returns ``500`` so Stripe retries.
      The next delivery passes the idempotency check (no row exists)
      and re-runs the handler. Handlers are written to be safe to run
      more than once.

    Returns:
        200 on success or terminal-permanent failure or already-processed
        or unknown event type.
        400 on invalid signature or payload.
        500 on transient handler failure (so Stripe retries).
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

    # Idempotency check: a row exists in any terminal state (processed
    # OR failed_permanent), so short-circuit. Both states are terminal
    # from Stripe's perspective — we never want to re-run the handler.
    if is_event_already_processed(event_id):
        logger.info("Webhook event already processed: %s (%s)", event_id, event_type)
        return JsonResponse({"status": "already_processed"}, status=200)

    # Dispatch to handler
    handler = EVENT_HANDLERS.get(event_type)
    if handler is None:
        # Unknown event type - acknowledge but don't process. We do NOT
        # record a WebhookEvent row for unknown types; Stripe doesn't
        # retry on 200, and not recording keeps the table focused on
        # events we know how to handle.
        logger.info("Unhandled webhook event type: %s", event_type)
        return JsonResponse({"status": "ignored"}, status=200)

    # Convert event_object to dict if it's a Stripe object
    if hasattr(event_object, "to_dict"):
        obj_dict = event_object.to_dict()
    elif isinstance(event_object, dict):
        obj_dict = event_object
    else:
        obj_dict = dict(event_object)

    try:
        handler(obj_dict)
    except WebhookPermanentError as exc:
        # Permanent, non-retryable failure: record a terminal row so
        # Stripe stops retrying, and return 200.
        logger.error(
            "Webhook handler raised WebhookPermanentError: %s (%s): %s",
            event_id,
            event_type,
            exc,
        )
        try:
            payload_json = (
                json.loads(payload) if isinstance(payload, bytes) else payload
            )
        except (json.JSONDecodeError, TypeError):
            payload_json = {}
        record_processed_event(
            event_id,
            event_type,
            payload_json,
            status=WebhookEvent.STATUS_FAILED_PERMANENT,
            error_message=repr(exc)[:1000],
        )
        return JsonResponse({"status": "failed_permanent"}, status=200)
    except Exception:
        # Transient failure: do NOT record a row. Stripe will retry,
        # and the next delivery will re-run the handler.
        logger.exception(
            "Error processing webhook event %s (%s)", event_id, event_type
        )
        return JsonResponse({"error": "Processing failed"}, status=500)

    # Handler returned cleanly — record the event as processed.
    try:
        payload_json = json.loads(payload) if isinstance(payload, bytes) else payload
    except (json.JSONDecodeError, TypeError):
        payload_json = {}

    record_processed_event(
        event_id,
        event_type,
        payload_json,
        status=WebhookEvent.STATUS_PROCESSED,
    )

    return JsonResponse({"status": "ok"}, status=200)
