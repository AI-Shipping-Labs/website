"""Shared 3-step Stripe-subscription -> ``Tier`` resolver.

The resolver chain is:

1. ``subscription.items.data[0].price.metadata.tier_slug`` — declarative
   label that travels with the price object (set by
   ``stripe_create_products``).
2. ``price_to_tier[price_id]`` — the DB ``Tier.stripe_price_id_*`` map.
3. ``price.unit_amount`` (cents) + ``recurring.interval`` against
   ``Tier.price_eur_month * 100`` / ``Tier.price_eur_year * 100``.
   Currency-agnostic; cents only. Robust to Stripe price regenerations.

This module exists so both the backfill path
(``payments.services.backfill_tiers``) and the webhook path
(``payments.services.webhook_handlers.handle_checkout_completed``) reach
for the same resolver. Previously the resolver lived only in
``backfill_tiers`` and the webhook fell back to a price-id-only lookup
that broke when live Stripe prices were not recorded on the local
``Tier`` rows (issue #663).

Note on product-level metadata: a fallback to
``price.product.metadata.tier_slug`` would require expanding
``data.items.data.price.product``, which is 5 levels deep — Stripe
rejects expansions past 4 levels. A separate ``Product.retrieve`` per
user is avoided because amount-based matching covers the same case.
"""

from __future__ import annotations

from payments.models import Tier


def resolve_subscription_tier(subscription, price_id, price_to_tier):
    """Resolve a Stripe subscription to a local ``Tier`` row.

    Returns ``None`` if all three resolver steps fail.
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

    return tier_by_amount_interval(price)


def tier_by_amount_interval(price):
    """Match a Stripe price to a ``Tier`` row by ``unit_amount`` + interval.

    ``Tier.price_eur_month`` / ``price_eur_year`` are integer EUR values;
    Stripe ``unit_amount`` is integer cents. Match when
    ``unit_amount == price_eur_* * 100`` for the corresponding
    ``recurring.interval``. Returns ``None`` if the price has no recurring
    interval, no amount, or no tier matches.
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


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)
