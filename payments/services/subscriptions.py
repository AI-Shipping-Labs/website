"""Subscription query helpers.

The active product model routes all subscription edits through the
Stripe Customer Portal, so this module only contains read helpers.

The query helpers (``_tier_from_subscription``,
``_get_subscription_period_end``, ``_get_subscription_price_id``) call
``_get_stripe_client`` through the ``payments.services`` package so tests
that patch ``payments.services._get_stripe_client`` still take effect.
Logger calls go through the package for the same reason.
"""

import stripe

from payments import services as _services


def _tier_from_subscription(subscription_id):
    """Look up the tier from a Stripe subscription's price ID."""
    try:
        client = _services._get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        price_id = _services._subscription_price_id(subscription)
        if price_id:
            return _services._tier_for_price_id(price_id)
    except stripe.StripeError:
        _services.logger.exception(
            "Failed to look up tier from subscription %s", subscription_id,
        )
    return None


def _get_subscription_period_end(subscription_id):
    """Get the current_period_end from a Stripe subscription."""
    try:
        client = _services._get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        return _services._subscription_period_end(subscription)
    except stripe.StripeError:
        _services.logger.exception(
            "Failed to get period end for subscription %s", subscription_id,
        )
    return None


def _get_subscription_interval(subscription_id):
    """Return ``"month"`` / ``"year"`` for a subscription, or "" on failure.

    Reads ``recurring.interval`` off the first subscription item's price.
    This is an OPTIONAL resilience fallback for ``notify_paid_signup``
    when the webhook-computed ``billing_period`` is empty; the common path
    threads ``billing_period`` through and never reaches here, so no extra
    Stripe round-trip is added to the common webhook flow.

    Wrapped so a slow / failed Stripe call NEVER raises — returns "" and
    the caller renders no interval. Returns "" on any error so the staff
    notification still sends.
    """
    try:
        client = _services._get_stripe_client()
        subscription = client.subscriptions.retrieve(
            subscription_id,
            params={"expand": ["items.data.price"]},
        )
        item = _services._first_subscription_item(subscription)
        price = _services._stripe_value(item, "price")
        recurring = _services._stripe_value(price, "recurring")
        interval = _services._stripe_value(recurring, "interval")
        if interval and not _is_missing(interval):
            return interval
    except Exception:  # noqa: BLE001 - optional best-effort lookup, never blocks the send
        _services.logger.warning(
            "Failed to get interval for subscription %s",
            subscription_id,
            exc_info=True,
        )
    return ""


def _is_missing(value):
    """True when ``_stripe_value`` returned its ``_MISSING`` sentinel."""
    from payments.services.stripe_client import _MISSING

    return value is _MISSING


def _get_subscription_price_id(subscription_id):
    """Return the price ID of the first item on a Stripe subscription.

    Used by ``handle_checkout_completed`` to determine whether the user
    bought the monthly or yearly variant of a tier. Returns "" on any
    error so the caller can fall back to a blank ``billing_period``
    rather than crashing.
    """
    try:
        client = _services._get_stripe_client()
        subscription = client.subscriptions.retrieve(subscription_id)
        return _services._subscription_price_id(subscription)
    except stripe.StripeError:
        _services.logger.exception(
            "Failed to get price id for subscription %s", subscription_id,
        )
    return ""
