from django.contrib import admin

from email_app.models import EmailLog


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ['email_type', 'user', 'sent_at', 'ses_message_id']
    list_filter = ['email_type', 'sent_at']
    search_fields = ['user__email', 'ses_message_id']
    readonly_fields = ['campaign', 'user', 'email_type', 'sent_at', 'ses_message_id']
    ordering = ['-sent_at']
