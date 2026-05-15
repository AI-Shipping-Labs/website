"""Low-level Stripe client wrappers and subscription-shape helpers.

These functions know how to talk to Stripe and how to safely walk the
mixed dict / StripeObject payloads that webhooks and the REST API return.
Higher-level handlers in :mod:`payments.services.webhook_handlers` and
:mod:`payments.services.subscriptions` build on these primitives.
"""

import logging
from datetime import datetime, timezone

import stripe

from integrations.config import get_config
from payments.models import Tier

logger = logging.getLogger(__name__)

# Sentinel returned by ``_stripe_value`` when a key is genuinely absent so
# we can distinguish "missing" from a literal ``None`` value coming back
# from Stripe.
_MISSING = object()


def _get_stripe_client():
    """Return a configured Stripe client using the configured secret key."""
    return stripe.StripeClient(get_config("STRIPE_SECRET_KEY", ""))


def _tier_for_price_id(price_id):
    """Look up a Tier by its monthly or yearly Stripe price ID.

    Returns None if no tier matches or if price_id is empty.
    """
    if not price_id:
        return None
    tier = Tier.objects.filter(stripe_price_id_monthly=price_id).first()
    if tier is None:
        tier = Tier.objects.filter(stripe_price_id_yearly=price_id).first()
    return tier


def _stripe_value(obj, key):
    """Read a StripeObject/dict field without tripping over mapping methods."""
    if obj is None:
        return _MISSING
    if isinstance(obj, dict):
        return obj.get(key, _MISSING)
    try:
        return obj[key]
    except (KeyError, TypeError, AttributeError):
        pass
    value = getattr(obj, key, _MISSING)
    if callable(value):
        return _MISSING
    return value


def _first_subscription_item(subscription):
    """Return the first subscription item from Stripe dict/object payloads."""
    items = _stripe_value(subscription, "items")
    if items is _MISSING:
        return None
    data = _stripe_value(items, "data")
    if data in (_MISSING, None):
        return None
    try:
        return data[0]
    except (IndexError, KeyError, TypeError):
        return None


def _subscription_price_id(subscription):
    item = _first_subscription_item(subscription)
    price = _stripe_value(item, "price")
    price_id = _stripe_value(price, "id")
    return price_id if price_id is not _MISSING and price_id else ""


def _subscription_period_end(subscription):
    period_end = _stripe_value(subscription, "current_period_end")
    if period_end in (_MISSING, None, ""):
        item = _first_subscription_item(subscription)
        period_end = _stripe_value(item, "current_period_end")
    if period_end in (_MISSING, None, ""):
        return None
    try:
        return datetime.fromtimestamp(period_end, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
