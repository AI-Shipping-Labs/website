"""Shared tier-override revoke logic for Studio and the staff API.

Both ``studio.views.users.user_tier_override_revoke`` (the Studio revoke
button) and ``POST /api/tier-overrides/revoke`` route through this module so
there is exactly one implementation of "revoke an override": flip
``is_active`` off (the row stays as history) and write one
``tier_override_revoked`` ``CommunityAuditLog`` row naming the actor.
"""

from django.db import transaction

from community.models import CommunityAuditLog
from payments.models import TierOverride

AUDIT_ACTION = "tier_override_revoked"


def revoke_tier_override(override, *, actor):
    """Deactivate one ``TierOverride`` and audit it.

    ``actor`` is a short label for who revoked it (``staff=<email>`` from
    Studio, ``actor_token=<name>`` from the API). It lands in the audit row's
    ``details`` next to the override id, tier, expiry and source.
    """
    override.is_active = False
    override.save(update_fields=["is_active"])
    CommunityAuditLog.objects.create(
        user=override.user,
        action=AUDIT_ACTION,
        details=(
            f"{actor} override_id={override.pk} "
            f"tier={override.override_tier.slug} "
            f"expires_at={override.expires_at.isoformat()} "
            f"source={override.source or 'manual'}"
        ),
    )


def active_overrides_for(user):
    """Return the user's active override rows, the set a revoke deactivates."""
    return list(
        TierOverride.objects.filter(user=user, is_active=True)
        .select_related("override_tier", "user")
        .order_by("pk")
    )


def revoke_active_overrides(user, *, actor):
    """Revoke every active override the user holds; return the revoked rows.

    A user can hold a manual grant alongside source-specific grants (Maven),
    so "remove this member's override access" deactivates all of them. An
    empty result means there was nothing to revoke (idempotent no-op, no
    audit row).
    """
    with transaction.atomic():
        overrides = active_overrides_for(user)
        for override in overrides:
            revoke_tier_override(override, actor=actor)
    return overrides
