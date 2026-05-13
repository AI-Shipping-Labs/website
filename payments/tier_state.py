"""Shared membership tier action state for pricing and account pages."""


def format_period_end(value):
    if value is None:
        return ""
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def build_tier_state(tier, user, active_override):
    if not user.is_authenticated:
        if tier.slug == "free":
            return {
                "badge": "",
                "note": "",
                "action_label": "Get the newsletter",
                "action_kind": "newsletter",
            }
        return {
            "badge": "",
            "note": "",
            "action_label": "Join",
            "action_kind": "checkout",
        }

    base_tier = user.tier
    pending_tier = user.pending_tier
    has_subscription = bool(user.subscription_id)
    base_level = base_tier.level if base_tier else 0
    tier_level = tier.level
    pending_end = format_period_end(user.billing_period_end)
    override_tier = active_override.override_tier if active_override else None
    override_level = override_tier.level if override_tier else 0
    override_end = format_period_end(active_override.expires_at) if active_override else ""

    has_stale_subscription = has_subscription and (
        base_tier is None or base_tier.level == 0
    )
    if has_stale_subscription:
        if tier.slug == "free":
            return {
                "badge": "Included",
                "note": "Your subscription needs review.",
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }
        return {
            "badge": "Manage Subscription",
            "note": "Your subscription needs review before changing plans.",
            "action_label": "Manage Subscription",
            "action_kind": "portal",
        }

    if tier.slug == "free":
        if pending_tier and pending_tier.slug == "free":
            note = "Free access continues after your paid access ends."
            if pending_end:
                note = f"Free access continues after {pending_end}."
            return {
                "badge": "Included",
                "note": note,
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }
        if base_level == 0:
            return {
                "badge": "Current free plan",
                "note": "You are on the free membership.",
                "action_label": "Current plan",
                "action_kind": "disabled",
            }
        return {
            "badge": "Included",
            "note": "Included with every paid membership.",
            "action_label": "Included",
            "action_kind": "disabled",
        }

    if pending_tier and pending_tier.slug == "free":
        if tier == base_tier:
            note = "Access ends at the end of your billing period."
            if pending_end:
                note = f"Access ends on {pending_end}."
            return {
                "badge": "Access ending",
                "note": note,
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }
        return {
            "badge": "",
            "note": "Your subscription is already scheduled to cancel.",
            "action_label": "Manage Subscription",
            "action_kind": "portal",
        }

    if pending_tier and pending_tier.slug != "free":
        if tier == base_tier:
            note = f"Your plan changes to {pending_tier.name} at period end."
            if pending_end:
                note = f"Your plan changes to {pending_tier.name} on {pending_end}."
            return {
                "badge": "Current plan",
                "note": note,
                "action_label": "Current plan",
                "action_kind": "disabled",
            }
        if tier == pending_tier:
            note = "Scheduled to become your plan at period end."
            if pending_end:
                note = f"Scheduled to become your plan on {pending_end}."
            return {
                "badge": "Scheduled change",
                "note": note,
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }

    if tier == base_tier and base_level > 0:
        note = ""
        if override_tier and override_level > base_level:
            note = f"Base subscription. Temporary {override_tier.name} access is active."
        return {
            "badge": "Current plan",
            "note": note,
            "action_label": "Current plan",
            "action_kind": "disabled",
        }

    if override_tier and override_level > base_level and tier_level <= override_level:
        if tier == override_tier:
            note = "Temporary access is active."
            if override_end:
                note = f"Temporary access active until {override_end}."
            return {
                "badge": "Temporary access",
                "note": note,
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }
        if tier_level > base_level:
            return {
                "badge": "Temporary access",
                "note": f"Included with your temporary {override_tier.name} access.",
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }

    if base_level == 0:
        return {
            "badge": "",
            "note": "",
            "action_label": "Upgrade",
            "action_kind": "checkout",
        }

    if tier_level > base_level:
        if has_subscription:
            return {
                "badge": "",
                "note": "Manage your subscription to switch to this tier.",
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            }
        return {
            "badge": "",
            "note": "",
            "action_label": "Upgrade",
            "action_kind": "checkout",
        }

    return {
        "badge": "",
        "note": "Manage your subscription to switch to this tier.",
        "action_label": "Downgrade",
        "action_kind": "portal",
    }
