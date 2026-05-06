from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

IMPORT_SOURCE_MANUAL = "manual"
IMPORT_SOURCE_SLACK = "slack"
IMPORT_SOURCE_COURSE_DB = "course_db"
IMPORT_SOURCE_STRIPE = "stripe"

IMPORT_SOURCE_CHOICES = [
    (IMPORT_SOURCE_MANUAL, "Manual / self signup"),
    (IMPORT_SOURCE_SLACK, "Slack workspace"),
    (IMPORT_SOURCE_COURSE_DB, "Course database"),
    (IMPORT_SOURCE_STRIPE, "Stripe customers"),
]

IMPORT_BATCH_SOURCE_CHOICES = [
    (IMPORT_SOURCE_SLACK, "Slack workspace"),
    (IMPORT_SOURCE_COURSE_DB, "Course database"),
    (IMPORT_SOURCE_STRIPE, "Stripe customers"),
]


class UserManager(BaseUserManager):
    """Custom user manager where email is the unique identifier."""

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email)
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Custom user model with email as the primary identifier.

    Includes profile, payment, and community fields as defined in
    specs 01 (membership tiers) and 02 (payments).
    """

    # Remove username field; email is the identifier
    username = None
    email = models.EmailField("email address", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    # Profile fields
    email_verified = models.BooleanField(
        default=False,
        help_text="Whether the user's email has been verified.",
    )
    # Lifecycle of an unverified email-signup account (issue #452).
    # ``verification_expires_at`` is set on email-only registration to
    # ``now + UNVERIFIED_USER_TTL_DAYS`` (default 7 days). Cleared to
    # NULL when the user verifies. Social signups never set this field
    # because OAuth providers already verify the address. Existing rows
    # are left NULL by the migration so the policy only applies forward.
    verification_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "If set, the unverified user is hard-deleted by the daily "
            "purge task once this timestamp is in the past."
        ),
    )
    verification_reminder_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the one-shot 'verify or expire' reminder email was "
            "sent. Prevents the daily reminder task from spamming."
        ),
    )
    unsubscribed = models.BooleanField(
        default=False,
        help_text="Whether the user has unsubscribed from emails.",
    )
    soft_bounce_count = models.PositiveSmallIntegerField(
        default=0,
        help_text=(
            "Running count of transient (soft) SES bounces. Reset to 0 once "
            "the user is auto-unsubscribed at the configured threshold."
        ),
    )
    email_preferences = models.JSONField(
        default=dict,
        blank=True,
        help_text="User email preferences as a JSON object.",
    )

    # Payment fields (spec 02)
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
    tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.PROTECT,
        related_name="users",
        null=True,
        blank=True,
        help_text="Current membership tier. Defaults to 'free' on creation.",
    )
    billing_period_end = models.DateTimeField(
        null=True,
        blank=True,
        help_text="End of the current billing period. Null for free users.",
    )
    pending_tier = models.ForeignKey(
        "payments.Tier",
        on_delete=models.SET_NULL,
        related_name="pending_users",
        null=True,
        blank=True,
        help_text="Tier scheduled after downgrade at billing_period_end.",
    )

    # Community fields (spec 09)
    slack_user_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Slack user ID for community integration.",
    )
    slack_member = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether the user's email is verified to be in the Slack workspace.",
    )
    slack_checked_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the user's Slack workspace membership was last verified.",
    )

    # UI preferences
    theme_preference = models.CharField(
        max_length=10,
        blank=True,
        default="",
        help_text="User's preferred theme: 'dark', 'light', or '' (follow system).",
    )
    preferred_timezone = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text="IANA timezone used for signed-in event time display.",
    )

    # Operator-managed contact tags (issue #354). Free-form short slugs
    # ("early-adopter", "ai-hero-waitlist") used by Studio for segmentation.
    # Always stored as a list of normalized strings (see
    # accounts/utils/tags.py); empty list when there are no tags. SEPARATE
    # namespace from content tags -- never rendered on public pages.
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="Operator-managed contact tags (Studio-only; staff-only data).",
    )

    # Import provenance for bulk-created or bulk-reconciled users.
    import_source = models.CharField(
        max_length=32,
        choices=IMPORT_SOURCE_CHOICES,
        default=IMPORT_SOURCE_MANUAL,
        db_index=True,
        help_text="Earliest non-manual source that imported or reconciled this user.",
    )
    imported_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the import pipeline first created or reconciled this user.",
    )
    import_metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Source-keyed import metadata for audit/debugging, not querying.",
    )

    class Meta:
        ordering = ["-date_joined"]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        """Assign default 'free' tier on creation if no tier is set."""
        if self.pk is None and self.tier_id is None:
            from payments.models import Tier

            try:
                self.tier = Tier.objects.get(slug="free")
            except Tier.DoesNotExist:
                pass
        super().save(*args, **kwargs)
