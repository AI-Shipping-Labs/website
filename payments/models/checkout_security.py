"""Server-side identity bindings and business-level checkout idempotency."""

import hashlib
import secrets

from django.conf import settings
from django.db import models

CHECKOUT_BINDING_PREFIX = "aisl_cb_"


class CheckoutAccountBinding(models.Model):
    """Opaque, expiring authorization to fulfill one membership checkout."""

    SOURCE_AUTHENTICATED_PRICING = "authenticated_pricing"
    PURPOSE_MEMBERSHIP_CHECKOUT = "membership_checkout"

    PERIOD_MONTHLY = "monthly"
    PERIOD_ANNUAL = "annual"
    PERIOD_CHOICES = [
        (PERIOD_MONTHLY, "Monthly"),
        (PERIOD_ANNUAL, "Annual"),
    ]

    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkout_account_bindings",
    )
    tier = models.ForeignKey("payments.Tier", on_delete=models.PROTECT)
    billing_period = models.CharField(max_length=10, choices=PERIOD_CHOICES)
    email_snapshot = models.EmailField()
    source = models.CharField(
        max_length=40,
        default=SOURCE_AUTHENTICATED_PRICING,
        editable=False,
    )
    purpose = models.CharField(
        max_length=40,
        default=PURPOSE_MEMBERSHIP_CHECKOUT,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, *, user, tier, billing_period, expires_at):
        raw_token = secrets.token_urlsafe(32)
        binding = cls.objects.create(
            token_hash=cls.hash_token(raw_token),
            user=user,
            tier=tier,
            billing_period=billing_period,
            email_snapshot=user.email,
            expires_at=expires_at,
        )
        return binding, f"{CHECKOUT_BINDING_PREFIX}{raw_token}"

    @staticmethod
    def hash_token(raw_token):
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

    @classmethod
    def from_reference(cls, reference):
        if not reference or not reference.startswith(CHECKOUT_BINDING_PREFIX):
            return None
        raw_token = reference.removeprefix(CHECKOUT_BINDING_PREFIX)
        if not raw_token:
            return None
        return cls.objects.select_related("user", "tier").filter(
            token_hash=cls.hash_token(raw_token)
        ).first()

    def __str__(self):
        return f"CheckoutAccountBinding({self.pk}, user={self.user_id})"


class CheckoutFulfillment(models.Model):
    """Exactly-once business outcome for a Stripe Checkout Session."""

    STATUS_PROCESSING = "processing"
    STATUS_FULFILLED = "fulfilled"
    STATUS_QUARANTINED = "quarantined"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Processing"),
        (STATUS_FULFILLED, "Fulfilled"),
        (STATUS_QUARANTINED, "Quarantined"),
    ]

    stripe_session_id = models.CharField(max_length=255, unique=True)
    binding = models.OneToOneField(
        CheckoutAccountBinding,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fulfillment",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkout_fulfillments",
    )
    tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    reason = models.CharField(max_length=64, blank=True, default="")
    details = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["stripe_customer_id"]),
            models.Index(fields=["stripe_subscription_id"]),
        ]

    def __str__(self):
        return f"CheckoutFulfillment({self.stripe_session_id}: {self.status})"
