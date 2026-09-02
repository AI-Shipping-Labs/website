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
import re

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
    handle_checkout_async_payment_failed,
    handle_checkout_async_payment_succeeded,
    handle_checkout_completed,
    handle_customer_updated,
    handle_invoice_paid,
    handle_invoice_payment_failed,
    handle_subscription_deleted,
    handle_subscription_updated,
    is_event_already_processed,
    record_processed_event,
)
from payments.services.refund_dispute_review import (
    DISPUTE_EVENT_TYPES,
    REFUND_EVENT_TYPES,
    REVIEW_EVENT_TYPES,
    RefundDisputeReview,
    classify_refund_or_dispute,
    safe_stripe_id,
)

logger = logging.getLogger(__name__)

# Event types we know how to handle. Only handled types get a delivery attempt.
EVENT_HANDLERS = {
    "checkout.session.completed": handle_checkout_completed,
    "checkout.session.async_payment_succeeded": handle_checkout_async_payment_succeeded,
    "checkout.session.async_payment_failed": handle_checkout_async_payment_failed,
    "customer.updated": handle_customer_updated,
    "customer.subscription.updated": handle_subscription_updated,
    "customer.subscription.deleted": handle_subscription_deleted,
    "invoice.payment_failed": handle_invoice_payment_failed,
    "invoice.paid": handle_invoice_paid,
    "charge.refunded": None,
    "charge.dispute.created": None,
    "charge.dispute.closed": None,
}

# The two callbacks that carry cancellation authority. Alerts and replay are
# scoped to these.
CANCELLATION_EVENT_TYPES = frozenset({
    "customer.subscription.updated",
    "customer.subscription.deleted",
})

Attempt = StripeWebhookDeliveryAttempt
_DECIMAL_USER_ID_RE = re.compile(r"[0-9]+\Z")
_MAX_BIGINT = 2**63 - 1


def is_handled_event_type(event_type):
    return event_type in EVENT_HANDLERS


def _id_of(value):
    """Return a Stripe id from a value that may be a string or expanded dict."""
    if isinstance(value, dict):
        value = value.get("id", "")
    if not isinstance(value, str) or not value or value != value.strip():
        return ""
    return value if len(value) <= 255 else ""


def _typed_id(value, prefix):
    return safe_stripe_id(value, prefix)


def _safe_local_user_id(value):
    """Return a positive, bounded local user ID or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif (
        isinstance(value, str)
        and len(value) <= len(str(_MAX_BIGINT))
        and _DECIMAL_USER_ID_RE.fullmatch(value)
    ):
        number = int(value)
    else:
        return None
    return number if 0 < number <= _MAX_BIGINT else None


def safe_subject_user_id(obj):
    """Extract one unambiguous app-owned numeric user ID from a Stripe object."""
    if not isinstance(obj, dict):
        return None

    candidates = []
    metadata = obj.get("metadata")
    if isinstance(metadata, dict) and "user_id" in metadata:
        value = _safe_local_user_id(metadata["user_id"])
        if value is not None:
            candidates.append(value)

    if "client_reference_id" in obj:
        value = _safe_local_user_id(obj["client_reference_id"])
        if value is not None:
            candidates.append(value)

    distinct = set(candidates)
    return next(iter(distinct)) if len(distinct) == 1 else None


def safe_object_ids(event_type, obj):
    """Extract safe (non-PII) Stripe identifiers from an event object."""
    obj = obj or {}
    object_id = _id_of(obj.get("id"))
    if event_type in REFUND_EVENT_TYPES:
        charge_id = _typed_id(obj.get("id"), "ch_")
        return {
            "object_id": charge_id,
            "customer_id": _typed_id(obj.get("customer"), "cus_"),
            "subscription_id": "",
            "charge_id": charge_id,
            "invoice_id": _typed_id(obj.get("invoice"), "in_"),
            "dispute_id": "",
        }
    if event_type in DISPUTE_EVENT_TYPES:
        dispute_id = _typed_id(obj.get("id"), "dp_")
        charge_id = _typed_id(obj.get("charge"), "ch_")
        return {
            "object_id": dispute_id,
            "customer_id": "",
            "subscription_id": "",
            "charge_id": charge_id,
            "invoice_id": "",
            "dispute_id": dispute_id,
        }
    if event_type in CANCELLATION_EVENT_TYPES:
        return {
            "object_id": object_id,
            "customer_id": _id_of(obj.get("customer")),
            "subscription_id": object_id,
            "charge_id": "",
            "invoice_id": "",
            "dispute_id": "",
        }
    if event_type == "customer.updated":
        return {
            "object_id": object_id,
            "customer_id": object_id,
            "subscription_id": "",
            "charge_id": "",
            "invoice_id": "",
            "dispute_id": "",
        }
    # Checkout Session / invoice payment events
    return {
        "object_id": object_id,
        "customer_id": _id_of(obj.get("customer")),
        "subscription_id": _id_of(obj.get("subscription")),
        "charge_id": "",
        "invoice_id": "",
        "dispute_id": "",
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
        stripe_charge_id=ids["charge_id"],
        stripe_invoice_id=ids["invoice_id"],
        stripe_dispute_id=ids["dispute_id"],
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


def _terminal_correlations(obj, attempt):
    return {
        "subject_user_id": safe_subject_user_id(obj),
        "stripe_customer_id": attempt.stripe_customer_id,
        "stripe_subscription_id": attempt.stripe_subscription_id,
    }


def run_handler(event_type, obj, *, event_context=None, attempt=None):
    """Run a handler and return an explicit outcome string.

    Handlers that mutate membership (subscriptions) return an outcome; other
    handlers return ``None`` on a clean run, which maps to ``processed``.
    """
    if event_type in REVIEW_EVENT_TYPES:
        return classify_refund_or_dispute(event_type, obj, attempt)
    handler = EVENT_HANDLERS[event_type]
    if event_type in {
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
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


def _review_member_url(user_id):
    try:
        from django.urls import reverse

        from payments.services.stripe_endpoint_verifier import (
            get_expected_webhook_url,
        )
        base = get_expected_webhook_url().split("/api/webhooks/payments")[0]
        return base + reverse("studio_user_detail", args=[user_id])
    except Exception:  # pragma: no cover - alert must never break dispatch
        return ""


def _send_review_alert(*, event_id, livemode, attempt, review):
    """Send one secret-free post-terminal operator review alert."""
    from payments.services.monthly_payment_grace import _effective_tier

    subject = (
        "[Payments] Stripe refund requires review"
        if review.category == "refund"
        else "[Payments] Stripe dispute requires review"
    )
    lines = [
        f"Event type: {review.event_type}",
        f"Event id: {event_id}",
        f"Mode: {'live' if livemode else 'test'}",
        f"Classification: {review.classification}",
        f"Resolution: {review.resolution}",
        f"Amount: {review.amount if review.amount is not None else '(unknown)'}",
        f"Currency: {review.currency or '(unknown)'}",
        f"Stripe charge id: {attempt.stripe_charge_id or '(none)'}",
        f"Stripe invoice id: {attempt.stripe_invoice_id or '(none)'}",
        f"Stripe dispute id: {attempt.stripe_dispute_id or '(none)'}",
        f"Stripe customer id: {attempt.stripe_customer_id or '(none)'}",
        f"Stripe subscription id: {attempt.stripe_subscription_id or '(none)'}",
    ]
    if review.dispute_status:
        lines.append(f"Dispute status: {review.dispute_status}")
    if review.user is not None:
        effective = _effective_tier(review.user)
        lines.extend([
            f"Local user id: {review.user.pk}",
            f"Local user email: {review.user.email}",
            f"Base tier: {review.user.tier.slug if review.user.tier_id else 'free'}",
            f"Effective tier: {effective.slug if effective else 'free'}",
        ])
        member_url = _review_member_url(review.user.pk)
        if member_url:
            lines.append(f"Member: {member_url}")
    lines.extend([
        "No access was changed by this callback.",
        "If membership access should end, cancel the subscription in Stripe; "
        "customer.subscription.deleted is the authoritative access transition.",
        f"Diagnostics: {_diagnostics_url()}",
    ])
    try:
        mail_admins(subject, "\n".join(lines), fail_silently=True)
    except Exception:  # pragma: no cover - alert must never break dispatch
        logger.exception("Failed to send payment review alert for %s", event_id)


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
            attempt=attempt,
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
            **_terminal_correlations(obj, attempt),
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
            **_terminal_correlations(obj, attempt),
        )
        if created and event_type in CANCELLATION_EVENT_TYPES:
            _send_failure_alert(
                event_id=event_id, event_type=event_type,
                outcome=Attempt.OUTCOME_FAILED_PERMANENT, attempt=attempt,
                error_code="failed_permanent", detail=str(exc),
            )
        return Attempt.OUTCOME_FAILED_PERMANENT, 200
    except Exception as exc:  # transient
        safe_error_message = (
            "Transient Stripe/configuration lookup failure"
            if event_type in REVIEW_EVENT_TYPES
            else repr(exc)
        )
        finalize_attempt(
            attempt,
            outcome=Attempt.OUTCOME_FAILED_TRANSIENT,
            http_status=500,
            error_code="failed_transient",
            error_message=safe_error_message,
        )
        logger.exception(
            "Transient error processing webhook %s (%s)", event_id, event_type,
        )
        return Attempt.OUTCOME_FAILED_TRANSIENT, 500

    if isinstance(outcome, RefundDisputeReview):
        finalize_attempt(
            attempt,
            outcome=Attempt.OUTCOME_REVIEW_REQUIRED,
            http_status=200,
            error_code=outcome.classification,
            error_message=outcome.safe_summary,
        )
        _, created = record_processed_event(
            event_id,
            event_type,
            {},
            status=WebhookEvent.STATUS_PROCESSED,
            **_terminal_correlations(obj, attempt),
        )
        if created:
            _send_review_alert(
                event_id=event_id,
                livemode=livemode,
                attempt=attempt,
                review=outcome,
            )
        return Attempt.OUTCOME_REVIEW_REQUIRED, 200

    # Clean terminal outcome (processed / ignored_stale).
    finalize_attempt(attempt, outcome=outcome, http_status=200)
    record_processed_event(
        event_id,
        event_type,
        {},
        status=WebhookEvent.STATUS_PROCESSED,
        **_terminal_correlations(obj, attempt),
    )
    return outcome, 200
