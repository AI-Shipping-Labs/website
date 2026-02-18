from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


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
    unsubscribed = models.BooleanField(
        default=False,
        help_text="Whether the user has unsubscribed from emails.",
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
