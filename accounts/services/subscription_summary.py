"""Cached subscription summary shared by Studio and operator APIs."""


STATUS_ACTIVE = "active"
STATUS_CANCELLATION_SCHEDULED = "cancellation_scheduled"
STATUS_NONE = "none"


def subscription_summary(user):
    """Serialize truthful subscription state without contacting Stripe."""
    base_tier = user.tier if user.tier_id and user.tier.level > 0 else None
    has_paid_subscription = bool(user.subscription_id and base_tier)
    cancellation_scheduled = bool(
        has_paid_subscription
        and user.pending_tier_id
        and user.pending_tier.slug == "free"
        and user.billing_period_end
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
            user.billing_period_end.isoformat()
            if user.billing_period_end is not None
            else None
        ),
        "date_kind": date_kind,
    }
