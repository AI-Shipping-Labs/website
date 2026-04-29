"""Stripe customer import adapter for the shared user-import runner."""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import timezone as datetime_timezone

import stripe
from django.core.management.base import CommandError

from accounts.models import IMPORT_SOURCE_STRIPE
from accounts.services.import_users import ImportRow, register_import_adapter
from integrations.config import get_config
from payments.models import Tier

logger = logging.getLogger(__name__)

ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}
CONFIGURATION_ERRORS = (
    stripe.AuthenticationError,
    stripe.PermissionError,
    stripe.InvalidRequestError,
)


def stripe_customer_import_adapter():
    """Yield import rows for Stripe customers and their subscription state."""
    secret_key = get_config("STRIPE_SECRET_KEY", "")
    if not secret_key:
        raise CommandError("STRIPE_SECRET_KEY is required for Stripe imports.")

    price_to_tier = _price_to_tier_map()

    try:
        customers = stripe.Customer.list(api_key=secret_key, limit=100)
        for customer in customers.auto_paging_iter():
            row = _row_for_customer(customer, secret_key=secret_key, price_to_tier=price_to_tier)
            if row is not None:
                yield row
    except CONFIGURATION_ERRORS as exc:
        raise CommandError(f"Stripe import configuration error: {exc}") from exc


def register_stripe_import_adapter():
    """Register the Stripe adapter with the shared import registry."""
    register_import_adapter(IMPORT_SOURCE_STRIPE, stripe_customer_import_adapter)


def _row_for_customer(customer, *, secret_key, price_to_tier):
    customer_id = _get(customer, "id", "")
    email = _get(customer, "email", "")
    metadata = {
        "stripe_customer_id": customer_id,
        "created": _get(customer, "created"),
        "livemode": _get(customer, "livemode"),
    }

    if not email:
        logger.info("Skipping Stripe customer without email: %s", customer_id)
        return ImportRow(
            email="",
            source_metadata={**metadata, "skip_reason": "missing_email"},
            tags=["stripe:imported"],
        )

    subscriptions = list(_subscriptions_for_customer(customer_id, secret_key=secret_key))
    selected = _select_active_subscription(subscriptions, price_to_tier)
    had_prior_subscriptions = bool(subscriptions)

    tags = ["stripe:imported"]
    extra_user_fields = {"stripe_customer_id": customer_id}
    tier_slug = None
    tier_expiry = None

    if selected:
        price_id = _subscription_price_id(selected)
        tier = price_to_tier.get(price_id)
        period_end = _subscription_period_end(selected)
        metadata.update(
            {
                "subscription_id": _get(selected, "id", ""),
                "subscription_status": _get(selected, "status", ""),
                "subscription_price_id": price_id,
                "current_period_end": _get(selected, "current_period_end"),
            }
        )
        tags.append("stripe:active")
        extra_user_fields["subscription_id"] = _get(selected, "id", "")
        if period_end:
            extra_user_fields["billing_period_end"] = period_end
            tier_expiry = period_end
        if tier:
            tier_slug = tier.slug
            tags.append(f"stripe:plan-{tier.slug}")
        else:
            logger.warning(
                "Stripe customer %s has active subscription %s with unknown price %s",
                customer_id,
                _get(selected, "id", ""),
                price_id,
            )
    elif had_prior_subscriptions:
        tags.append("stripe:churned")

    return ImportRow(
        email=email,
        name=_get(customer, "name", "") or "",
        source_metadata=metadata,
        tier_slug=tier_slug,
        tier_expiry=tier_expiry,
        tags=tags,
        extra_user_fields=extra_user_fields,
    )


def _subscriptions_for_customer(customer_id, *, secret_key):
    subscriptions = stripe.Subscription.list(
        api_key=secret_key,
        customer=customer_id,
        status="all",
        limit=100,
        expand=["data.items.data.price"],
    )
    return subscriptions.auto_paging_iter()


def _select_active_subscription(subscriptions, price_to_tier):
    active_subscriptions = [
        subscription
        for subscription in subscriptions
        if _get(subscription, "status", "") in ACTIVE_SUBSCRIPTION_STATUSES
    ]
    if not active_subscriptions:
        return None

    return max(
        active_subscriptions,
        key=lambda subscription: (
            _tier_level_for_subscription(subscription, price_to_tier),
            _get(subscription, "current_period_end") or 0,
        ),
    )


def _tier_level_for_subscription(subscription, price_to_tier):
    tier = price_to_tier.get(_subscription_price_id(subscription))
    return tier.level if tier else -1


def _subscription_price_id(subscription):
    items = _get(subscription, "items", {}) or {}
    data = _get(items, "data", []) or []
    if not data:
        return ""
    first_item = data[0]
    price = _get(first_item, "price", {}) or {}
    return _get(price, "id", "") or ""


def _subscription_period_end(subscription):
    current_period_end = _get(subscription, "current_period_end")
    if not current_period_end:
        return None
    return datetime.fromtimestamp(current_period_end, tz=datetime_timezone.utc)


def _price_to_tier_map():
    mapping = {}
    for tier in Tier.objects.exclude(level=0):
        for price_id in (tier.stripe_price_id_monthly, tier.stripe_price_id_yearly):
            if price_id:
                mapping[price_id] = tier
    return mapping


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
