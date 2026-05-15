"""Webhook signature verification and event-idempotency persistence.

A ``WebhookEvent`` row exists for any event that has reached a terminal
state — either ``processed`` (handler ran clean) or ``failed_permanent``
(handler raised :class:`payments.exceptions.WebhookPermanentError`).
Transient failures MUST NOT call :func:`record_processed_event` so Stripe
keeps retrying.

The signature check itself reaches into the ``payments.services`` package
for ``stripe`` and ``get_config`` so tests can patch them at
``payments.services.stripe.Webhook.construct_event`` and
``payments.services.get_config``.
"""

from payments import services as _services
from payments.models import WebhookEvent


def verify_webhook_signature(payload, sig_header):
    """Verify a Stripe webhook signature.

    Args:
        payload: The raw request body (bytes).
        sig_header: The Stripe-Signature header value.

    Returns:
        The verified Stripe Event object.

    Raises:
        stripe.SignatureVerificationError: If the signature is invalid.
        ValueError: If the payload is invalid.
    """
    webhook_secret = _services.get_config("STRIPE_WEBHOOK_SECRET", "")
    event = _services.stripe.Webhook.construct_event(
        payload, sig_header, webhook_secret,
    )
    return event


def is_event_already_processed(event_id):
    """Check if a webhook event has already been processed (idempotency)."""
    return WebhookEvent.objects.filter(stripe_event_id=event_id).exists()


def record_processed_event(
    event_id,
    event_type,
    payload=None,
    status=WebhookEvent.STATUS_PROCESSED,
    error_message="",
):
    """Record that a webhook event has reached a terminal state.

    A ``WebhookEvent`` row means "do not run the handler for this event
    id again" — it represents either a clean handler run (``processed``)
    or a permanent, non-retryable failure (``failed_permanent``).
    Transient failures (generic ``Exception``) MUST NOT call this so
    Stripe's retry can re-run the handler.

    Idempotent via ``get_or_create``: if a concurrent retry beats us to
    the row, the existing row stays and we don't overwrite its status.
    """
    WebhookEvent.objects.get_or_create(
        stripe_event_id=event_id,
        defaults={
            "event_type": event_type,
            "payload": payload or {},
            "status": status,
            "error_message": error_message,
        },
    )
