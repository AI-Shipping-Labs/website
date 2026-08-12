"""Cohort-wide Stripe subscription reconciliation service (issue #1308).

Compares live Stripe subscription truth against local website membership for
every current or former Stripe subscriber across Basic, Main, and Premium.

Design goals:

- Query Stripe with ``status=all`` and expose the EXACT live status, so
  dunning states (``past_due`` / ``unpaid`` / ``incomplete``) can never
  collapse into a false "no active subscription" and no merely not-active
  result can downgrade a member.
- Distinguish a SCHEDULED cancellation (``cancel_at_period_end=true``) from an
  already-ENDED (``canceled``) subscription.
- Read-only by default. Diagnostic runs and the daily schedule never mutate
  ``User`` / ``TierOverride`` / tags / community state.
- The confirmed apply path performs only deterministic repairs
  (active/trialing metadata, scheduled-cancellation metadata, and confirmed
  ``canceled`` reversion via the shared ended-subscription transition) and
  preserves active overrides + community access.

Tests use mocked Stripe: they patch
``payments.services.subscription_reconciliation.stripe`` (or the module-level
``_retrieve_subscription`` / ``_list_subscriptions_for_customer`` helpers).
The service NEVER contacts live Stripe in tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import stripe
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import TierOverride, User
from integrations.config import get_config
from payments.models import (
    StripeWebhookDeliveryAttempt,
    SubscriptionReconciliationFinding,
    SubscriptionReconciliationRun,
    WebhookEvent,
)
from payments.services import tier_resolution as _tier_resolution
from payments.services.import_stripe import CONFIGURATION_ERRORS, _price_to_tier_map
from payments.services.stripe_client import (
    _subscription_period_end,
    _subscription_price_id,
)
from payments.services.stripe_tags import reconcile_stripe_status_tags
from payments.services.subscription_transition import apply_ended_subscription

# ---------------------------------------------------------------------------
# Classifications and actions (stable, machine-readable).
# ---------------------------------------------------------------------------

CLASSIFICATION_OK = "in_sync"
CLASSIFICATION_ACTIVE_METADATA = "active_metadata_drift"
CLASSIFICATION_SCHEDULED = "scheduled_cancellation"
CLASSIFICATION_ENDED = "ended_subscription_still_entitled"
CLASSIFICATION_SUSPECTED_MISSED_DELETE = "suspected_missed_subscription_deleted"
CLASSIFICATION_DUNNING = "dunning_grace"
CLASSIFICATION_MONTHLY_GRACE_ACTIVE = "monthly_payment_grace_active"
CLASSIFICATION_MONTHLY_GRACE_DUE = "monthly_payment_grace_due"
CLASSIFICATION_MONTHLY_GRACE_REVIEW = "monthly_payment_grace_review"
CLASSIFICATION_NON_ENTITLED_REVIEW = "non_entitled_status_review"
CLASSIFICATION_MISSING_SUBSCRIPTION = "missing_stripe_subscription"
CLASSIFICATION_MISSING_LINK = "missing_stripe_link"
CLASSIFICATION_INCONSISTENT_TAGS = "inconsistent_stripe_status_tags"
CLASSIFICATION_DUPLICATE_OWNERSHIP = "duplicate_stripe_ownership"
CLASSIFICATION_AMBIGUOUS_SUBSCRIPTION = "ambiguous_subscription"
CLASSIFICATION_UNKNOWN_PRICE = "unknown_price"
CLASSIFICATION_LOOKUP_ERROR = "lookup_error"

ACTION_NOOP = "noop"
ACTION_REVERT_TO_FREE = "revert_to_free"
ACTION_REPAIR_ACTIVE_METADATA = "repair_active_metadata"
ACTION_REPAIR_SCHEDULED_CANCELLATION = "repair_scheduled_cancellation"
ACTION_REVIEW = "review"

# Deterministic classifications that a confirmed apply may write.
_APPLYABLE_CLASSIFICATIONS = frozenset({
    CLASSIFICATION_ACTIVE_METADATA,
    CLASSIFICATION_SCHEDULED,
    CLASSIFICATION_ENDED,
    CLASSIFICATION_SUSPECTED_MISSED_DELETE,
})

# Classifications counted as "actionable" drift (used for run counts + alerts).
ACTIONABLE_CLASSIFICATIONS = frozenset({
    CLASSIFICATION_ACTIVE_METADATA,
    CLASSIFICATION_ENDED,
    CLASSIFICATION_SUSPECTED_MISSED_DELETE,
    CLASSIFICATION_DUPLICATE_OWNERSHIP,
})

_ACTIVE_STATUSES = frozenset({"active", "trialing"})
_DUNNING_STATUSES = frozenset({"past_due", "unpaid"})
_NON_ENTITLED_REVIEW_STATUSES = frozenset({
    "incomplete", "incomplete_expired", "paused",
})
_ENDED_STATUSES = frozenset({"canceled"})

_PAID_TIER_SLUGS = frozenset({"basic", "main", "premium"})

Finding = SubscriptionReconciliationFinding
Run = SubscriptionReconciliationRun


# ---------------------------------------------------------------------------
# In-memory classification result.
# ---------------------------------------------------------------------------


@dataclass
class ClassificationResult:
    user: User
    classification: str
    action: str = ACTION_NOOP
    message: str = ""
    # Live Stripe truth.
    stripe_subscription_id: str = ""
    stripe_status: str = ""
    cancel_at_period_end: bool = False
    stripe_period_end: object = None
    stripe_tier_slug: str = ""
    stripe_tier: object = None
    # Webhook evidence.
    webhook_event_id: str = ""
    webhook_event_type: str = ""
    webhook_event_status: str = ""
    webhook_processed_at: object = None
    webhook_evidence: str = Finding.WEBHOOK_NOT_APPLICABLE
    # Duplicate ownership.
    conflicting_user_ids: list = field(default_factory=list)
    effective_tier_slug: str = ""
    latest_invoice_id: str = ""
    latest_invoice_status: str = ""
    latest_invoice_paid: bool | None = None
    latest_invoice_created: object = None
    collection_method: str = ""
    interval: str = ""
    interval_count: int | None = None
    payment_grace: object = None

    @property
    def is_ok(self):
        return self.classification == CLASSIFICATION_OK

    @property
    def is_scheduled(self):
        return self.classification == CLASSIFICATION_SCHEDULED

    @property
    def is_actionable(self):
        return self.classification in ACTIONABLE_CLASSIFICATIONS

    @property
    def is_warning(self):
        return self.classification in {
            CLASSIFICATION_DUNNING,
            CLASSIFICATION_MONTHLY_GRACE_REVIEW,
            CLASSIFICATION_NON_ENTITLED_REVIEW,
            CLASSIFICATION_MISSING_SUBSCRIPTION,
            CLASSIFICATION_MISSING_LINK,
            CLASSIFICATION_INCONSISTENT_TAGS,
            CLASSIFICATION_AMBIGUOUS_SUBSCRIPTION,
            CLASSIFICATION_UNKNOWN_PRICE,
            CLASSIFICATION_LOOKUP_ERROR,
        }


# ---------------------------------------------------------------------------
# Cohort selection.
# ---------------------------------------------------------------------------


def cohort_queryset():
    """Every local user with any indicator of Stripe membership.

    Includes: a non-empty ``stripe_customer_id`` OR ``subscription_id``, a
    paid base tier (Basic/Main/Premium), or a ``stripe:active`` /
    ``stripe:churned`` / ``stripe:plan-*`` tag. Paid rows whose Stripe link is
    missing are deliberately included so they surface as ``missing_stripe_link``.
    """
    # DB-filterable indicators (portable across SQLite + Postgres): a non-empty
    # Stripe customer/subscription id, or a paid base tier.
    stripe_indicator = (
        ~Q(stripe_customer_id="")
        | ~Q(subscription_id="")
        | Q(tier__slug__in=_PAID_TIER_SLUGS)
    )
    base_ids = set(
        User.objects.filter(stripe_indicator).values_list("pk", flat=True)
    )
    # Tag indicators (``stripe:active`` / ``stripe:churned`` / ``stripe:plan-*``)
    # are matched in Python over the small tagged population — JSONField
    # ``contains`` / prefix lookups are not portable to SQLite.
    for user in User.objects.exclude(tags=[]).only("pk", "tags"):
        tags = user.tags or []
        if any(
            t == "stripe:active"
            or t == "stripe:churned"
            or str(t).startswith("stripe:plan-")
            for t in tags
        ):
            base_ids.add(user.pk)
    return (
        User.objects.filter(pk__in=base_ids)
        .select_related("tier", "pending_tier")
        .order_by("email")
    )


# ---------------------------------------------------------------------------
# Live Stripe truth resolution.
# ---------------------------------------------------------------------------


def _secret_key():
    return get_config("STRIPE_SECRET_KEY", "")


def _retrieve_subscription(subscription_id):
    """Retrieve one subscription by id with price data expanded.

    Returns the subscription payload, or ``None`` when Stripe reports it does
    not exist. Configuration/transport errors propagate.
    """
    try:
        return stripe.Subscription.retrieve(
            subscription_id,
            api_key=_secret_key(),
            expand=["items.data.price", "latest_invoice"],
        )
    except stripe.InvalidRequestError as exc:
        if getattr(exc, "code", None) == "resource_missing" or getattr(
            exc, "http_status", None
        ) == 404:
            return None
        raise


def _list_subscriptions_for_customer(customer_id):
    """List ALL subscriptions for a customer (``status=all``), newest first."""
    response = stripe.Subscription.list(
        api_key=_secret_key(),
        customer=customer_id,
        status="all",
        limit=100,
        expand=["data.items.data.price", "data.latest_invoice"],
    )
    subscriptions = list(response.auto_paging_iter())
    subscriptions.sort(
        key=lambda s: _get(s, "current_period_end") or 0,
        reverse=True,
    )
    return subscriptions


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


@dataclass
class _StripeState:
    found: bool = False
    subscription: object = None
    status: str = ""
    subscription_id: str = ""
    cancel_at_period_end: bool = False
    period_end: object = None
    ambiguous: bool = False
    lookup_error: str = ""
    latest_invoice: object = None


def _resolve_stripe_state(user):
    """Resolve the authoritative Stripe subscription for a user.

    Uses the stored ``subscription_id`` when present; otherwise lists all of
    the customer's subscriptions (``status=all``). Selects the most relevant
    subscription: prefer an entitled (active/trialing) one; a single entitled
    subscription among several is authoritative, more than one entitled is
    ambiguous.
    """
    try:
        if user.subscription_id:
            sub = _retrieve_subscription(user.subscription_id)
            if sub is not None:
                return _state_from_subscription(sub)
            # Stored subscription no longer exists — fall through to a customer
            # listing so a re-subscribe under a new id is still discovered.
        if user.stripe_customer_id:
            subs = _list_subscriptions_for_customer(user.stripe_customer_id)
            return _select_state(subs)
    except CONFIGURATION_ERRORS as exc:
        message = str(exc).splitlines()[0] or exc.__class__.__name__
        return _StripeState(lookup_error=message)
    return _StripeState(found=False)


def _select_state(subscriptions):
    if not subscriptions:
        return _StripeState(found=False)
    entitled = [
        s for s in subscriptions
        if _get(s, "status", "") in _ACTIVE_STATUSES
    ]
    if len(entitled) > 1:
        state = _state_from_subscription(entitled[0])
        state.ambiguous = True
        return state
    if len(entitled) == 1:
        return _state_from_subscription(entitled[0])
    # No entitled subscription; the newest (already sorted) is authoritative
    # for showing the exact live status (e.g. canceled / past_due).
    return _state_from_subscription(subscriptions[0])


def _state_from_subscription(sub):
    return _StripeState(
        found=True,
        subscription=sub,
        status=_get(sub, "status", "") or "",
        subscription_id=_get(sub, "id", "") or "",
        cancel_at_period_end=bool(_get(sub, "cancel_at_period_end", False)),
        period_end=_subscription_period_end(sub),
        latest_invoice=_get(sub, "latest_invoice", None),
    )


# ---------------------------------------------------------------------------
# Webhook evidence correlation.
# ---------------------------------------------------------------------------


def _correlate_deletion_evidence(subscription_id, customer_id):
    """Return latest deletion-webhook evidence for a subscription/customer.

    Returns a tuple ``(event_id, event_type, event_status, processed_at,
    evidence)``. Reads ``StripeWebhookDeliveryAttempt`` (durable, secret-free)
    for the latest ``customer.subscription.deleted`` delivery.
    """
    qs = StripeWebhookDeliveryAttempt.objects.filter(
        event_type="customer.subscription.deleted",
    )
    filters = Q()
    if subscription_id:
        filters |= Q(stripe_subscription_id=subscription_id)
    if customer_id:
        filters |= Q(stripe_customer_id=customer_id)
    if not filters:
        return "", "", "", None, Finding.WEBHOOK_MISSING
    attempt = qs.filter(filters).order_by("-received_at").first()
    if attempt is None:
        return "", "", "", None, Finding.WEBHOOK_MISSING
    if attempt.outcome == StripeWebhookDeliveryAttempt.OUTCOME_PROCESSED:
        evidence = Finding.WEBHOOK_PROCESSED
    elif attempt.outcome in {
        StripeWebhookDeliveryAttempt.OUTCOME_FAILED_PERMANENT,
        StripeWebhookDeliveryAttempt.OUTCOME_AMBIGUOUS_USER,
    }:
        evidence = Finding.WEBHOOK_FAILED_PERMANENT
    else:
        evidence = Finding.WEBHOOK_MISSING
    return (
        attempt.stripe_event_id,
        attempt.event_type,
        attempt.outcome,
        attempt.finished_at or attempt.received_at,
        evidence,
    )


# ---------------------------------------------------------------------------
# Per-user classification.
# ---------------------------------------------------------------------------


def _tier_slug(user):
    return user.tier.slug if user.tier_id and user.tier else "free"


def _has_paid_tier(user):
    return _tier_slug(user) in _PAID_TIER_SLUGS


def _inconsistent_status_tags(user):
    tags = set(user.tags or [])
    return "stripe:active" in tags and "stripe:churned" in tags


def classify_user(user, *, price_to_tier=None, duplicate_ids=None):
    """Classify one cohort user against live Stripe truth."""
    price_to_tier = price_to_tier if price_to_tier is not None else _price_to_tier_map()
    duplicate_ids = duplicate_ids or set()

    result = ClassificationResult(user=user, classification=CLASSIFICATION_OK)
    from payments.services.monthly_payment_grace import _effective_tier
    effective = _effective_tier(user)
    result.effective_tier_slug = effective.slug if effective else "free"

    # Duplicate ownership is decided BEFORE any Stripe-driven classification;
    # it is never eligible for apply.
    if user.pk in duplicate_ids:
        result.classification = CLASSIFICATION_DUPLICATE_OWNERSHIP
        result.action = ACTION_REVIEW
        result.conflicting_user_ids = sorted(duplicate_ids.get(user.pk, []))
        result.message = (
            "Stripe customer/subscription id is shared by more than one local "
            "user; resolve identity ownership before any apply."
        )
        return result

    state = _resolve_stripe_state(user)

    if state.lookup_error:
        result.classification = CLASSIFICATION_LOOKUP_ERROR
        result.action = ACTION_REVIEW
        result.message = f"Stripe lookup failed: {state.lookup_error}"
        return result

    # Populate live truth on the result for reporting.
    if state.found:
        result.stripe_subscription_id = state.subscription_id
        result.stripe_status = state.status
        result.cancel_at_period_end = state.cancel_at_period_end
        result.stripe_period_end = state.period_end
        tier = _resolve_tier(state.subscription, price_to_tier)
        if tier is not None:
            result.stripe_tier = tier
            result.stripe_tier_slug = tier.slug
        invoice = state.latest_invoice
        if invoice is not None and not isinstance(invoice, str):
            from payments.services.monthly_payment_grace import _utc_timestamp
            result.latest_invoice_id = _get(invoice, "id", "") or ""
            result.latest_invoice_status = _get(invoice, "status", "") or ""
            result.latest_invoice_paid = bool(_get(invoice, "paid", False))
            result.latest_invoice_created = _utc_timestamp(_get(invoice, "created"))
            result.collection_method = _get(invoice, "collection_method", "") or ""
        items = list(_get(_get(state.subscription, "items", {}) or {}, "data", []) or [])
        if len(items) == 1:
            recurring = _get(_get(items[0], "price", {}) or {}, "recurring", {}) or {}
            result.interval = _get(recurring, "interval", "") or ""
            try:
                result.interval_count = int(_get(recurring, "interval_count", 1) or 1)
            except (TypeError, ValueError):
                result.interval_count = None

    # No Stripe subscription/history found.
    if not state.found:
        if _has_paid_tier(user) or user.subscription_id:
            result.classification = CLASSIFICATION_MISSING_SUBSCRIPTION
            result.action = ACTION_REVIEW
            result.message = (
                "No Stripe subscription/history found for a linked or paid "
                "local user; not enough evidence to infer cancellation."
            )
            return result
        # A churned-only record with no live subscription is in sync.
        result.classification = CLASSIFICATION_OK
        return result

    # Ambiguous: multiple entitled subscriptions.
    if state.ambiguous:
        result.classification = CLASSIFICATION_AMBIGUOUS_SUBSCRIPTION
        result.action = ACTION_REVIEW
        result.message = (
            "Customer has more than one entitled Stripe subscription; "
            "operator review required."
        )
        return result

    status = state.status

    # Active / trialing.
    if status in _ACTIVE_STATUSES:
        return _classify_active(user, state, result)

    # Dunning / grace — NEVER a cancellation, NEVER a downgrade.
    if status in _DUNNING_STATUSES:
        from payments.models import MonthlyPaymentGrace
        from payments.services.monthly_payment_grace import qualify_monthly_failure
        grace = MonthlyPaymentGrace.objects.filter(
            user=user,
            status__in=[MonthlyPaymentGrace.STATUS_ACTIVE, MonthlyPaymentGrace.STATUS_REVIEW],
        ).first()
        result.payment_grace = grace
        qualification = qualify_monthly_failure(
            state.latest_invoice or {}, state.subscription,
            price_to_tier=price_to_tier,
        )
        if qualification.eligible:
            due = bool(grace and timezone.now() >= grace.effective_expires_at)
            result.classification = (
                CLASSIFICATION_MONTHLY_GRACE_DUE if due
                else CLASSIFICATION_MONTHLY_GRACE_ACTIVE
            )
            result.action = "payment_grace_sweep" if due else "payment_grace_observe"
            result.message = (
                "Eligible unpaid monthly membership invoice; payment grace "
                "is due." if due else
                "Eligible unpaid monthly membership invoice; payment grace is active."
            )
        else:
            result.classification = CLASSIFICATION_MONTHLY_GRACE_REVIEW
            result.action = ACTION_REVIEW
            result.message = f"Payment grace excluded: {qualification.message}"
        return result

    # Non-entitled review states.
    if status in _NON_ENTITLED_REVIEW_STATUSES:
        result.classification = CLASSIFICATION_NON_ENTITLED_REVIEW
        result.action = ACTION_REVIEW
        result.message = (
            f"Live Stripe status {status} has no defined entitlement "
            "transition; operator review required."
        )
        return result

    # Ended / canceled.
    if status in _ENDED_STATUSES:
        return _classify_ended(user, state, result)

    # Unknown status — surface exactly, do not act.
    result.classification = CLASSIFICATION_NON_ENTITLED_REVIEW
    result.action = ACTION_REVIEW
    result.message = f"Unexpected live Stripe status {status}; review required."
    return result


def _resolve_tier(subscription, price_to_tier):
    if subscription is None:
        return None
    price_id = _subscription_price_id(subscription)
    return _tier_resolution.resolve_subscription_tier(
        subscription, price_id, price_to_tier
    )


def _classify_active(user, state, result):
    tier = result.stripe_tier
    if tier is None:
        result.classification = CLASSIFICATION_UNKNOWN_PRICE
        result.action = ACTION_REVIEW
        result.message = (
            "Active Stripe subscription uses an unknown/unmapped price; "
            "tier unchanged."
        )
        return result

    if state.cancel_at_period_end:
        # Scheduled cancellation: keep paid access + subscription_id through
        # period end; desired pending_tier=free + billing_period_end.
        result.classification = CLASSIFICATION_SCHEDULED
        result.action = ACTION_REPAIR_SCHEDULED_CANCELLATION
        result.message = (
            "Subscription is scheduled to cancel at period end; access "
            "continues until then."
        )
        return result

    # Entitled now. Desired state is the mapped paid base tier + subscription
    # metadata, pending_tier cleared. Flag metadata drift if any diverges.
    drift = (
        user.tier_id != tier.pk
        or (state.subscription_id and user.subscription_id != state.subscription_id)
        or (state.period_end and user.billing_period_end != state.period_end)
        or user.pending_tier_id is not None
    )
    if not drift:
        result.classification = CLASSIFICATION_OK
        return result
    # Report tag contradiction as its own thing even while active.
    if _inconsistent_status_tags(user):
        result.classification = CLASSIFICATION_INCONSISTENT_TAGS
        result.action = ACTION_REVIEW
        result.message = (
            "Simultaneous stripe:active and stripe:churned tags; the "
            "contradiction is classification drift, not cancellation."
        )
        return result
    result.classification = CLASSIFICATION_ACTIVE_METADATA
    result.action = ACTION_REPAIR_ACTIVE_METADATA
    result.message = (
        f"Active {tier.slug} subscription but local membership metadata is "
        "stale; repair tier/subscription metadata."
    )
    return result


def _classify_ended(user, state, result):
    still_entitled = _has_paid_tier(user) or bool(user.subscription_id)
    if not still_entitled:
        # Already free locally; the ended subscription matches website truth.
        result.classification = CLASSIFICATION_OK
        return result

    # Correlate deletion-webhook evidence to distinguish state drift from a
    # missed/failed webhook.
    (
        result.webhook_event_id,
        result.webhook_event_type,
        result.webhook_event_status,
        result.webhook_processed_at,
        result.webhook_evidence,
    ) = _correlate_deletion_evidence(
        state.subscription_id or user.subscription_id,
        user.stripe_customer_id,
    )

    result.action = ACTION_REVERT_TO_FREE
    if result.webhook_evidence == Finding.WEBHOOK_PROCESSED:
        result.classification = CLASSIFICATION_ENDED
        result.message = (
            "Stripe subscription is canceled but the website still grants a "
            "paid tier; revert to Free."
        )
    else:
        result.classification = CLASSIFICATION_SUSPECTED_MISSED_DELETE
        evidence_note = (
            "the deletion webhook failed permanently"
            if result.webhook_evidence == Finding.WEBHOOK_FAILED_PERMANENT
            else "no local deletion webhook was processed"
        )
        result.message = (
            f"Stripe subscription is canceled but {evidence_note}; revert to "
            "Free."
        )
    return result


# ---------------------------------------------------------------------------
# Duplicate ownership detection.
# ---------------------------------------------------------------------------


def _duplicate_ownership_map(users):
    """Map user id -> conflicting user ids for shared customer/subscription."""
    by_customer = {}
    by_subscription = {}
    for user in users:
        cust = (user.stripe_customer_id or "").strip()
        sub = (user.subscription_id or "").strip()
        if cust:
            by_customer.setdefault(cust, []).append(user.pk)
        if sub:
            by_subscription.setdefault(sub, []).append(user.pk)

    conflicts = {}

    def _record(group):
        if len(group) < 2:
            return
        for uid in group:
            conflicts.setdefault(uid, set()).update(group)

    for group in by_customer.values():
        _record(group)
    for group in by_subscription.values():
        _record(group)

    return {uid: sorted(peers) for uid, peers in conflicts.items()}


# ---------------------------------------------------------------------------
# Run orchestration.
# ---------------------------------------------------------------------------


def run_reconciliation(*, mode=Run.MODE_DIAGNOSTIC, source=Run.SOURCE_SCHEDULED,
                       requested_by=None, users=None, confirm=False):
    """Execute a cohort reconciliation run and persist a run + findings.

    ``mode=diagnostic`` is read-only. ``mode=apply`` performs deterministic
    writes ONLY when ``confirm`` is True; otherwise apply degrades to a
    dry-run (would_change) with no writes.

    Returns the persisted :class:`SubscriptionReconciliationRun`.
    """
    run = Run.objects.create(
        status=Run.STATUS_QUEUED,
        mode=mode,
        source=source,
        requested_by=requested_by,
        started_at=timezone.now(),
    )
    return execute_run(run, users=users, confirm=confirm)


def execute_run(run, *, users=None, confirm=False):
    """Execute an already-created (queued) run in place. Returns the run.

    A Stripe auth/config failure fails the run and writes no membership
    changes. Marks the run running while it works so a concurrent scheduled
    run can detect it and exit.
    """
    run.status = Run.STATUS_RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])
    try:
        _execute_run(run, users=users, confirm=confirm)
    except CONFIGURATION_ERRORS as exc:
        run.status = Run.STATUS_FAILED
        run.finished_at = timezone.now()
        run.error_message = str(exc).splitlines()[0] or exc.__class__.__name__
        run.save(update_fields=["status", "finished_at", "error_message"])
        return run
    run.status = Run.STATUS_COMPLETED
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "finished_at"])
    return run


def _execute_run(run, *, users, confirm):
    price_to_tier = _price_to_tier_map()
    cohort = list(users) if users is not None else list(cohort_queryset())
    duplicate_ids = _duplicate_ownership_map(cohort)

    cohort_count = len(cohort)
    ok_count = 0
    scheduled_count = 0
    actionable_count = 0
    warning_count = 0
    changed_count = 0

    is_apply = run.mode == Run.MODE_APPLY and confirm

    for user in cohort:
        try:
            result = classify_user(
                user, price_to_tier=price_to_tier, duplicate_ids=duplicate_ids,
            )
        except Exception as exc:  # per-user transient error; continue cohort
            warning_count += 1
            _persist_finding(
                run,
                _error_result(user, exc),
                outcome=Finding.OUTCOME_ERROR,
            )
            continue

        if result.is_ok:
            ok_count += 1
            continue

        if result.is_scheduled:
            scheduled_count += 1
        elif result.is_actionable:
            actionable_count += 1
        elif result.is_warning:
            warning_count += 1

        outcome = _apply_or_preview(run, result, is_apply=is_apply)
        if outcome == Finding.OUTCOME_CHANGED:
            changed_count += 1
        _persist_finding(run, result, outcome=outcome)

    run.cohort_count = cohort_count
    run.ok_count = ok_count
    run.scheduled_cancellation_count = scheduled_count
    run.actionable_count = actionable_count
    run.warning_count = warning_count
    run.changed_count = changed_count
    run.save(update_fields=[
        "cohort_count", "ok_count", "scheduled_cancellation_count",
        "actionable_count", "warning_count", "changed_count",
    ])


def _apply_or_preview(run, result, *, is_apply):
    """Return the finding outcome, applying deterministic drift when allowed."""
    if result.classification not in _APPLYABLE_CLASSIFICATIONS:
        # Warnings/reviews/duplicates are always skipped for writes.
        if result.is_warning or result.classification == \
                CLASSIFICATION_DUPLICATE_OWNERSHIP:
            return Finding.OUTCOME_WARNING if result.is_warning \
                else Finding.OUTCOME_REPORTED
        return Finding.OUTCOME_REPORTED

    if not is_apply:
        return Finding.OUTCOME_WOULD_CHANGE

    apply_deterministic(result)
    return Finding.OUTCOME_CHANGED


def apply_deterministic(result):
    """Apply the deterministic repair described by ``result`` (writes)."""
    user = result.user
    with transaction.atomic():
        locked = User.objects.select_for_update().get(pk=user.pk)
        if result.classification in {
            CLASSIFICATION_ENDED, CLASSIFICATION_SUSPECTED_MISSED_DELETE,
        }:
            apply_ended_subscription(locked)
        elif result.classification == CLASSIFICATION_SCHEDULED:
            _apply_scheduled_repair(locked, result)
        elif result.classification == CLASSIFICATION_ACTIVE_METADATA:
            _apply_active_repair(locked, result)
    _write_audit(result)
    # Refresh the in-memory user so callers/serializers see written state.
    user.refresh_from_db()


def _apply_scheduled_repair(user, result):
    from payments.models import Tier
    fields = []
    if user.pending_tier_id is None or (
        user.pending_tier and user.pending_tier.slug != "free"
    ):
        user.pending_tier = Tier.objects.filter(slug="free").first()
        fields.append("pending_tier")
    if result.stripe_period_end and user.billing_period_end != result.stripe_period_end:
        user.billing_period_end = result.stripe_period_end
        fields.append("billing_period_end")
    if result.stripe_subscription_id and user.subscription_id != result.stripe_subscription_id:
        user.subscription_id = result.stripe_subscription_id
        fields.append("subscription_id")
    if fields:
        user.save(update_fields=fields)


def _apply_active_repair(user, result):
    tier = result.stripe_tier
    fields = []
    if tier is not None and user.tier_id != tier.pk:
        user.tier = tier
        fields.append("tier")
    if result.stripe_subscription_id and user.subscription_id != result.stripe_subscription_id:
        user.subscription_id = result.stripe_subscription_id
        fields.append("subscription_id")
    if result.stripe_period_end and user.billing_period_end != result.stripe_period_end:
        user.billing_period_end = result.stripe_period_end
        fields.append("billing_period_end")
    if user.pending_tier_id is not None:
        user.pending_tier = None
        fields.append("pending_tier")
    if fields:
        user.save(update_fields=fields)
    # An active subscription is "active on tier" — resync status tags + retire
    # a now-redundant override.
    if tier is not None:
        reconcile_stripe_status_tags(user, active=True, tier=tier)
        _retire_redundant_override(user, tier)


def _retire_redundant_override(user, tier):
    override = (
        TierOverride.objects
        .filter(
            user_id=user.pk,
            override_tier=tier,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    if override is not None:
        override.is_active = False
        override.save(update_fields=["is_active"])


def _write_audit(result):
    """Write a secret-free audit event for an applied change."""
    user = result.user
    event_id = (
        f"subscription_reconciliation:user:{user.pk}:"
        f"{timezone.now().timestamp()}"
    )
    payload = {
        "user_id": user.pk,
        "email": user.email,
        "classification": result.classification,
        "action": result.action,
        "stripe_status": result.stripe_status,
        "stripe_subscription_id": result.stripe_subscription_id,
        "stripe_tier": result.stripe_tier_slug,
        "old_subscription_id": user.subscription_id,
        "source": "subscription_reconciliation",
    }
    WebhookEvent.objects.create(
        stripe_event_id=event_id,
        event_type="subscription_reconciliation_apply",
        payload=payload,
    )
    return event_id


def _error_result(user, exc):
    message = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    return ClassificationResult(
        user=user,
        classification=CLASSIFICATION_LOOKUP_ERROR,
        action=ACTION_REVIEW,
        message=f"Per-user error: {message}",
    )


def _persist_finding(run, result, *, outcome):
    user = result.user
    Finding.objects.create(
        run=run,
        user=user,
        email=user.email,
        current_tier=_tier_slug(user),
        current_subscription_id=user.subscription_id or "",
        stripe_customer_id=user.stripe_customer_id or "",
        stripe_subscription_id=result.stripe_subscription_id,
        stripe_status=result.stripe_status,
        cancel_at_period_end=result.cancel_at_period_end,
        stripe_period_end=result.stripe_period_end,
        stripe_tier=result.stripe_tier_slug,
        effective_tier=result.effective_tier_slug,
        latest_invoice_id=result.latest_invoice_id,
        latest_invoice_status=result.latest_invoice_status,
        latest_invoice_paid=result.latest_invoice_paid,
        latest_invoice_created=result.latest_invoice_created,
        collection_method=result.collection_method,
        interval=result.interval,
        interval_count=result.interval_count,
        payment_grace=result.payment_grace,
        payment_grace_status=(result.payment_grace.status if result.payment_grace else ""),
        payment_grace_started_at=(result.payment_grace.grace_started_at if result.payment_grace else None),
        payment_grace_expires_at=(result.payment_grace.effective_expires_at if result.payment_grace else None),
        webhook_event_id=result.webhook_event_id,
        webhook_event_type=result.webhook_event_type,
        webhook_event_status=result.webhook_event_status,
        webhook_processed_at=result.webhook_processed_at,
        webhook_evidence=result.webhook_evidence,
        classification=result.classification,
        action=result.action,
        outcome=outcome,
        message=result.message,
        conflicting_user_ids=list(result.conflicting_user_ids),
    )
