"""Stripe webhook dispatch with durable per-delivery attempt evidence.

Issue #1314. This module is the single place that turns a verified Stripe
event into a persisted delivery attempt, an explicit machine-readable outcome,
and — for terminal outcomes — a ``WebhookEvent`` idempotency row plus a
one-shot operator alert. Both the live webhook view and the operator replay
API run through :func:`process_event` so they cannot diverge.

No unverified request body, ``Stripe-Signature`` header, API key, webhook
secret, member email, or full payload is ever persisted here.
"""

import logging

from django.core.mail import mail_admins
from django.db.models import Max
from django.utils import timezone

from payments.exceptions import (
    WebhookAmbiguousUserError,
    WebhookPermanentError,
    WebhookUnmatchedUserError,
)
from payments.models import StripeWebhookDeliveryAttempt, WebhookEvent
from payments.services import (
    handle_checkout_completed,
    handle_customer_updated,
    handle_invoice_paid,
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_event_already_processed,
    record_processed_event,
)

logger = logging.getLogger(__name__)

# Event types we know how to handle. Only handled types get a delivery attempt.
EVENT_HANDLERS = {
    "checkout.session.completed": handle_checkout_completed,
    "customer.updated": handle_customer_updated,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
    "invoice.payment_failed": handle_invoice_payment_failed,
    "invoice.paid": handle_invoice_paid,
}

# The two callbacks that carry cancellation authority. Alerts and replay are
# scoped to these.
CANCELLATION_EVENT_TYPES = frozenset({
    "customer.subscription.updated",
    "customer.subscription.deleted",
})

Attempt = StripeWebhookDeliveryAttempt


def is_handled_event_type(event_type):
    return event_type in EVENT_HANDLERS


def _id_of(value):
    """Return a Stripe id from a value that may be a string or expanded dict."""
    if isinstance(value, dict):
        return str(value.get("id", "") or "")
    return str(value or "")


def safe_object_ids(event_type, obj):
    """Extract safe (non-PII) Stripe identifiers from an event object."""
    obj = obj or {}
    object_id = _id_of(obj.get("id"))
    if event_type in CANCELLATION_EVENT_TYPES:
        return {
            "object_id": object_id,
            "customer_id": _id_of(obj.get("customer")),
            "subscription_id": object_id,
        }
    if event_type == "customer.updated":
        return {
            "object_id": object_id,
            "customer_id": object_id,
            "subscription_id": "",
        }
    # checkout.session.completed / invoice.payment_failed
    return {
        "object_id": object_id,
        "customer_id": _id_of(obj.get("customer")),
        "subscription_id": _id_of(obj.get("subscription")),
    }


def _next_attempt_number(event_id):
    """Monotonic per-event attempt number, safe under duplicate deliveries."""
    current = Attempt.objects.filter(stripe_event_id=event_id).aggregate(
        m=Max("attempt_number"),
    )["m"]
    return (current or 0) + 1


def record_attempt(*, event_id, event_type, obj, livemode,
                   source=Attempt.SOURCE_STRIPE_DELIVERY, requested_by=None):
    """Persist a fresh ``received`` delivery attempt before dispatch."""
    ids = safe_object_ids(event_type, obj)
    return Attempt.objects.create(
        source=source,
        requested_by=requested_by,
        stripe_event_id=event_id,
        event_type=event_type,
        stripe_object_id=ids["object_id"],
        stripe_customer_id=ids["customer_id"],
        stripe_subscription_id=ids["subscription_id"],
        livemode=livemode,
        attempt_number=_next_attempt_number(event_id),
        outcome=Attempt.OUTCOME_RECEIVED,
    )


def finalize_attempt(attempt, *, outcome, http_status,
                     error_code="", error_message=""):
    attempt.outcome = outcome
    attempt.http_status = http_status
    attempt.error_code = (error_code or "")[:64]
    attempt.error_message = (error_message or "")[:500]
    attempt.finished_at = timezone.now()
    attempt.save(update_fields=[
        "outcome", "http_status", "error_code", "error_message", "finished_at",
    ])
    return attempt


def run_handler(event_type, obj, *, event_context=None):
    """Run a handler and return an explicit outcome string.

    Handlers that mutate membership (subscriptions) return an outcome; other
    handlers return ``None`` on a clean run, which maps to ``processed``.
    """
    handler = EVENT_HANDLERS[event_type]
    if event_type in {
        "invoice.payment_failed", "invoice.paid",
        "customer.subscription.updated",
    }:
        result = handler(obj, event_context=event_context or {})
    else:
        result = handler(obj)
    if isinstance(result, str) and result:
        return result
    return Attempt.OUTCOME_PROCESSED


def _diagnostics_url():
    try:
        from django.urls import reverse

        from payments.services.stripe_endpoint_verifier import (
            get_expected_webhook_url,
        )
        base = get_expected_webhook_url().split("/api/webhooks/payments")[0]
        return base + reverse("studio_stripe_webhooks")
    except Exception:  # pragma: no cover - defensive, never break dispatch
        return "/studio/payments/stripe-webhooks/"


def _send_failure_alert(*, event_id, event_type, outcome, attempt,
                        error_code="", detail=""):
    """One secret-free admin alert for a new terminal cancellation failure."""
    subject = (
        f"[AISL] Stripe cancellation webhook {outcome}: {event_type}"
    )
    lines = [
        f"Outcome: {outcome}",
        f"Event type: {event_type}",
        f"Event id: {event_id}",
        f"Stripe customer id: {attempt.stripe_customer_id or '(none)'}",
        f"Stripe subscription id: {attempt.stripe_subscription_id or '(none)'}",
        f"Failure code: {error_code or '(none)'}",
    ]
    if detail:
        lines.append(f"Detail: {detail}")
    lines.append(f"Diagnostics: {_diagnostics_url()}")
    try:
        mail_admins(subject, "\n".join(lines), fail_silently=True)
    except Exception:  # pragma: no cover - alert must never break dispatch
        logger.exception("Failed to send cancellation failure alert for %s", event_id)


def process_event(*, event_id, event_type, obj, livemode, event_created=None,
                  source=Attempt.SOURCE_STRIPE_DELIVERY, requested_by=None):
    """Full lifecycle for a handled, signature-verified Stripe event.

    Persists a delivery attempt, checks terminal idempotency, dispatches to the
    handler, and records the terminal ``WebhookEvent`` + one-shot alert for
    terminal failures. Returns ``(outcome, http_status)``.

    Contract:

    - ``already_processed`` -> 200, no re-run.
    - ``processed`` / ``ignored_stale`` -> 200, terminal ``WebhookEvent``.
    - ``unmatched_user`` -> 500, NO terminal row (Stripe retries).
    - ``ambiguous_user`` -> 200, terminal row + alert (mutates nobody).
    - ``failed_permanent`` -> 200, terminal row + alert.
    - ``failed_transient`` -> 500, NO terminal row (Stripe retries).
    """
    attempt = record_attempt(
        event_id=event_id,
        event_type=event_type,
        obj=obj,
        livemode=livemode,
        source=source,
        requested_by=requested_by,
    )

    if is_event_already_processed(event_id):
        finalize_attempt(
            attempt, outcome=Attempt.OUTCOME_ALREADY_PROCESSED, http_status=200,
        )
        return Attempt.OUTCOME_ALREADY_PROCESSED, 200

    try:
        outcome = run_handler(
            event_type,
            obj,
            event_context={
                "event_id": event_id,
                "created": event_created,
                "livemode": livemode,
            },
        )
    except WebhookUnmatchedUserError as exc:
        finalize_attempt(
            attempt,
            outcome=Attempt.OUTCOME_UNMATCHED_USER,
            http_status=500,
            error_code="unmatched_user",
            error_message=str(exc),
        )
        logger.warning(
            "webhook %s (%s) unmatched user; retryable", event_id, event_type,
        )
        return Attempt.OUTCOME_UNMATCHED_USER, 500
    except WebhookAmbiguousUserError as exc:
        finalize_attempt(
            attempt,
            outcome=Attempt.OUTCOME_AMBIGUOUS_USER,
            http_status=200,
            error_code="ambiguous_user",
            error_message=str(exc),
        )
        _, created = record_processed_event(
            event_id, event_type, {},
            status=WebhookEvent.STATUS_FAILED_PERMANENT,
            error_message=f"ambiguous_user: {exc}"[:1000],
        )
        if created and event_type in CANCELLATION_EVENT_TYPES:
            _send_failure_alert(
                event_id=event_id, event_type=event_type,
                outcome=Attempt.OUTCOME_AMBIGUOUS_USER, attempt=attempt,
                error_code="ambiguous_user", detail=str(exc),
            )
        return Attempt.OUTCOME_AMBIGUOUS_USER, 200
    except WebhookPermanentError as exc:
        logger.warning(
            "Webhook handler permanent failure: %s (%s): %s",
            event_id, event_type, exc,
        )
        finalize_attempt(
            attempt,
            outcome=Attempt.OUTCOME_FAILED_PERMANENT,
            http_status=200,
            error_code="failed_permanent",
            error_message=repr(exc),
        )
        _, created = record_processed_event(
            event_id, event_type, {},
            status=WebhookEvent.STATUS_FAILED_PERMANENT,
            error_message=repr(exc)[:1000],
        )
        if created and event_type in CANCELLATION_EVENT_TYPES:
            _send_failure_alert(
                event_id=event_id, event_type=event_type,
                outcome=Attempt.OUTCOME_FAILED_PERMANENT, attempt=attempt,
                error_code="failed_permanent", detail=str(exc),
            )
        return Attempt.OUTCOME_FAILED_PERMANENT, 200
    except Exception as exc:  # transient
        finalize_attempt(
            attempt,
            outcome=Attempt.OUTCOME_FAILED_TRANSIENT,
            http_status=500,
            error_code="failed_transient",
            error_message=repr(exc),
        )
        logger.exception(
            "Transient error processing webhook %s (%s)", event_id, event_type,
        )
        return Attempt.OUTCOME_FAILED_TRANSIENT, 500

    # Clean terminal outcome (processed / ignored_stale).
    finalize_attempt(attempt, outcome=outcome, http_status=200)
    record_processed_event(
        event_id, event_type, {}, status=WebhookEvent.STATUS_PROCESSED,
    )
    return outcome, 200
