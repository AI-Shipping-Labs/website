from django.contrib import admin

from accounts.models import EmailAlias


@admin.register(EmailAlias)
class EmailAliasAdmin(admin.ModelAdmin):
    """Admin configuration for the EmailAlias model."""

    list_display = [
        "email",
        "user",
        "source",
        "created_by",
        "created_at",
    ]
    list_filter = ["source"]
    search_fields = ["email", "user__email", "created_by__email"]
    raw_id_fields = ["user", "created_by"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]
