"""Admin for ``EmailTemplateOverride`` (issue #455).

Operators normally edit overrides from the Studio editor, but the Django
admin is the safety net: superusers can reset or inspect rows directly.
"""

from django.contrib import admin

from email_app.models import EmailTemplateOverride


@admin.register(EmailTemplateOverride)
class EmailTemplateOverrideAdmin(admin.ModelAdmin):
    list_display = ['template_name', 'subject', 'updated_at', 'updated_by']
    search_fields = ['template_name', 'subject']
    readonly_fields = ['updated_at']
    ordering = ['template_name']
