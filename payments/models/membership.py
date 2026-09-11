from django.db import models


class Membership(models.Model):
    """Membership/billing profile for one user (issue #1579, phase 1 expand).

    Carries the five tier/Stripe fields that used to live on
    ``accounts.User`` (``tier``, ``pending_tier``, ``billing_period_end``,
    ``stripe_customer_id``, ``subscription_id``). The old ``User`` columns
    remain physically present during the expand window but no new-code path
    reads or writes them; the contract migration removing them ships later.

    Invariant: every ``User`` row has exactly one ``Membership`` row. The
    row is created by the post-create receiver in ``payments.apps`` for new
    users, by the data migration for existing rows, and lazily by
    :meth:`for_user` as the fallback.
    """

    user = models.OneToOneField(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="membership",
        help_text="The user this membership profile belongs to.",
    )
    tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.PROTECT,
        related_name="memberships",
        null=True,
        blank=True,
        help_text="Current membership tier. Defaults to 'free' on creation.",
    )
    pending_tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.SET_NULL,
        related_name="pending_memberships",
        null=True,
        blank=True,
        help_text="Tier scheduled after downgrade at billing_period_end.",
    )
    billing_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the current billing period. Null for free users.",
    )
    stripe_customer_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stripe customer ID.",
    )
    subscription_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Stripe/MoR subscription ID.",
    )

    def __str__(self):
        return f"Membership({self.user_id}, tier={self.tier_id})"

    @classmethod
    def for_user(cls, user):
        """Return the user's Membership row, creating it lazily when missing.

        Reads the cached reverse relation first so querysets built with
        ``select_related("membership__tier")`` (or a cached instance) pay
        no extra query. Race-safe: the fallback uses ``get_or_create`` so
        two concurrent creators converge on one row. The created row gets
        the ``free`` Tier; when the ``free`` Tier row does not exist the
        row is created with ``tier=None`` — the same fallback
        ``User.save()`` had.
        """
        try:
            return user.membership
        except cls.DoesNotExist:
            pass
        from payments.models import Tier

        free_tier = Tier.objects.filter(slug="free").first()
        membership, _created = cls.objects.get_or_create(
            user=user,
            defaults={"tier": free_tier},
        )
        return membership
