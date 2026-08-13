"""Review-only Stripe refund and dispute classification (issue #1422).

The callbacks handled here are operational evidence, never entitlement
authority.  This module follows only safe Stripe ids from the verified event
snapshot, resolves an exact current membership owner when possible, and does
not write any member, access, grace, course, tag, or community state.
"""

import re
from dataclasses import dataclass

from payments.exceptions import (
    WebhookAmbiguousUserError,
    WebhookUnmatchedUserError,
)
from payments.services.stripe_client import _get_stripe_client
from payments.services.subscription_resolution import resolve_subscription_user

REFUND_EVENT_TYPES = frozenset({"charge.refunded"})
DISPUTE_EVENT_TYPES = frozenset({
    "charge.dispute.created",
    "charge.dispute.closed",
})
REVIEW_EVENT_TYPES = REFUND_EVENT_TYPES | DISPUTE_EVENT_TYPES
DISPUTE_STATUSES = frozenset({
    "warning_needs_response",
    "warning_under_review",
    "warning_closed",
    "needs_response",
    "under_review",
    "won",
    "lost",
})
STRIPE_ID_SUFFIX_RE = re.compile(r"[A-Za-z0-9_]+")


@dataclass
class RefundDisputeReview:
    event_type: str
    classification: str
    resolution: str
    charge_id: str = ""
    invoice_id: str = ""
    dispute_id: str = ""
    customer_id: str = ""
    subscription_id: str = ""
    amount: int | None = None
    currency: str = ""
    dispute_status: str = ""
    user: object | None = None

    @property
    def category(self):
        return "refund" if self.event_type in REFUND_EVENT_TYPES else "dispute"

    @property
    def safe_summary(self):
        parts = [
            f"classification={self.classification}",
            f"resolution={self.resolution}",
        ]
        if self.dispute_status:
            parts.append(f"dispute_status={self.dispute_status}")
        if self.amount is not None:
            parts.append(f"amount={self.amount}")
        if self.currency:
            parts.append(f"currency={self.currency}")
        return "; ".join(parts)


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def safe_stripe_id(value, prefix):
    """Return a bounded canonical Stripe id, or blank for unsafe input."""
    if isinstance(value, dict):
        value = value.get("id", "")
    elif value is not None and not isinstance(value, str):
        value = getattr(value, "id", "")
    value = str(value or "").strip()
    if not value.startswith(prefix) or len(value) > 255:
        return ""
    suffix = value[len(prefix):]
    if not suffix or STRIPE_ID_SUFFIX_RE.fullmatch(suffix) is None:
        return ""
    return value


def _safe_amount(value):
    if isinstance(value, bool):
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    return amount if amount >= 0 else None


def _safe_currency(value):
    value = str(value or "").strip().lower()
    return value if value.isalpha() and 3 <= len(value) <= 12 else ""


def _persist_ids(attempt, *, charge_id="", invoice_id="", dispute_id="",
                 customer_id="", subscription_id=""):
    updates = []
    for field, value in (
        ("stripe_charge_id", charge_id),
        ("stripe_invoice_id", invoice_id),
        ("stripe_dispute_id", dispute_id),
        ("stripe_customer_id", customer_id),
        ("stripe_subscription_id", subscription_id),
    ):
        if value and getattr(attempt, field) != value:
            setattr(attempt, field, value)
            updates.append(field)
    if updates:
        attempt.save(update_fields=updates)


def _resolve_owner(subscription_id, customer_id):
    try:
        resolution = resolve_subscription_user(subscription_id, customer_id)
    except WebhookAmbiguousUserError:
        return "ambiguous_local_owner", None
    except WebhookUnmatchedUserError:
        return "unmatched_local_owner", None
    if resolution.user is None:
        return "unmatched_local_owner", None
    return "exact_membership_owner", resolution.user


def _classify_refund(obj):
    amount = _safe_amount(_value(obj, "amount"))
    refunded_amount = _safe_amount(_value(obj, "amount_refunded"))
    refunded = _value(obj, "refunded")
    currency = _safe_currency(_value(obj, "currency"))
    if (
        not isinstance(refunded, bool)
        or amount is None
        or refunded_amount is None
        or not currency
        or amount <= 0
        or refunded_amount <= 0
        or refunded_amount > amount
    ):
        return "malformed_refund", amount, currency
    if refunded and refunded_amount == amount:
        return "full_refund", refunded_amount, currency
    if not refunded and refunded_amount < amount:
        return "partial_refund", refunded_amount, currency
    return "malformed_refund", refunded_amount, currency


def _classify_dispute(event_type, obj):
    amount = _safe_amount(_value(obj, "amount"))
    currency = _safe_currency(_value(obj, "currency"))
    raw_status = _value(obj, "status", "")
    status = raw_status.strip().lower() if isinstance(raw_status, str) else ""
    if status not in DISPUTE_STATUSES:
        status = ""
    if amount is None or not currency or not status:
        return "malformed_dispute", amount, currency, ""
    if event_type == "charge.dispute.created":
        return "dispute_created", amount, currency, status
    if status in {"won", "lost"}:
        return f"dispute_closed_{status}", amount, currency, status
    return "malformed_dispute", amount, currency, status


def classify_refund_or_dispute(event_type, obj, attempt):
    """Classify one verified refund/dispute snapshot without changing access.

    Stripe/configuration exceptions intentionally propagate.  The dispatcher
    records those as transient and omits terminal event-id evidence so Stripe
    can retry.
    """
    obj = obj or {}
    dispute_id = ""
    charge = obj

    if event_type in REFUND_EVENT_TYPES:
        charge_id = safe_stripe_id(_value(obj, "id"), "ch_")
        classification, amount, currency = _classify_refund(obj)
        dispute_status = ""
    else:
        dispute_id = safe_stripe_id(_value(obj, "id"), "dp_")
        charge_id = safe_stripe_id(_value(obj, "charge"), "ch_")
        classification, amount, currency, dispute_status = _classify_dispute(
            event_type, obj,
        )

    _persist_ids(
        attempt, charge_id=charge_id, dispute_id=dispute_id,
    )
    if not charge_id or (event_type in DISPUTE_EVENT_TYPES and not dispute_id):
        return RefundDisputeReview(
            event_type=event_type,
            classification=(
                classification
                if classification.startswith("malformed_")
                else f"malformed_{'refund' if event_type in REFUND_EVENT_TYPES else 'dispute'}"
            ),
            resolution="malformed_safe_reference",
            charge_id=charge_id,
            dispute_id=dispute_id,
            amount=amount,
            currency=currency,
            dispute_status=dispute_status,
        )

    if classification.startswith("malformed_"):
        return RefundDisputeReview(
            event_type=event_type,
            classification=classification,
            resolution="malformed_safe_reference",
            charge_id=charge_id,
            dispute_id=dispute_id,
            amount=amount,
            currency=currency,
            dispute_status=dispute_status,
        )

    client = None
    if event_type in DISPUTE_EVENT_TYPES:
        client = _get_stripe_client()
        charge = client.charges.retrieve(charge_id)

    retrieved_charge_id = safe_stripe_id(_value(charge, "id"), "ch_")
    if retrieved_charge_id != charge_id:
        return RefundDisputeReview(
            event_type=event_type,
            classification=f"malformed_{'refund' if event_type in REFUND_EVENT_TYPES else 'dispute'}",
            resolution="malformed_safe_reference",
            charge_id=charge_id,
            dispute_id=dispute_id,
            amount=amount,
            currency=currency,
            dispute_status=dispute_status,
        )

    raw_invoice = _value(charge, "invoice")
    invoice_id = safe_stripe_id(raw_invoice, "in_")
    customer_id = safe_stripe_id(_value(charge, "customer"), "cus_")
    _persist_ids(
        attempt,
        charge_id=charge_id,
        invoice_id=invoice_id,
        dispute_id=dispute_id,
        customer_id=customer_id,
    )
    if raw_invoice and not invoice_id:
        return RefundDisputeReview(
            event_type=event_type,
            classification=classification,
            resolution="malformed_safe_reference",
            charge_id=charge_id,
            dispute_id=dispute_id,
            customer_id=customer_id,
            amount=amount,
            currency=currency,
            dispute_status=dispute_status,
        )
    if not invoice_id:
        return RefundDisputeReview(
            event_type=event_type,
            classification=classification,
            resolution="non_membership_charge",
            charge_id=charge_id,
            dispute_id=dispute_id,
            customer_id=customer_id,
            amount=amount,
            currency=currency,
            dispute_status=dispute_status,
        )

    if client is None:
        client = _get_stripe_client()
    invoice = client.invoices.retrieve(invoice_id)
    if safe_stripe_id(_value(invoice, "id"), "in_") != invoice_id:
        return RefundDisputeReview(
            event_type=event_type,
            classification=classification,
            resolution="malformed_safe_reference",
            charge_id=charge_id,
            invoice_id=invoice_id,
            dispute_id=dispute_id,
            customer_id=customer_id,
            amount=amount,
            currency=currency,
            dispute_status=dispute_status,
        )

    raw_subscription = _value(invoice, "subscription")
    subscription_id = safe_stripe_id(raw_subscription, "sub_")
    invoice_customer_id = safe_stripe_id(_value(invoice, "customer"), "cus_")
    if invoice_customer_id:
        customer_id = invoice_customer_id
    _persist_ids(
        attempt,
        invoice_id=invoice_id,
        customer_id=customer_id,
        subscription_id=subscription_id,
    )
    if raw_subscription and not subscription_id:
        resolution, user = "malformed_safe_reference", None
    elif not subscription_id:
        resolution, user = "non_membership_charge", None
    else:
        resolution, user = _resolve_owner(subscription_id, customer_id)

    return RefundDisputeReview(
        event_type=event_type,
        classification=classification,
        resolution=resolution,
        charge_id=charge_id,
        invoice_id=invoice_id,
        dispute_id=dispute_id,
        customer_id=customer_id,
        subscription_id=subscription_id,
        amount=amount,
        currency=currency,
        dispute_status=dispute_status,
        user=user,
    )
