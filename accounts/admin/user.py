from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom User model."""

    list_display = ["email", "tier", "email_verified", "date_joined"]
    list_filter = ["email_verified", "tier", "is_staff", "is_active"]
    search_fields = ["email", "first_name", "last_name"]
    ordering = ["-date_joined"]

    # Override fieldsets to remove username references
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Personal info",
            {"fields": ("first_name", "last_name")},
        ),
        (
            "Profile",
            {
                "fields": (
                    "email_verified",
                    "unsubscribed",
                    "email_preferences",
                )
            },
        ),
        (
            "Payment",
            {
                "fields": (
                    "tier",
                    "stripe_customer_id",
                    "subscription_id",
                    "billing_period_end",
                    "pending_tier",
                )
            },
        ),
        (
            "Community",
            {"fields": ("slack_user_id",)},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )
