"""Backfill user subscription tiers from Stripe.

This module is shared by the management command and the Studio one-user
action so both paths make the same decisions about direct tier writes,
redundant overrides, and subscription metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import stripe
from django.utils import timezone

from accounts.models import TierOverride
from integrations.config import get_config
from payments.models import Tier, WebhookEvent
from payments.services.import_stripe import (
    CONFIGURATION_ERRORS,
    _price_to_tier_map,
    _subscription_period_end,
    _subscription_price_id,
)


@dataclass
class ChangeRecord:
    user_id: int
    email: str
    stripe_customer_id: str
    status: str
    message: str
    old_tier_slug: str = ""
    new_tier_slug: str = ""
    subscription_id: str = ""
    price_id: str = ""
    override_deactivated: bool = False
    metadata_saved: bool = False
    audit_event_id: str = ""
    warnings: list[str] = field(default_factory=list)
    # Force-mode audit signals (issue #660). Populated only when the
    # apply-side reconcile sweeps more than the single matching override
    # and/or overwrites a non-null billing_period_end.
    deactivated_override_ids: list[int] = field(default_factory=list)
    billing_period_end_overwritten: bool = False

    @property
    def changed(self):
        return self.status == "changed"


def backfill_user_from_stripe(user, *, dry_run=False, price_to_tier=None, force=False):
    """Sync one user's direct tier fields from their active Stripe subscription.

    When ``force=True`` and the user has an active Stripe subscription that
    resolves to a tier, every active ``TierOverride`` on the user is
    deactivated (not just the one matching the resolved tier), and
    ``user.billing_period_end`` is overwritten with Stripe's current
    ``current_period_end`` even when it is already populated.

    Force is only ever honored when Stripe says the user is actively paying.
    Users with no ``stripe_customer_id`` or no active subscription are
    returned with their existing ``skipped`` / ``warning`` outcome and no
    writes occur — force never escalates a non-Stripe user.
    """
    if not user.stripe_customer_id:
        return ChangeRecord(
            user_id=user.pk,
            email=user.email,
            stripe_customer_id="",
            status="skipped",
            message="skipped: user has no Stripe customer ID",
            old_tier_slug=_tier_slug(user),
        )

    price_to_tier = price_to_tier or _price_to_tier_map()
    try:
        subscriptions = _active_subscriptions_for_customer(user.stripe_customer_id)
    except CONFIGURATION_ERRORS as exc:
        warning = _stripe_lookup_warning(user.email, exc)
        return ChangeRecord(
            user_id=user.pk,
            email=user.email,
            stripe_customer_id=user.stripe_customer_id,
            status="warning",
            message=warning,
            old_tier_slug=_tier_slug(user),
            warnings=[warning],
        )
    subscription = subscriptions[0] if subscriptions else None
    old_tier_slug = _tier_slug(user)

    if subscription is None:
        if old_tier_slug != "free":
            warning = (
                f"warning: no active Stripe subscription for paid user "
                f"{user.email}; leaving tier {old_tier_slug} unchanged"
            )
            return ChangeRecord(
                user_id=user.pk,
                email=user.email,
                stripe_customer_id=user.stripe_customer_id,
                status="warning",
                message=warning,
                old_tier_slug=old_tier_slug,
                warnings=[warning],
            )
        return ChangeRecord(
            user_id=user.pk,
            email=user.email,
            stripe_customer_id=user.stripe_customer_id,
            status="skipped",
            message="no change: no active Stripe subscription",
            old_tier_slug=old_tier_slug,
        )

    price_id = _subscription_price_id(subscription)
    tier = _resolve_subscription_tier(subscription, price_id, price_to_tier)
    subscription_id = _get(subscription, "id", "") or ""
    period_end = _subscription_period_end(subscription)

    if tier is None:
        warning = (
            f"warning: active Stripe subscription {subscription_id} for "
            f"{user.email} uses unknown price {price_id}; tier unchanged"
        )
        return ChangeRecord(
            user_id=user.pk,
            email=user.email,
            stripe_customer_id=user.stripe_customer_id,
            status="warning",
            message=warning,
            old_tier_slug=old_tier_slug,
            subscription_id=subscription_id,
            price_id=price_id,
            warnings=[warning],
        )

    # Override planning. In non-force mode we only touch the override that
    # matches the resolved tier (today's behaviour). In force mode we sweep
    # every active override on the user.
    if force:
        sweep_overrides = list(_active_overrides_for_user(user))
    else:
        matching_override = _active_matching_override(user, tier)
        sweep_overrides = [matching_override] if matching_override else []

    override_deactivated = bool(sweep_overrides)
    deactivated_override_ids = [override.pk for override in sweep_overrides]

    # Subscription-id metadata behaves the same in both modes: write only
    # when the local field is empty. Force only widens billing_period_end.
    subscription_id_would_change = bool(subscription_id and not user.subscription_id)
    if force:
        billing_period_end_would_change = bool(
            period_end and user.billing_period_end != period_end
        )
        billing_period_end_overwritten = bool(
            period_end
            and user.billing_period_end is not None
            and user.billing_period_end != period_end
        )
    else:
        billing_period_end_would_change = bool(
            period_end and user.billing_period_end is None
        )
        billing_period_end_overwritten = False

    metadata_saved = subscription_id_would_change or billing_period_end_would_change
    tier_changed = user.tier_id != tier.pk

    if not tier_changed and not override_deactivated and not metadata_saved:
        return ChangeRecord(
            user_id=user.pk,
            email=user.email,
            stripe_customer_id=user.stripe_customer_id,
            status="skipped",
            message=f"no change: already on {tier.slug}",
            old_tier_slug=old_tier_slug,
            new_tier_slug=tier.slug,
            subscription_id=subscription_id,
            price_id=price_id,
        )

    record = ChangeRecord(
        user_id=user.pk,
        email=user.email,
        stripe_customer_id=user.stripe_customer_id,
        status="changed",
        message=_change_message(
            old_tier_slug,
            tier.slug,
            override_deactivated=override_deactivated,
            metadata_saved=metadata_saved,
            dry_run=dry_run,
        ),
        old_tier_slug=old_tier_slug,
        new_tier_slug=tier.slug,
        subscription_id=subscription_id,
        price_id=price_id,
        override_deactivated=override_deactivated,
        metadata_saved=metadata_saved,
        deactivated_override_ids=deactivated_override_ids,
        billing_period_end_overwritten=billing_period_end_overwritten,
    )

    if dry_run:
        record.status = "dry_run"
        return record

    update_fields = []
    if tier_changed:
        user.tier = tier
        update_fields.append("tier")
    if subscription_id_would_change:
        user.subscription_id = subscription_id
        update_fields.append("subscription_id")
    if billing_period_end_would_change:
        user.billing_period_end = period_end
        update_fields.append("billing_period_end")
    if update_fields:
        user.save(update_fields=update_fields)
    for override in sweep_overrides:
        override.is_active = False
        override.save(update_fields=["is_active"])

    record.audit_event_id = _write_audit_row(record)
    return record


def _resolve_subscription_tier(subscription, price_id, price_to_tier):
    """Resolve a Stripe subscription to a local ``Tier`` row.

    Resolution order:

    1. ``subscription.items.data[0].price.metadata.tier_slug`` (declarative
       label that travels with the price object, set by
       ``stripe_create_products``).
    2. ``price_to_tier[price_id]`` — the DB ``Tier.stripe_price_id_*`` map.
    3. Amount + interval match: ``price.unit_amount`` against
       ``Tier.price_eur_month * 100`` or ``Tier.price_eur_year * 100``
       (currency-agnostic; cents only). Robust to Stripe price regenerations,
       which is exactly the case that the original DB-only lookup misses.

    Returns ``None`` if all three paths fail.

    Note on product-level metadata: a fallback to
    ``price.product.metadata.tier_slug`` would require expanding
    ``data.items.data.price.product``, which is 5 levels deep — Stripe rejects
    expansions past 4 levels. A separate ``Product.retrieve`` per user is
    avoided because amount-based matching already covers the same case.
    """
    items = _get(subscription, "items", {}) or {}
    data = _get(items, "data", []) or []
    first_item = data[0] if data else {}
    price = _get(first_item, "price", {}) or {}

    price_metadata = _get(price, "metadata", {}) or {}
    price_slug = _normalize_slug(price_metadata.get("tier_slug"))
    if price_slug:
        tier = _tier_by_slug(price_slug)
        if tier is not None:
            return tier

    tier = price_to_tier.get(price_id)
    if tier is not None:
        return tier

    return _tier_by_amount_interval(price)


def _tier_by_amount_interval(price):
    """Match a Stripe price to a ``Tier`` row by ``unit_amount`` + interval.

    ``Tier.price_eur_month`` / ``price_eur_year`` are integer EUR values; Stripe
    ``unit_amount`` is integer cents. Match when ``unit_amount == price_eur_* * 100``
    for the corresponding ``recurring.interval``. Returns ``None`` if the price
    has no recurring interval, no amount, or no tier matches.
    """
    unit_amount = _get(price, "unit_amount")
    if not isinstance(unit_amount, int) or unit_amount <= 0:
        return None
    recurring = _get(price, "recurring", {}) or {}
    interval = _get(recurring, "interval")
    if interval == "month":
        return Tier.objects.filter(price_eur_month=unit_amount // 100).first()
    if interval == "year":
        return Tier.objects.filter(price_eur_year=unit_amount // 100).first()
    return None


def _normalize_slug(value):
    if not isinstance(value, str):
        return ""
    return value.strip()


def _tier_by_slug(slug):
    try:
        return Tier.objects.get(slug=slug)
    except Tier.DoesNotExist:
        return None


def _active_overrides_for_user(user):
    """All active, unexpired ``TierOverride`` rows for a user (force-mode sweep)."""
    return (
        TierOverride.objects
        .filter(
            user_id=user.pk,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
    )


def _active_subscriptions_for_customer(customer_id):
    secret_key = get_config("STRIPE_SECRET_KEY", "")
    response = stripe.Subscription.list(
        api_key=secret_key,
        customer=customer_id,
        status="active",
        limit=100,
        # 4 levels deep — Stripe rejects 5-level expansions. The resolver
        # reads ``price.metadata.tier_slug`` from this payload and falls
        # back to amount + interval matching for prices that lack metadata
        # (e.g. prices created out-of-band on the Stripe dashboard).
        expand=["data.items.data.price"],
    )
    subscriptions = list(response.auto_paging_iter())
    subscriptions.sort(
        key=lambda subscription: _get(subscription, "current_period_end") or 0,
        reverse=True,
    )
    return subscriptions


def _stripe_lookup_warning(email, exc):
    message = str(exc).splitlines()[0]
    if not message:
        message = exc.__class__.__name__
    return f"warning: Stripe lookup failed for {email}: {message}"


def _active_matching_override(user, tier):
    return (
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


def _tier_slug(user):
    if user.tier_id and user.tier:
        return user.tier.slug
    return "free"


def _change_message(old_slug, new_slug, *, override_deactivated, metadata_saved, dry_run):
    parts = [f"{old_slug} -> {new_slug}"]
    if override_deactivated:
        parts.append("deactivate matching override")
    if metadata_saved:
        parts.append("save subscription metadata")
    prefix = "would change" if dry_run else "changed"
    return f"{prefix}: " + "; ".join(parts)


def _write_audit_row(record):
    event_id = f"backfill_stripe_tiers:user:{record.user_id}:{timezone.now().timestamp()}"
    payload = {
        "user_id": record.user_id,
        "email": record.email,
        "stripe_customer_id": record.stripe_customer_id,
        "old_tier_slug": record.old_tier_slug,
        "new_tier_slug": record.new_tier_slug,
        "subscription_id": record.subscription_id,
        "price_id": record.price_id,
        "override_deactivated": record.override_deactivated,
        "metadata_saved": record.metadata_saved,
    }
    # Additive force-mode signals (issue #660): always present so prod
    # operators can audit force-mode sweeps. Backwards compatible — keys
    # are additive on top of the existing schema.
    payload["deactivated_override_ids"] = list(record.deactivated_override_ids)
    payload["deactivated_override_count"] = len(record.deactivated_override_ids)
    payload["billing_period_end_overwritten"] = record.billing_period_end_overwritten
    WebhookEvent.objects.create(
        stripe_event_id=event_id,
        event_type="backfill_stripe_tiers",
        payload=payload,
    )
    return event_id


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
