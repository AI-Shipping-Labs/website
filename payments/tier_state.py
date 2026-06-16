"""Shared membership tier action state for pricing and account pages."""

from dataclasses import dataclass


def format_period_end(value):
    if value is None:
        return ""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


@dataclass(frozen=True)
class _TierStateContext:
    tier: object
    base_tier: object
    pending_tier: object
    has_subscription: bool
    base_level: int
    tier_level: int
    pending_end: str
    override_tier: object
    override_level: int
    override_end: str


def _state(badge, note, action_label, action_kind):
    return {
        "badge": badge,
        "note": note,
        "action_label": action_label,
        "action_kind": action_kind,
    }


def _build_context(tier, user, active_override):
    base_tier = user.tier
    pending_tier = user.pending_tier
    has_subscription = bool(user.subscription_id)
    base_level = base_tier.level if base_tier else 0
    tier_level = tier.level
    pending_end = format_period_end(user.billing_period_end)
    override_tier = active_override.override_tier if active_override else None
    override_level = override_tier.level if override_tier else 0
    override_end = (
        format_period_end(active_override.expires_at)
        if active_override
        else ""
    )

    return _TierStateContext(
        tier=tier,
        base_tier=base_tier,
        pending_tier=pending_tier,
        has_subscription=has_subscription,
        base_level=base_level,
        tier_level=tier_level,
        pending_end=pending_end,
        override_tier=override_tier,
        override_level=override_level,
        override_end=override_end,
    )


def _anonymous_visitor_state(tier):
    if tier.slug == "free":
        return _state("", "", "Create an account", "signup")
    return _state("", "", "Join", "checkout")


def _has_stale_subscription(context):
    return context.has_subscription and (
        context.base_tier is None or context.base_tier.level == 0
    )


def _stale_subscription_state(context):
    if context.tier.slug == "free":
        return _state(
            "Included",
            "Your subscription needs review.",
            "Manage Subscription",
            "portal",
        )
    return _state(
        "Manage Subscription",
        "Your subscription needs review before changing plans.",
        "Manage Subscription",
        "portal",
    )


def _free_tier_state(context):
    if context.pending_tier and context.pending_tier.slug == "free":
        note = "Free access continues after your paid access ends."
        if context.pending_end:
            note = f"Free access continues after {context.pending_end}."
        return _state("Included", note, "Manage Subscription", "portal")

    if context.base_level == 0:
        return _state(
            "Current free plan",
            "You are on the free membership.",
            "Current plan",
            "disabled",
        )

    return _state(
        "Included",
        "Included with every paid membership.",
        "Included",
        "disabled",
    )


def _pending_cancellation_state(context):
    if not (context.pending_tier and context.pending_tier.slug == "free"):
        return None

    if context.tier == context.base_tier:
        note = "Access ends at the end of your billing period."
        if context.pending_end:
            note = f"Access ends on {context.pending_end}."
        return _state(
            "Access ending",
            note,
            "Manage Subscription",
            "portal",
        )

    return _state(
        "",
        "Your subscription is already scheduled to cancel.",
        "Manage Subscription",
        "portal",
    )


def _current_base_paid_tier_state(context):
    if not (context.tier == context.base_tier and context.base_level > 0):
        return None

    note = ""
    if (
        context.override_tier
        and context.override_level > context.base_level
    ):
        note = (
            "Base subscription. Temporary "
            f"{context.override_tier.name} access is active."
        )
    return _state("Current plan", note, "Current plan", "disabled")


def _active_override_state(context):
    override_applies = (
        context.override_tier
        and context.override_level > context.base_level
        and context.tier_level <= context.override_level
    )
    if not override_applies:
        return None

    if context.tier == context.override_tier:
        note = "Temporary access is active."
        if context.override_end:
            note = f"Temporary access active until {context.override_end}."
        return _state(
            "Temporary access",
            note,
            "Manage Subscription",
            "portal",
        )

    if context.tier_level > context.base_level:
        return _state(
            "Temporary access",
            f"Included with your temporary {context.override_tier.name} access.",
            "Manage Subscription",
            "portal",
        )

    return None


def _default_paid_tier_state(context):
    if context.base_level == 0:
        return _state("", "", "Upgrade", "checkout")

    if context.tier_level > context.base_level:
        if context.has_subscription:
            return _state(
                "",
                "Manage your subscription to switch to this tier.",
                "Manage Subscription",
                "portal",
            )
        return _state("", "", "Upgrade", "checkout")

    return _state(
        "",
        "Manage your subscription to switch to this tier.",
        "Downgrade",
        "portal",
    )


def build_tier_state(tier, user, active_override):
    if not user.is_authenticated:
        return _anonymous_visitor_state(tier)

    context = _build_context(tier, user, active_override)

    if _has_stale_subscription(context):
        return _stale_subscription_state(context)

    if tier.slug == "free":
        return _free_tier_state(context)

    pending_cancellation_state = _pending_cancellation_state(context)
    if pending_cancellation_state is not None:
        return pending_cancellation_state

    current_base_paid_tier_state = _current_base_paid_tier_state(context)
    if current_base_paid_tier_state is not None:
        return current_base_paid_tier_state

    active_override_state = _active_override_state(context)
    if active_override_state is not None:
        return active_override_state

    return _default_paid_tier_state(context)
