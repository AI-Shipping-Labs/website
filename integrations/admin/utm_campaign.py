"""Admin registration for UTM campaign models."""

from django.contrib import admin

from integrations.models import UtmCampaign, UtmCampaignLink
from studio.admin_links import studio_link


@admin.register(UtmCampaign)
class UtmCampaignAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'slug', 'default_utm_source', 'default_utm_medium',
        'is_archived', 'created_at', 'studio_link',
    ]
    list_filter = ['is_archived', 'default_utm_source', 'default_utm_medium']
    search_fields = ['name', 'slug', 'notes']
    readonly_fields = ['created_at', 'updated_at', 'studio_link']

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_utm_campaign_detail',
            lambda o: {'campaign_id': o.pk},
        )


@admin.register(UtmCampaignLink)
class UtmCampaignLinkAdmin(admin.ModelAdmin):
    list_display = [
        'campaign', 'utm_content', 'label', 'destination',
        'is_archived', 'created_at', 'studio_link',
    ]
    list_filter = ['is_archived', 'campaign']
    search_fields = ['utm_content', 'label', 'destination', 'campaign__slug', 'campaign__name']
    readonly_fields = ['created_at', 'updated_at', 'studio_link']
    autocomplete_fields = ['campaign']

    @admin.display(description='Studio')
    def studio_link(self, obj):
        return studio_link(
            obj,
            'studio_utm_link_edit',
            lambda o: {'campaign_id': o.campaign_id, 'link_id': o.pk},
        )
