from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import UserChangeForm as BaseUserChangeForm

from accounts.models import User
from studio.admin_links import studio_link

#: What unchecking ``Active`` actually costs (issue #1745). The checkbox is one
#: of exactly two operator-reachable deactivation paths, and the only one that
#: explained nothing: ``User.save()`` runs
#: ``accounts.services.credentials.revoke_api_credentials_on_deactivation``,
#: which revokes member and community-base API keys in place and deletes
#: operator tokens outright. None of that is undone by re-checking the box.
IS_ACTIVE_HELP_TEXT = (
    "Unchecking this revokes every API credential this account owns: member "
    "and community-base API keys are revoked in place, operator tokens are "
    "deleted. They stop working immediately, and re-checking this box does "
    "not restore them — a replacement key must be issued."
)


class UserChangeForm(BaseUserChangeForm):
    """The Django admin change form, carrying the operator-only ``is_active`` copy.

    The help text lives on the FORM, not on ``User.is_active``: a model-level
    ``help_text`` would generate a migration and leak wording written for a
    superuser on this one form into every other form bound to that field.
    """

    class Meta(BaseUserChangeForm.Meta):
        model = User
        help_texts = {"is_active": IS_ACTIVE_HELP_TEXT}


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin configuration for the custom User model."""

    form = UserChangeForm

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
