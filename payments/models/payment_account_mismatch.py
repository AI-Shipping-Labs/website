from django.conf import settings
from django.db import models
from django.utils import timezone


class PaymentAccountMismatch(models.Model):
    """Operator-visible Stripe checkout identity conflict.

    These rows are diagnostics only. They intentionally do not merge accounts,
    move subscriptions, or reassign aliases.
    """

    REASON_PRIMARY_EMAIL_COLLISION = "primary_email_collision"
    REASON_ALIAS_COLLISION = "alias_collision"
    REASON_UNKNOWN_REFERENCE = "unknown_reference"
    REASON_INVALID_BINDING = "invalid_binding"
    REASON_EXPIRED_BINDING = "expired_binding"
    REASON_BINDING_USER_UNAVAILABLE = "binding_user_unavailable"
    REASON_BINDING_REUSED = "binding_reused"
    REASON_BINDING_PURPOSE_MISMATCH = "binding_purpose_mismatch"
    REASON_UNPAID_CHECKOUT = "unpaid_checkout"
    REASON_INCOMPLETE_CHECKOUT = "incomplete_checkout"
    REASON_STRIPE_MODE_MISMATCH = "stripe_mode_mismatch"
    REASON_MISSING_PRICE = "missing_price"
    REASON_TIER_MISMATCH = "tier_mismatch"
    REASON_BILLING_EMAIL_MISMATCH = "billing_email_mismatch"
    REASON_LEGACY_REFERENCE_MISMATCH = "legacy_reference_mismatch"
    REASON_SUBSCRIPTION_CONFLICT = "subscription_conflict"
    REASON_CUSTOMER_CONFLICT = "customer_conflict"
    REASON_OUT_OF_ORDER_SUBSCRIPTION_EVENT = "out_of_order_subscription_event"
    REASON_CHOICES = [
        (REASON_PRIMARY_EMAIL_COLLISION, "Primary email collision"),
        (REASON_ALIAS_COLLISION, "Alias collision"),
        (REASON_UNKNOWN_REFERENCE, "Unknown reference"),
        (REASON_INVALID_BINDING, "Invalid checkout binding"),
        (REASON_EXPIRED_BINDING, "Expired checkout binding"),
        (REASON_BINDING_USER_UNAVAILABLE, "Checkout binding user unavailable"),
        (REASON_BINDING_REUSED, "Checkout binding already used"),
        (REASON_BINDING_PURPOSE_MISMATCH, "Checkout binding purpose mismatch"),
        (REASON_UNPAID_CHECKOUT, "Checkout is not paid"),
        (REASON_INCOMPLETE_CHECKOUT, "Checkout is not complete"),
        (REASON_STRIPE_MODE_MISMATCH, "Stripe mode mismatch"),
        (REASON_MISSING_PRICE, "Checkout Price missing"),
        (REASON_TIER_MISMATCH, "Purchased tier mismatch"),
        (REASON_BILLING_EMAIL_MISMATCH, "Billing email mismatch"),
        (REASON_LEGACY_REFERENCE_MISMATCH, "Legacy reference mismatch"),
        (REASON_SUBSCRIPTION_CONFLICT, "Subscription conflict"),
        (REASON_CUSTOMER_CONFLICT, "Customer conflict"),
        (REASON_OUT_OF_ORDER_SUBSCRIPTION_EVENT, "Out-of-order subscription event"),
    ]

    STATUS_OPEN = "open"
    STATUS_RESOLVED = "resolved"
    STATUS_IGNORED = "ignored"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_RESOLVED, "Resolved"),
        (STATUS_IGNORED, "Ignored"),
    ]
    TERMINAL_STATUSES = {STATUS_RESOLVED, STATUS_IGNORED}

    stripe_session_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Stripe Checkout Session ID that produced the conflict.",
    )
    stripe_customer_id = models.CharField(max_length=255, blank=True, default="")
    stripe_subscription_id = models.CharField(max_length=255, blank=True, default="")
    stripe_email = models.EmailField(help_text="Billing email from Stripe.")
    paid_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_mismatches_as_paid_user",
        help_text=(
            "User who received entitlement from client_reference_id. "
            "Null after privacy deletion."
        ),
    )
    candidate_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment_mismatches_as_candidate_user",
        help_text="Existing primary/alias owner that collided, when known.",
    )
    reason = models.CharField(max_length=40, choices=REASON_CHOICES)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
    )
    details = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_payment_mismatches",
    )
    resolution_note = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["stripe_customer_id"]),
            models.Index(fields=["stripe_session_id"]),
            models.Index(fields=["stripe_email"]),
        ]

    def __str__(self):
        return (
            f"PaymentAccountMismatch({self.stripe_session_id}: "
            f"{self.stripe_email} -> {self.paid_user_id})"
        )

    def mark_terminal(self, *, status, note, actor):
        if status not in self.TERMINAL_STATUSES:
            raise ValueError(f"Unsupported terminal status: {status}")
        self.status = status
        self.resolution_note = note
        self.resolved_by = actor
        self.resolved_at = timezone.now()
