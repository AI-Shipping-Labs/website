"""ConversionAttribution: frozen per-conversion snapshot of UTM attribution.

Append-only model. One row per paid Stripe checkout session. Created by
``payments.services._record_conversion_attribution`` on
``checkout.session.completed`` events. Never updated after insert — later
changes to the user's :class:`analytics.models.UserAttribution` (e.g. new
visit with new UTMs) MUST NOT mutate this row. This is what makes
"MRR per campaign" stable in the dashboard (#196).

Note on backfill: pre-shipping subscriptions are NOT backfilled. Users
who paid before this model was added have zero ``ConversionAttribution``
rows. The dashboard handles this gracefully — campaigns whose conversions
all predate this feature show 0 paid conversions / 0 MRR (not an error).
"""

from django.conf import settings
from django.db import models

BILLING_PERIOD_CHOICES = [
    ("monthly", "Monthly"),
    ("yearly", "Yearly"),
]


class ConversionAttribution(models.Model):
    """Frozen snapshot of attribution data at the moment of a paid conversion.

    Created exactly once per Stripe ``checkout.session.completed`` event.
    Never updated after insert — the row reflects the user's attribution
    state at conversion time and stays stable as the user's
    ``UserAttribution`` evolves over time (re-visits, new UTMs, etc.).

    Two-layer idempotency:
      1. ``WebhookEvent.stripe_event_id`` (event-level guard)
      2. ``ConversionAttribution.stripe_session_id`` unique constraint
         (belt-and-braces, in case the same session somehow generates
         two distinct event IDs).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="conversion_attributions",
        help_text="The user who paid. PROTECT so we never silently drop "
                  "attribution rows when a user is hard-deleted.",
    )
    stripe_session_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Stripe Checkout session ID. Unique constraint provides a "
                  "second idempotency layer beyond WebhookEvent.",
    )
    stripe_subscription_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stripe subscription ID for subscription mode; blank for "
                  "one-off course purchases.",
    )
    tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text="The tier purchased at this conversion (snapshot — do "
                  "not follow later tier changes). Null for one-off course "
                  "purchases where there is no tier.",
    )
    billing_period = models.CharField(
        max_length=20,
        choices=BILLING_PERIOD_CHOICES,
        blank=True,
        default="",
        help_text="Inferred from which Stripe price ID was used at checkout. "
                  "Blank for one-off course purchases.",
    )
    amount_eur = models.IntegerField(
        null=True,
        blank=True,
        help_text="Price the user paid, in whole EUR (matches Tier.price_eur_* "
                  "convention). Null for free/comped or course-purchase flows "
                  "where tier price doesn't apply.",
    )
    mrr_eur = models.IntegerField(
        null=True,
        blank=True,
        help_text="Normalized monthly recurring revenue in EUR for this "
                  "conversion: amount_eur for monthly, amount_eur // 12 for "
                  "yearly, null for one-off.",
    )

    # First-touch snapshot (copied from UserAttribution.first_touch_*)
    first_touch_utm_source = models.CharField(max_length=255, blank=True, default="")
    first_touch_utm_medium = models.CharField(max_length=255, blank=True, default="")
    first_touch_utm_campaign = models.CharField(max_length=255, blank=True, default="")
    first_touch_utm_content = models.CharField(max_length=255, blank=True, default="")
    first_touch_utm_term = models.CharField(max_length=255, blank=True, default="")
    first_touch_campaign = models.ForeignKey(
        "integrations.UtmCampaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Resolved by matching first-touch utm_campaign to "
                  "UtmCampaign.slug at conversion time. Null when no "
                  "matching campaign exists.",
    )

    # Last-touch snapshot (copied from UserAttribution.last_touch_*)
    last_touch_utm_source = models.CharField(max_length=255, blank=True, default="")
    last_touch_utm_medium = models.CharField(max_length=255, blank=True, default="")
    last_touch_utm_campaign = models.CharField(max_length=255, blank=True, default="")
    last_touch_utm_content = models.CharField(max_length=255, blank=True, default="")
    last_touch_utm_term = models.CharField(max_length=255, blank=True, default="")
    last_touch_campaign = models.ForeignKey(
        "integrations.UtmCampaign",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Resolved by matching last-touch utm_campaign to "
                  "UtmCampaign.slug at conversion time.",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Conversion timestamp (when checkout.session.completed "
                  "was processed).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["first_touch_utm_campaign"]),
            models.Index(fields=["last_touch_utm_campaign"]),
        ]
        verbose_name = "Conversion Attribution"
        verbose_name_plural = "Conversion Attributions"

    def __str__(self):
        tier_part = self.tier.slug if self.tier else "course"
        return f"{self.user_id} {tier_part} {self.billing_period or 'one-off'} ({self.stripe_session_id})"


__all__ = ["ConversionAttribution", "BILLING_PERIOD_CHOICES"]
