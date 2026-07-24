"""Unambiguous local-user resolution for Stripe subscription callbacks.

Issue #1314. Subscription callbacks (``customer.subscription.updated`` /
``customer.subscription.deleted``) must resolve to EXACTLY one local user or
fail explicitly. The previous ``.first()`` lookups could silently pick an
arbitrary account when two local users shared a Stripe customer or
subscription id.

Resolution order:

1. Exact, non-empty ``subscription_id``. Exactly one match wins. More than
   one is a terminal :class:`WebhookAmbiguousUserError`.
2. Non-empty ``stripe_customer_id``, but only for users whose stored
   subscription is blank or equals the event's subscription. Exactly one
   eligible match wins; more than one is ambiguous. When the only customer
   matches store a DIFFERENT non-empty subscription, the event is stale (an
   older subscription must not cancel a newer authoritative one).

Zero matches raise :class:`WebhookUnmatchedUserError` (retryable).
"""

from dataclasses import dataclass, field

from accounts.models import User
from payments.exceptions import (
    WebhookAmbiguousUserError,
    WebhookUnmatchedUserError,
)


@dataclass
class SubscriptionResolution:
    """Outcome of resolving a subscription callback to a local user."""

    user: User | None = None
    stale_customer_user: User | None = None
    matched_by: str = ""
    _customer_user_ids: list = field(default_factory=list)


def resolve_subscription_user(subscription_id, customer_id):
    """Resolve a subscription callback to exactly one local ``User``.

    Returns a :class:`SubscriptionResolution` with either ``user`` set (one
    unambiguous owner) or ``stale_customer_user`` set (the customer owns a
    newer authoritative subscription, so this older event is stale).

    Raises:
        WebhookAmbiguousUserError: More than one local user matches.
        WebhookUnmatchedUserError: No local user matches.
    """
    subscription_id = (subscription_id or "").strip()
    customer_id = (customer_id or "").strip()

    if subscription_id:
        sub_matches = list(User.objects.filter(subscription_id=subscription_id))
        if len(sub_matches) > 1:
            raise WebhookAmbiguousUserError(
                f"Multiple local users share subscription_id={subscription_id}",
                matched_by="subscription_id",
                subscription_id=subscription_id,
                customer_id=customer_id,
                user_ids=[u.pk for u in sub_matches],
            )
        if len(sub_matches) == 1:
            return SubscriptionResolution(
                user=sub_matches[0], matched_by="subscription_id",
            )

    if customer_id:
        cust_matches = list(
            User.objects.filter(stripe_customer_id=customer_id)
        )
        eligible = [
            u for u in cust_matches
            if not (u.subscription_id or "").strip()
            or u.subscription_id == subscription_id
        ]
        stale = [
            u for u in cust_matches
            if (u.subscription_id or "").strip()
            and u.subscription_id != subscription_id
        ]
        if len(eligible) > 1:
            raise WebhookAmbiguousUserError(
                f"Multiple local users share stripe_customer_id={customer_id}",
                matched_by="customer_id",
                subscription_id=subscription_id,
                customer_id=customer_id,
                user_ids=[u.pk for u in eligible],
            )
        if len(eligible) == 1:
            return SubscriptionResolution(
                user=eligible[0], matched_by="customer_id",
            )
        if stale:
            # No eligible match, but the customer owns a different, newer
            # authoritative subscription. This event is stale.
            return SubscriptionResolution(stale_customer_user=stale[0])

    raise WebhookUnmatchedUserError(
        f"No local user for subscription_id={subscription_id} "
        f"customer_id={customer_id}",
        subscription_id=subscription_id,
        customer_id=customer_id,
    )
