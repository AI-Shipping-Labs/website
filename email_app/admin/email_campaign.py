from django.contrib import admin

from email_app.models import EmailCampaign


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = ['subject', 'status', 'target_min_level', 'sent_count', 'sent_at', 'created_at']
    list_filter = ['status', 'target_min_level']
    search_fields = ['subject']
    ordering = ['-created_at']
