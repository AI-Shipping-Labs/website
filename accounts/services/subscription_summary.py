"""Cached subscription summary shared by Studio and operator APIs."""

from payments.models import Membership

STATUS_ACTIVE = "active"
STATUS_CANCELLATION_SCHEDULED = "cancellation_scheduled"
STATUS_NONE = "none"


def subscription_summary(user):
    """Serialize truthful subscription state without contacting Stripe."""
    # Issue #1579: the tier/Stripe state lives on payments.Membership.
    membership = Membership.for_user(user)
    base_tier = (
        membership.tier
        if membership.tier_id and membership.tier.level > 0
        else None
    )
    has_paid_subscription = bool(membership.subscription_id and base_tier)
    cancellation_scheduled = bool(
        has_paid_subscription
        and membership.pending_tier_id
        and membership.pending_tier.slug == "free"
        and membership.billing_period_end
    )

    if cancellation_scheduled:
        status = STATUS_CANCELLATION_SCHEDULED
        date_kind = "access_until"
    elif has_paid_subscription:
        status = STATUS_ACTIVE
        date_kind = "renews"
    else:
        status = STATUS_NONE
        date_kind = "none"

    return {
        "plan_name": base_tier.name if has_paid_subscription else None,
        "plan_slug": base_tier.slug if has_paid_subscription else None,
        "status": status,
        "current_period_end": (
            membership.billing_period_end.isoformat()
            if membership.billing_period_end is not None
            else None
        ),
        "date_kind": date_kind,
    }
