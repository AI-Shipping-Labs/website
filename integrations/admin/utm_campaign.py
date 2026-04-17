"""Admin registration for UTM campaign models."""

from django.contrib import admin

from integrations.models import UtmCampaign, UtmCampaignLink


@admin.register(UtmCampaign)
class UtmCampaignAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'default_utm_source', 'default_utm_medium', 'is_archived', 'created_at']
    list_filter = ['is_archived', 'default_utm_source', 'default_utm_medium']
    search_fields = ['name', 'slug', 'notes']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(UtmCampaignLink)
class UtmCampaignLinkAdmin(admin.ModelAdmin):
    list_display = ['campaign', 'utm_content', 'label', 'destination', 'is_archived', 'created_at']
    list_filter = ['is_archived', 'campaign']
    search_fields = ['utm_content', 'label', 'destination', 'campaign__slug', 'campaign__name']
    readonly_fields = ['created_at', 'updated_at']
    autocomplete_fields = ['campaign']
