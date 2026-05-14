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
from payments.models import WebhookEvent
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

    @property
    def changed(self):
        return self.status == "changed"


def backfill_user_from_stripe(user, *, dry_run=False, price_to_tier=None):
    """Sync one user's direct tier fields from their active Stripe subscription."""
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
    tier = price_to_tier.get(price_id)
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

    override = _active_matching_override(user, tier)
    metadata_saved = _subscription_metadata_would_change(user, subscription_id, period_end)
    tier_changed = user.tier_id != tier.pk
    override_deactivated = override is not None

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
    )

    if dry_run:
        record.status = "dry_run"
        return record

    update_fields = []
    if tier_changed:
        user.tier = tier
        update_fields.append("tier")
    if subscription_id and not user.subscription_id:
        user.subscription_id = subscription_id
        update_fields.append("subscription_id")
    if period_end and user.billing_period_end is None:
        user.billing_period_end = period_end
        update_fields.append("billing_period_end")
    if update_fields:
        user.save(update_fields=update_fields)
    if override:
        override.is_active = False
        override.save(update_fields=["is_active"])

    record.audit_event_id = _write_audit_row(record)
    return record


def _active_subscriptions_for_customer(customer_id):
    secret_key = get_config("STRIPE_SECRET_KEY", "")
    response = stripe.Subscription.list(
        api_key=secret_key,
        customer=customer_id,
        status="active",
        limit=100,
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


def _subscription_metadata_would_change(user, subscription_id, period_end):
    return bool(
        (subscription_id and not user.subscription_id)
        or (period_end and user.billing_period_end is None)
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
    WebhookEvent.objects.create(
        stripe_event_id=event_id,
        event_type="backfill_stripe_tiers",
        payload={
            "user_id": record.user_id,
            "email": record.email,
            "stripe_customer_id": record.stripe_customer_id,
            "old_tier_slug": record.old_tier_slug,
            "new_tier_slug": record.new_tier_slug,
            "subscription_id": record.subscription_id,
            "price_id": record.price_id,
            "override_deactivated": record.override_deactivated,
            "metadata_saved": record.metadata_saved,
        },
    )
    return event_id


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
