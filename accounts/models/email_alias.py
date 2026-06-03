from django.conf import settings
from django.db import models


class EmailAlias(models.Model):
    """An additional email that routes to a canonical ``User`` account.

    One account can have many known emails (issue #840a). The motivating
    case is an Apple Pay relay address (e.g. ``47-gentle.virtual@icloud.com``)
    that keeps sending FUTURE Stripe payments: the alias is what makes those
    webhook events resolve to the canonical account instead of creating a
    duplicate user.

    Invariant: an ``EmailAlias.email`` MUST NOT equal any ``User.email`` --
    an address is either a primary login OR an alias, never both. The
    resolver (``accounts.services.email_resolution``) and the operator API
    enforce this; the alias resolver only fires when no ``User.email``
    matches (primary always wins).

    ``email`` is stored normalized (``User.objects.normalize_email(...).lower()``)
    and is unique across the table, so an address routes to at most one
    canonical account.
    """

    SOURCE_MERGE = "merge"
    SOURCE_MANUAL = "manual"
    SOURCE_STRIPE_RELAY = "stripe_relay"
    SOURCE_CHOICES = [
        (SOURCE_MERGE, "Added by an account merge"),
        (SOURCE_MANUAL, "Operator-added"),
        (SOURCE_STRIPE_RELAY, "Billing relay address"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_aliases",
        help_text="The canonical account this email routes to.",
    )
    email = models.EmailField(
        unique=True,
        help_text=(
            "Normalized alias email (lower-cased). Unique across the table; "
            "must not equal any primary User.email."
        ),
    )
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_MANUAL,
        help_text="How this alias came to exist.",
    )
    note = models.TextField(
        blank=True,
        default="",
        help_text="Operator context for why this alias exists.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="Operator who added the alias (NULL if deleted / system).",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Email Alias"
        verbose_name_plural = "Email Aliases"

    def __str__(self):
        return f"EmailAlias({self.email} -> {self.user_id})"
