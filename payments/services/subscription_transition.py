"""Shared Stripe subscription-state transitions.

Issue #1308. The ended-subscription transition is the single write path that
both ``customer.subscription.deleted`` (webhook) and the confirmed
reconciliation apply use, so the two can never drift.

``apply_ended_subscription`` reverts the base tier to Free, clears
``subscription_id`` / ``billing_period_end`` / ``pending_tier``, churns the
Stripe status tags, and removes the user from the community ONLY when
EFFECTIVE access falls below Main (a surviving active ``TierOverride`` keeps
a comped member in). It never deactivates an override.

The function assumes it is called inside ``transaction.atomic()`` with the
user row already ``select_for_update``-locked by the caller (both the webhook
handler and the reconcile apply do this).
"""

from payments import services as _services
from payments.models import Tier
from payments.services.stripe_tags import reconcile_stripe_status_tags


def apply_ended_subscription(user):
    """Revert a user to Free for an ended (``canceled``) subscription.

    Returns a dict describing what changed so callers can audit:
    ``{"community_removed": bool, "old_tier_slug": str}``.
    """
    old_tier_slug = user.tier.slug if user.tier_id and user.tier else "free"
    had_community = bool(user.tier and user.tier.level >= 20)

    free_tier = Tier.objects.filter(slug="free").first()
    user.tier = free_tier
    user.subscription_id = ""
    user.billing_period_end = None
    user.pending_tier = None
    user.save(
        update_fields=[
            "tier",
            "subscription_id",
            "billing_period_end",
            "pending_tier",
        ]
    )

    # The subscription is gone — churn the stripe:* status tags.
    reconcile_stripe_status_tags(user, active=False, tier=None)

    # Community access follows EFFECTIVE tier. A paid subscription ending does
    # NOT revoke an admin-granted override — a comped member stays in.
    from content.access import LEVEL_MAIN, get_user_level

    community_removed = False
    if had_community and get_user_level(user) < LEVEL_MAIN:
        _services._community_remove(user)
        community_removed = True

    return {
        "community_removed": community_removed,
        "old_tier_slug": old_tier_slug,
    }
