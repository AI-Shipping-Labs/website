from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from accounts.models import User
from studio.admin_links import studio_link


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom User model."""

    list_display = [
        "email", "tier", "import_source", "email_verified", "date_joined",
        "studio_link",
    ]
    list_filter = ["email_verified", "tier", "import_source", "is_staff", "is_active"]
    search_fields = ["email", "first_name", "last_name"]
    ordering = ["-date_joined"]
    readonly_fields = ["studio_link"]

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_user_detail',
            lambda o: {'user_id': o.pk},
        )

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
                    "preferred_timezone",
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
            {"fields": ("slack_user_id", "slack_member", "slack_checked_at")},
        ),
        (
            "Import",
            {"fields": ("import_source", "imported_at", "import_metadata", "tags")},
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
        ("Studio", {"fields": ("studio_link",)}),
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
