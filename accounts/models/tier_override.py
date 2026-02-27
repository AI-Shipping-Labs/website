from django.conf import settings
from django.db import models


class TierOverride(models.Model):
    """Temporary tier upgrade for a user.

    Allows admins to grant time-limited access to a higher tier for trials,
    promotions, or courtesy access. The override sits on top of the real
    subscription tier (user.tier) so there are no conflicts with Stripe
    webhooks.

    Constraint: only one active override per user at a time. Creating a new
    override deactivates any existing active one.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tier_overrides",
    )
    original_tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.PROTECT,
        related_name="overrides_from",
        null=True,
        blank=True,
        help_text="User's tier when override was created (for audit).",
    )
    override_tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.PROTECT,
        related_name="overrides_to",
        help_text="The upgraded tier.",
    )
    expires_at = models.DateTimeField(
        help_text="When the override expires (UTC).",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="granted_overrides",
        help_text="Admin who created the override.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(
        default=True,
        help_text="Set False on expiry or manual revocation.",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["user", "is_active"],
                name="tieroverride_user_active_idx",
            ),
        ]

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return (
            f"TierOverride({self.user_id} -> "
            f"{self.override_tier_id}, {status})"
        )
