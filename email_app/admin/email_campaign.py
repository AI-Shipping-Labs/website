"""Low-level Django admin for inspecting and editing email campaigns."""

from django.contrib import admin

from email_app.models import EmailCampaign
from studio.admin_links import studio_link


@admin.register(EmailCampaign)
class EmailCampaignAdmin(admin.ModelAdmin):
    list_display = [
        'subject',
        'status',
        'target_level_display',
        'sent_count',
        'sent_at',
        'created_at',
        'studio_link',
    ]
    list_filter = ['status', 'target_min_level']
    search_fields = ['subject']
    ordering = ['-created_at']
    readonly_fields = [
        'status', 'sent_at', 'sent_count', 'created_at',
        'studio_link',
    ]
    fields = [
        'subject',
        'body',
        'target_min_level',
        'status',
        'sent_count',
        'sent_at',
        'created_at',
        'studio_link',
    ]

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_campaign_detail',
            lambda o: {'campaign_id': o.pk},
        )

    def get_readonly_fields(self, request, obj=None):
        """Make all fields readonly for sent/sending campaigns."""
        if obj and obj.status in ('sending', 'needs_attention', 'sent'):
            return [
                'subject', 'body', 'target_min_level',
                'status', 'sent_at', 'sent_count', 'created_at',
                'studio_link',
            ]
        return self.readonly_fields

    def get_urls(self):
        """Remove Django's unnamed legacy change-URL redirect.

        Without that compatibility route, removed nested campaign action URLs
        cannot be swallowed as an object id and redirected to a change form.
        """
        return [pattern for pattern in super().get_urls() if pattern.name]

    def target_level_display(self, obj):
        """Display the target audience label."""
        level_map = dict(EmailCampaign.TARGET_LEVEL_CHOICES)
        return level_map.get(obj.target_min_level, str(obj.target_min_level))
    target_level_display.short_description = 'Target Audience'
