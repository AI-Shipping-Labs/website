"""Monthly failed-payment grace policy and delivery worker (issue #1413).

Stripe remains the payment authority.  This module stores only safe IDs and
bounded decisions; it never stores webhook payloads, payment methods, secrets,
or short-lived Customer Portal sessions.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from urllib.parse import urlparse

import stripe
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import User
from community.models import CommunityAuditLog
from content.access import LEVEL_MAIN, get_active_override, get_user_level
from email_app.services import EmailService
from integrations.config import get_config, site_base_url
from payments.exceptions import (
    WebhookAmbiguousUserError,
    WebhookPermanentError,
    WebhookUnmatchedUserError,
)
from payments.models import (
    MonthlyPaymentGrace as Grace,
)
from payments.models import (
    MonthlyPaymentGraceDelivery as Delivery,
)
from payments.models import (
    Tier,
)
from payments.services.import_stripe import CONFIGURATION_ERRORS, _price_to_tier_map
from payments.services.subscription_resolution import resolve_subscription_user

GRACE_HOURS = 168
REMINDER_HOURS = 48
CLAIM_TIMEOUT = timedelta(minutes=15)
MAX_DELIVERY_ATTEMPTS = 8
DUNNING_STATUSES = frozenset({"past_due", "unpaid"})
RECOVERY_STATUSES = frozenset({"active", "trialing"})
_MODE_UNSET = object()


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _id(value):
    return str(_get(value, "id", "") if not isinstance(value, str) else value)


def _utc_timestamp(value):
    try:
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def grace_mode():
    value = str(get_config("STRIPE_MONTHLY_PAYMENT_GRACE_MODE", "observe"))
    value = value.strip().lower()
    return value if value in {"observe", "enforce"} else "observe"


def _validated_team_email():
    # ``get_config`` intentionally treats blank DB values as absent. This
    # policy is stricter: an explicitly blank operator setting disables the
    # team delivery and must surface as a configuration error rather than
    # silently falling back to a different recipient.
    from integrations.models import IntegrationSetting

    configured = IntegrationSetting.objects.filter(
        key="PAYMENT_FAILURE_TEAM_EMAIL",
    ).values_list("value", flat=True).first()
    value = str(
        configured
        if configured is not None
        else get_config(
            "PAYMENT_FAILURE_TEAM_EMAIL",
            "team@aishippinglabs.com",
        )
    ).strip().lower()
    try:
        validate_email(value)
    except ValidationError:
        return ""
    return value


def safe_portal_url():
    """Return the stable configured HTTPS portal URL, never a bearer session."""
    value = str(get_config("STRIPE_CUSTOMER_PORTAL_URL", "") or "").strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or hostname != "billing.stripe.com"
        or not parsed.path.startswith("/p/login/")
        or parsed.query
        or parsed.fragment
    ):
        return ""
    return value


@dataclass
class Qualification:
    eligible: bool
    code: str = ""
    message: str = ""
    invoice_id: str = ""
    subscription_id: str = ""
    customer_id: str = ""
    interval: str = ""
    interval_count: int = 0
    tier: Tier | None = None
    invoice_created_at: datetime | None = None
    collection_method: str = ""


def qualify_monthly_failure(invoice, subscription, *, price_to_tier=None):
    """Validate the narrow automatic-grace billing contract."""
    invoice_id = _id(_get(invoice, "id", ""))
    invoice_subscription_id = _id(_get(invoice, "subscription", ""))
    live_subscription_id = _id(_get(subscription, "id", ""))
    invoice_customer_id = _id(_get(invoice, "customer", ""))
    live_customer_id = _id(_get(subscription, "customer", ""))
    subscription_id = invoice_subscription_id or live_subscription_id
    customer_id = invoice_customer_id or live_customer_id
    base = dict(
        invoice_id=invoice_id,
        subscription_id=subscription_id,
        customer_id=customer_id,
        collection_method=str(_get(invoice, "collection_method", "") or ""),
        invoice_created_at=_utc_timestamp(_get(invoice, "created")),
    )
    if not invoice_id or not invoice_subscription_id or not live_subscription_id:
        return Qualification(False, "missing_history", "Missing invoice/subscription ID", **base)
    if (
        invoice_subscription_id
        and live_subscription_id
        and invoice_subscription_id != live_subscription_id
    ) or (
        invoice_customer_id
        and live_customer_id
        and invoice_customer_id != live_customer_id
    ):
        return Qualification(
            False,
            "authority_mismatch",
            "Invoice and subscription authority do not match",
            **base,
        )
    if base["collection_method"] != "charge_automatically":
        return Qualification(False, "manual_collection", "Invoice is not automatically collected", **base)
    if bool(_get(invoice, "paid", False)) or str(_get(invoice, "status", "")) == "paid":
        return Qualification(False, "invoice_paid", "Invoice is already paid", **base)
    status = str(_get(subscription, "status", "") or "")
    if status not in DUNNING_STATUSES:
        return Qualification(False, "unsupported_status", f"Subscription status {status or 'missing'} is not eligible", **base)

    items = list(_get(_get(subscription, "items", {}) or {}, "data", []) or [])
    if len(items) != 1:
        return Qualification(False, "mixed_or_multiple_price", "Subscription must contain exactly one membership item", **base)
    price = _get(items[0], "price", {}) or {}
    price_id = _id(price)
    price_to_tier = price_to_tier if price_to_tier is not None else _price_to_tier_map()
    tier = price_to_tier.get(price_id)
    if tier is None or tier.slug == "free" or tier.level <= 0:
        return Qualification(False, "unknown_price", "Price is not mapped to a paid membership tier", **base)
    recurring = _get(price, "recurring", {}) or {}
    interval = str(_get(recurring, "interval", "") or "")
    try:
        interval_count = int(_get(recurring, "interval_count", 1) or 1)
    except (TypeError, ValueError):
        interval_count = 0
    base.update(interval=interval, interval_count=interval_count, tier=tier)
    if interval != "month" or interval_count != 1:
        return Qualification(False, "unsupported_interval", "Only one-month recurring membership invoices qualify", **base)
    return Qualification(True, **base)


def _effective_tier(user):
    """Return canonical strongest current membership tier (base or override)."""
    tier = user.tier
    # Source-specific courtesy grants may coexist.  The shared access resolver
    # orders them by tier strength (then expiry), matching ``get_user_level``;
    # choosing the newest row can under-report an older, stronger grant.
    override = get_active_override(user)
    if override and (tier is None or override.override_tier.level > tier.level):
        return override.override_tier
    return tier or Tier.objects.filter(slug="free").first()


def _audit(grace, action, *, actor, reason="", old_base=None, new_base=None,
           old_effective=None, new_effective=None, community_changed=False,
           tag_changes=None, run_id=""):
    payload = {
        "grace_id": str(grace.pk),
        "run_id": str(run_id or ""),
        "actor": actor,
        "reason": str(reason or "")[:500],
        "stripe_customer_id": grace.stripe_customer_id,
        "stripe_subscription_id": grace.stripe_subscription_id,
        "stripe_invoice_id": grace.stripe_invoice_id,
        "old_base_tier": getattr(old_base, "slug", old_base or ""),
        "new_base_tier": getattr(new_base, "slug", new_base or ""),
        "old_effective_tier": getattr(old_effective, "slug", old_effective or ""),
        "new_effective_tier": getattr(new_effective, "slug", new_effective or ""),
        "community_changed": bool(community_changed),
        "tag_changes": list(tag_changes or []),
        "at": timezone.now().isoformat(),
    }
    CommunityAuditLog.objects.create(
        user=grace.user,
        action=f"monthly_payment_grace_{action}",
        details=json.dumps(payload, sort_keys=True),
    )


def _initial_deliveries(grace):
    Delivery.objects.get_or_create(
        grace=grace,
        kind=Delivery.KIND_FAILURE_MEMBER,
        recipient=grace.user.email.strip().lower(),
    )
    team_email = _validated_team_email()
    if team_email:
        Delivery.objects.get_or_create(
            grace=grace,
            kind=Delivery.KIND_FAILURE_TEAM,
            recipient=team_email,
        )
    else:
        grace.last_error_code = "invalid_team_email"
        grace.last_error_message = "PAYMENT_FAILURE_TEAM_EMAIL is blank or invalid."
        grace.save(update_fields=["last_error_code", "last_error_message", "updated_at"])


def start_grace_from_failure(*, invoice, subscription, event_id="",
                             event_created=None, livemode=None,
                             source=Grace.SOURCE_WEBHOOK, run_id=""):
    """Start (or return) the one immutable grace for an unresolved invoice."""
    qualification = qualify_monthly_failure(invoice, subscription)
    if not qualification.eligible:
        return None, qualification
    resolution = resolve_subscription_user(
        qualification.subscription_id, qualification.customer_id,
    )
    if resolution.stale_customer_user is not None:
        qualification.eligible = False
        qualification.code = "stale_subscription"
        qualification.message = "Invoice belongs to a superseded subscription"
        return None, qualification
    user = resolution.user
    start = (
        _utc_timestamp(event_created)
        if source == Grace.SOURCE_WEBHOOK
        else qualification.invoice_created_at
    )
    if start is None:
        qualification.eligible = False
        qualification.code = "missing_authoritative_timestamp"
        qualification.message = "Stripe did not provide an authoritative created timestamp"
        return None, qualification

    with transaction.atomic():
        user = User.objects.select_for_update().select_related("tier").get(pk=user.pk)
        recovered = Grace.objects.filter(
            stripe_invoice_id=qualification.invoice_id,
            livemode=livemode,
            status=Grace.STATUS_RECOVERED,
        ).first()
        if recovered:
            return recovered, qualification
        existing_invoice = Grace.objects.filter(
            stripe_invoice_id=qualification.invoice_id, livemode=livemode,
        ).first()
        if existing_invoice:
            if event_id and not existing_invoice.last_failure_event_id:
                existing_invoice.last_failure_event_id = event_id[:255]
                existing_invoice.save(update_fields=["last_failure_event_id", "updated_at"])
            return existing_invoice, qualification
        active = Grace.objects.filter(
            user=user,
            status__in=[Grace.STATUS_ACTIVE, Grace.STATUS_REVIEW],
        ).first()
        if active:
            qualification.eligible = False
            qualification.code = "active_grace_exists"
            qualification.message = "A prior invoice still has active grace"
            return active, qualification
        if user.subscription_id != qualification.subscription_id:
            qualification.eligible = False
            qualification.code = "manual_subscription_change"
            qualification.message = "Local authoritative subscription changed"
            return None, qualification
        if user.tier_id != qualification.tier.pk:
            qualification.eligible = False
            qualification.code = "manual_tier_change"
            qualification.message = "Local base tier does not match Stripe"
            return None, qualification
        try:
            # Keep the insert in a savepoint so an invoice-uniqueness race
            # does not poison the surrounding transaction before we load the
            # winning row.
            with transaction.atomic():
                grace = Grace.objects.create(
                    user=user,
                    base_tier_at_start=qualification.tier,
                    stripe_customer_id=qualification.customer_id,
                    stripe_subscription_id=qualification.subscription_id,
                    stripe_invoice_id=qualification.invoice_id,
                    livemode=livemode,
                    source=source,
                    interval=qualification.interval,
                    interval_count=qualification.interval_count,
                    grace_started_at=start,
                    grace_expires_at=start + timedelta(hours=GRACE_HOURS),
                    policy_enforced_at=(
                        start if grace_mode() == "enforce" else None
                    ),
                    last_failure_event_id=(event_id or "")[:255],
                )
        except IntegrityError:
            grace = Grace.objects.get(
                stripe_invoice_id=qualification.invoice_id, livemode=livemode,
            )
        _initial_deliveries(grace)
        effective = _effective_tier(user)
        _audit(
            grace, "started", actor=("stripe_webhook" if source == Grace.SOURCE_WEBHOOK else "scheduled_reconciliation"),
            reason=source, old_base=user.tier, new_base=user.tier,
            old_effective=effective, new_effective=effective, run_id=run_id,
        )
    process_due_deliveries(grace_ids=[grace.pk], initial_only=True)
    return grace, qualification


def recover_grace(*, subscription_id, invoice_id="", event_id="",
                  event_created=None, livemode=_MODE_UNSET,
                  actor="stripe_webhook", run_id=""):
    """Atomically recover matching active/review grace and cancel unsent work."""
    filters = {"stripe_subscription_id": subscription_id}
    if invoice_id:
        filters["stripe_invoice_id"] = invoice_id
    if livemode is not _MODE_UNSET:
        # Stripe object ids may be reused between test and live mode.  Verified
        # webhook mode is therefore part of recovery authority, just as it is
        # part of the grace uniqueness key.
        filters["livemode"] = livemode
    with transaction.atomic():
        candidates = list(
            Grace.objects.select_for_update().select_related("base_tier_at_start")
            .filter(status__in=[Grace.STATUS_ACTIVE, Grace.STATUS_REVIEW], **filters)
            .order_by("grace_started_at", "pk")[:2]
        )
        if len(candidates) > 1:
            raise WebhookAmbiguousUserError(
                "Multiple payment graces match recovery authority",
                matched_by="payment_grace",
                subscription_id=subscription_id,
                user_ids=[candidate.user_id for candidate in candidates],
            )
        if not candidates:
            if livemode is not _MODE_UNSET:
                other_mode = Grace.objects.filter(
                    status__in=[Grace.STATUS_ACTIVE, Grace.STATUS_REVIEW],
                    stripe_subscription_id=subscription_id,
                    **({"stripe_invoice_id": invoice_id} if invoice_id else {}),
                ).exclude(livemode=livemode).exists()
                if other_mode:
                    raise WebhookPermanentError(
                        "Payment-grace recovery mode does not match verified event mode"
                    )
            return None
        grace = candidates[0]
        user = User.objects.select_for_update().select_related("tier").get(
            pk=grace.user_id,
        )
        grace.user = user
        now = timezone.now()
        recovered_at = _utc_timestamp(event_created) or now
        old_effective = _effective_tier(user)
        if user.subscription_id != grace.stripe_subscription_id:
            grace.status = Grace.STATUS_SUPERSEDED
            grace.last_checked_at = now
            grace.last_error_code = "manual_subscription_change"
            grace.last_error_message = (
                "Authoritative subscription changed before recovery"
            )
            grace.save(update_fields=[
                "status", "last_checked_at", "last_error_code",
                "last_error_message", "updated_at",
            ])
            Delivery.objects.filter(
                grace=grace,
                kind__in=[
                    Delivery.KIND_REMINDER_MEMBER,
                    Delivery.KIND_EXPIRED_MEMBER,
                ],
            ).exclude(status=Delivery.STATUS_SENT).delete()
            _audit(
                grace,
                "superseded",
                actor=actor,
                reason="Authoritative subscription changed before recovery",
                old_base=user.tier,
                new_base=user.tier,
                old_effective=old_effective,
                new_effective=old_effective,
                run_id=run_id,
            )
            return grace
        if user.tier_id != grace.base_tier_at_start_id:
            _review(
                grace,
                "manual_tier_change",
                "Base tier changed before payment recovery",
                actor=actor,
            )
            return grace
        resolution = resolve_subscription_user(
            grace.stripe_subscription_id,
            grace.stripe_customer_id,
        )
        if (
            resolution.stale_customer_user is not None
            or resolution.user.pk != grace.user_id
        ):
            _review(
                grace,
                "ownership_changed",
                "Unique local ownership no longer matches recovery",
                actor=actor,
            )
            return grace
        grace.status = Grace.STATUS_RECOVERED
        grace.recovered_at = recovered_at
        grace.recovery_event_id = (event_id or "")[:255]
        grace.last_checked_at = now
        grace.last_error_code = ""
        grace.last_error_message = ""
        grace.save(update_fields=[
            "status", "recovered_at", "recovery_event_id", "last_checked_at",
            "last_error_code", "last_error_message", "updated_at",
        ])
        Delivery.objects.filter(
            grace=grace,
            kind__in=[Delivery.KIND_REMINDER_MEMBER, Delivery.KIND_EXPIRED_MEMBER],
        ).exclude(status=Delivery.STATUS_SENT).delete()
        from payments.services.stripe_tags import reconcile_stripe_status_tags

        before_tags = set(grace.user.tags or [])
        if user.tier_id and user.tier.level > 0:
            reconcile_stripe_status_tags(
                user,
                active=True,
                tier=user.tier,
            )
        after_tags = set(user.tags or [])
        tag_changes = [
            *(f"removed:{tag}" for tag in sorted(before_tags - after_tags)),
            *(f"added:{tag}" for tag in sorted(after_tags - before_tags)),
        ]
        _audit(
            grace, "recovered", actor=actor, reason="Stripe payment recovered",
            old_base=user.tier, new_base=user.tier,
            old_effective=old_effective, new_effective=old_effective,
            tag_changes=tag_changes, run_id=run_id,
        )
        return grace


def _retrieve_subscription(subscription_id):
    return stripe.Subscription.retrieve(
        subscription_id,
        api_key=get_config("STRIPE_SECRET_KEY", ""),
        expand=["items.data.price", "latest_invoice"],
    )


def _retrieve_invoice(invoice_id):
    return stripe.Invoice.retrieve(
        invoice_id,
        api_key=get_config("STRIPE_SECRET_KEY", ""),
        expand=["subscription"],
    )


def _review(grace, code, message, *, actor="grace_sweep"):
    bounded_code = str(code or "review")[:64]
    bounded_message = str(message or "Review required")[:500]
    decision_changed = (
        grace.status != Grace.STATUS_REVIEW
        or grace.last_error_code != bounded_code
        or grace.last_error_message != bounded_message
    )
    grace.status = Grace.STATUS_REVIEW
    grace.last_checked_at = timezone.now()
    grace.last_error_code = bounded_code
    grace.last_error_message = bounded_message
    grace.save(update_fields=[
        "status", "last_checked_at", "last_error_code", "last_error_message",
        "updated_at",
    ])
    if decision_changed:
        effective = _effective_tier(grace.user)
        _audit(
            grace, "review", actor=actor,
            reason=f"{bounded_code}: {bounded_message}",
            old_base=grace.user.tier, new_base=grace.user.tier,
            old_effective=effective, new_effective=effective,
        )


def _restore_active(grace):
    """Clear a retryable review after authoritative evidence becomes safe."""
    if grace.status != Grace.STATUS_REVIEW:
        return
    grace.status = Grace.STATUS_ACTIVE
    grace.last_checked_at = timezone.now()
    grace.last_error_code = ""
    grace.last_error_message = ""
    grace.save(update_fields=[
        "status", "last_checked_at", "last_error_code", "last_error_message",
        "updated_at",
    ])
    effective = _effective_tier(grace.user)
    _audit(
        grace, "resumed", actor="grace_sweep",
        reason="Review resolved; monthly payment grace resumed",
        old_base=grace.user.tier, new_base=grace.user.tier,
        old_effective=effective, new_effective=effective,
    )


def _revalidate(grace):
    # ``_expire_locked`` reloads and locks ``grace.user`` immediately before
    # this call.  Check that local manual authority first, before any Stripe
    # ownership lookup can collapse a subscription edit into a generic stale
    # ownership result.
    if grace.user.subscription_id != grace.stripe_subscription_id:
        return None, None, "manual_subscription_change", "Authoritative subscription changed"
    if grace.user.tier_id != grace.base_tier_at_start_id:
        return None, None, "manual_tier_change", "Base tier changed while grace was active"
    try:
        subscription = _retrieve_subscription(grace.stripe_subscription_id)
        invoice = _retrieve_invoice(grace.stripe_invoice_id)
    except Exception as exc:
        return (
            None,
            None,
            "stripe_lookup_error",
            f"Stripe lookup failed ({exc.__class__.__name__})",
        )
    try:
        resolution = resolve_subscription_user(
            grace.stripe_subscription_id, grace.stripe_customer_id,
        )
    except (WebhookAmbiguousUserError, WebhookUnmatchedUserError) as exc:
        return None, None, "ownership_ambiguous", str(exc).splitlines()[0]
    if resolution.stale_customer_user is not None or resolution.user.pk != grace.user_id:
        return None, None, "ownership_changed", "Unique local ownership no longer matches"
    qualification = qualify_monthly_failure(invoice, subscription)
    if not qualification.eligible:
        if qualification.code == "invoice_paid" or str(_get(subscription, "status", "")) in RECOVERY_STATUSES:
            return subscription, invoice, "recovered", qualification.message
        return None, None, qualification.code, qualification.message
    if qualification.invoice_id != grace.stripe_invoice_id or qualification.subscription_id != grace.stripe_subscription_id:
        return None, None, "authority_changed", "Stripe invoice/subscription no longer match grace"
    if qualification.tier.pk != grace.base_tier_at_start_id:
        return None, None, "price_changed", "Mapped membership price changed"
    return subscription, invoice, "ok", ""


def _reconcile_lapsed_tags(user):
    from accounts.utils.tags import add_tag, remove_tag
    changes = []
    for tag in list(user.tags or []):
        if tag == "stripe:active" or tag == "stripe:churned" or tag.startswith("stripe:plan-"):
            remove_tag(user, tag)
            changes.append(f"removed:{tag}")
    if "stripe:lapsed" not in (user.tags or []):
        add_tag(user, "stripe:lapsed")
        changes.append("added:stripe:lapsed")
    return changes


def _expire_locked(grace):
    # Lock and reload the member before consulting local manual state.  A
    # cached ``select_related`` user from the candidate query is not authority:
    # a concurrent staff tier/subscription edit must win over this transition.
    user = User.objects.select_for_update().select_related("tier").get(
        pk=grace.user_id,
    )
    grace.user = user
    subscription, invoice, code, message = _revalidate(grace)
    if code == "recovered":
        return recover_grace(
            subscription_id=grace.stripe_subscription_id,
            invoice_id=grace.stripe_invoice_id,
            livemode=grace.livemode,
            actor="grace_sweep",
        )
    if code != "ok":
        _review(grace, code, message)
        return grace

    old_base = user.tier
    old_effective = _effective_tier(user)
    free = Tier.objects.get(slug="free")
    user.tier = free
    user.pending_tier = None
    user.billing_period_end = None
    user.save(update_fields=["tier", "pending_tier", "billing_period_end"])
    tag_changes = _reconcile_lapsed_tags(user)
    new_effective = _effective_tier(user)
    community_removed = False
    if old_effective and old_effective.level >= LEVEL_MAIN and get_user_level(user) < LEVEL_MAIN:
        from payments import services as payment_services
        payment_services._community_remove(user)
        community_removed = True
    now = timezone.now()
    grace.status = Grace.STATUS_EXPIRED
    grace.expired_at = now
    grace.last_checked_at = now
    grace.last_error_code = ""
    grace.last_error_message = ""
    grace.save(update_fields=[
        "status", "expired_at", "last_checked_at", "last_error_code",
        "last_error_message", "updated_at",
    ])
    Delivery.objects.get_or_create(
        grace=grace,
        kind=Delivery.KIND_EXPIRED_MEMBER,
        recipient=user.email.strip().lower(),
    )
    _audit(
        grace, "expired", actor="grace_sweep", reason="Unpaid monthly invoice after grace",
        old_base=old_base, new_base=free, old_effective=old_effective,
        new_effective=new_effective, community_changed=community_removed,
        tag_changes=tag_changes,
    )
    return grace


def sweep_payment_graces(*, now=None):
    """15-minute idempotent reminder, transition, and delivery sweep."""
    now = now or timezone.now()
    mode = grace_mode()
    candidates = list(
        Grace.objects.filter(status__in=[Grace.STATUS_ACTIVE, Grace.STATUS_REVIEW])
        .values_list("pk", flat=True)
    )
    for grace_id in candidates:
        with transaction.atomic():
            grace = (
                Grace.objects.select_for_update().select_related(
                    "user__tier", "base_tier_at_start",
                ).get(pk=grace_id)
            )
            if grace.status not in {Grace.STATUS_ACTIVE, Grace.STATUS_REVIEW}:
                continue
            if mode != "enforce":
                continue
            if grace.policy_enforced_at is None:
                grace.policy_enforced_at = now
                grace.save(update_fields=["policy_enforced_at", "updated_at"])
            deadline = grace.effective_expires_at
            if now >= deadline:
                _expire_locked(grace)
            elif now >= deadline - timedelta(hours=REMINDER_HOURS):
                # A cheap exact recheck prevents warning after recovery when the
                # recovery webhook arrived late or was missed.
                _, _, code, message = _revalidate(grace)
                if code == "recovered":
                    recover_grace(
                        subscription_id=grace.stripe_subscription_id,
                        invoice_id=grace.stripe_invoice_id,
                        livemode=grace.livemode,
                        actor="grace_sweep",
                    )
                elif code == "ok":
                    _restore_active(grace)
                    Delivery.objects.get_or_create(
                        grace=grace,
                        kind=Delivery.KIND_REMINDER_MEMBER,
                        recipient=grace.user.email.strip().lower(),
                    )
                else:
                    _review(grace, code, message)
    process_due_deliveries(now=now)
    return len(candidates)


def _delivery_backoff(delivery):
    minutes = min(360, 15 * (2 ** max(0, delivery.attempt_count - 1)))
    return timedelta(minutes=minutes)


def _claim_delivery(delivery_id, now):
    with transaction.atomic():
        delivery = Delivery.objects.select_for_update().select_related(
            "grace__user__tier", "grace__base_tier_at_start",
        ).get(pk=delivery_id)
        if delivery.status == Delivery.STATUS_SENT or delivery.attempt_count >= MAX_DELIVERY_ATTEMPTS:
            return None
        if delivery.transport_started_at:
            # Once transport starts, automatic reclaim would permit two SES
            # sends when the original worker completes late.  A caught
            # transport failure clears this marker below and remains
            # retryable.  An interrupted/unknown transport is instead frozen
            # for operator review: at-most-one delivery wins over guessing.
            if delivery.transport_started_at < now - CLAIM_TIMEOUT:
                delivery.status = Delivery.STATUS_FAILED
                delivery.claim_token = None
                delivery.claimed_at = None
                delivery.last_error = (
                    "Delivery outcome unknown after worker interruption; "
                    "automatic retry suppressed to prevent duplicate email."
                )
                delivery.save(update_fields=[
                    "status", "claim_token", "claimed_at", "last_error",
                ])
            return None
        if delivery.claimed_at and delivery.claimed_at >= now - CLAIM_TIMEOUT:
            return None
        if delivery.status == Delivery.STATUS_FAILED and delivery.last_attempt_at:
            if delivery.last_attempt_at + _delivery_backoff(delivery) > now:
                return None
        if delivery.kind in {Delivery.KIND_REMINDER_MEMBER, Delivery.KIND_EXPIRED_MEMBER}:
            expected = Grace.STATUS_ACTIVE if delivery.kind == Delivery.KIND_REMINDER_MEMBER else Grace.STATUS_EXPIRED
            if delivery.grace.status != expected:
                return None
        token = uuid.uuid4()
        delivery.claim_token = token
        delivery.claimed_at = now
        delivery.last_attempt_at = now
        delivery.attempt_count += 1
        delivery.status = Delivery.STATUS_PENDING
        delivery.save(update_fields=[
            "claim_token", "claimed_at", "last_attempt_at", "attempt_count", "status",
        ])
        return delivery, token


def _begin_delivery_transport(delivery_id, token, now):
    """Fence stale workers immediately before the irreversible SES call."""
    with transaction.atomic():
        delivery = Delivery.objects.select_for_update().select_related(
            "grace__user__tier", "grace__base_tier_at_start",
        ).get(pk=delivery_id)
        if (
            delivery.status == Delivery.STATUS_SENT
            or delivery.claim_token != token
            or delivery.transport_started_at is not None
        ):
            return None
        delivery.transport_started_at = now
        delivery.save(update_fields=["transport_started_at"])
        return delivery


def _delivery_template(delivery):
    grace = delivery.grace
    user = grace.user
    effective = _effective_tier(user)
    portal = safe_portal_url()
    deadline = grace.effective_expires_at.astimezone(dt_timezone.utc)
    context = {
        "recovery_url": portal,
        "deadline_utc": deadline.strftime("%Y-%m-%d %H:%M UTC"),
        "base_tier": user.tier.name if user.tier_id else "Free",
        "effective_tier": effective.name if effective else "Free",
        "override_continues": bool(effective and effective.level > (user.tier.level if user.tier_id else 0)),
        "stripe_customer_id": grace.stripe_customer_id,
        "stripe_subscription_id": grace.stripe_subscription_id,
        "stripe_invoice_id": grace.stripe_invoice_id,
        "failure_time": grace.grace_started_at.astimezone(dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "interval": f"{grace.interval} x {grace.interval_count}",
        "studio_member_url": f"{site_base_url()}/studio/users/{user.pk}/",
        "studio_report_url": f"{site_base_url()}/studio/payments/subscription-reconciliation/?filter=payment_grace",
    }
    names = {
        Delivery.KIND_FAILURE_MEMBER: "payment_grace_failure_member",
        Delivery.KIND_FAILURE_TEAM: "payment_grace_failure_team",
        Delivery.KIND_REMINDER_MEMBER: "payment_grace_reminder_member",
        Delivery.KIND_EXPIRED_MEMBER: "payment_grace_expired_member",
    }
    return names[delivery.kind], context


def _send_delivery(delivery):
    template_name, context = _delivery_template(delivery)
    return EmailService().send(
        delivery.grace.user,
        template_name,
        context,
        recipient_email=delivery.recipient,
        dedupe_key=f"monthly-payment-grace:{delivery.grace_id}:{delivery.kind}:{delivery.recipient}",
    )


def process_due_deliveries(*, grace_ids=None, initial_only=False, now=None):
    now = now or timezone.now()
    qs = Delivery.objects.exclude(status=Delivery.STATUS_SENT)
    if grace_ids is not None:
        qs = qs.filter(grace_id__in=grace_ids)
    if initial_only:
        qs = qs.filter(kind__in=[Delivery.KIND_FAILURE_MEMBER, Delivery.KIND_FAILURE_TEAM])
    for delivery_id in list(qs.values_list("pk", flat=True)):
        claimed = _claim_delivery(delivery_id, now)
        if claimed is None:
            continue
        delivery, token = claimed
        delivery = _begin_delivery_transport(delivery.pk, token, now)
        if delivery is None:
            continue
        try:
            email_log = _send_delivery(delivery)
            error = "" if safe_portal_url() else "STRIPE_CUSTOMER_PORTAL_URL is missing or invalid; recovery link omitted."
        except Exception as exc:  # transport/config errors are retryable
            email_log = None
            error = f"Email transport failed ({exc.__class__.__name__})"
        with transaction.atomic():
            current = Delivery.objects.select_for_update().get(pk=delivery.pk)
            if current.claim_token != token:
                continue
            current.claim_token = None
            current.claimed_at = None
            current.last_error = error
            if email_log is not None:
                current.status = Delivery.STATUS_SENT
                current.sent_at = timezone.now()
                current.email_log = email_log
            else:
                current.status = Delivery.STATUS_FAILED
                # A caught failure is known not to have succeeded and may be
                # retried after backoff.  Only an interrupted/unknown outcome
                # retains the transport fence above.
                current.transport_started_at = None
            current.save(update_fields=[
                "claim_token", "claimed_at", "transport_started_at",
                "last_error", "status", "sent_at", "email_log",
            ])


def discover_from_reconciliation_run(run_id):
    """Materialize missed-webhook grace from eligible persisted findings."""
    from payments.models import SubscriptionReconciliationFinding as Finding

    found = 0
    rows = Finding.objects.filter(run_id=run_id).select_related("user")
    for row in rows:
        if row.classification not in {
            "monthly_payment_grace_active", "monthly_payment_grace_due",
        } or row.user is None:
            continue
        try:
            subscription = _retrieve_subscription(row.stripe_subscription_id)
            invoice = _retrieve_invoice(row.latest_invoice_id)
            grace, qualification = start_grace_from_failure(
                invoice=invoice,
                subscription=subscription,
                livemode=None,
                source=Grace.SOURCE_RECONCILIATION,
                run_id=str(run_id),
            )
            if grace and qualification.eligible:
                found += 1
        except (
            stripe.StripeError,
            *CONFIGURATION_ERRORS,
            IntegrityError,
            WebhookAmbiguousUserError,
            WebhookUnmatchedUserError,
        ):
            continue
    return found
