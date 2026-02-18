from django.db import models


class Tier(models.Model):
    """Membership tier defining access level and pricing."""

    slug = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    level = models.IntegerField(
        unique=True,
        help_text="Access level (0, 10, 20, 30). Higher = more access.",
    )
    price_eur_month = models.IntegerField(
        null=True,
        blank=True,
        help_text="Monthly price in EUR. Null for free tier.",
    )
    price_eur_year = models.IntegerField(
        null=True,
        blank=True,
        help_text="Yearly price in EUR. Null for free tier.",
    )
    stripe_price_id_monthly = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stripe Price ID for monthly billing.",
    )
    stripe_price_id_yearly = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stripe Price ID for yearly billing.",
    )
    description = models.TextField(
        blank=True,
        default="",
        help_text="Short description shown on the pricing page.",
    )
    features = models.JSONField(
        default=list,
        blank=True,
        help_text="List of feature descriptions for the pricing page.",
    )

    class Meta:
        ordering = ["level"]

    def __str__(self):
        return self.name
