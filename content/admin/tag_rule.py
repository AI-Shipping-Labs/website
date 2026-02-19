from django.contrib import admin

from content.models import TagRule


@admin.register(TagRule)
class TagRuleAdmin(admin.ModelAdmin):
    list_display = ['tag', 'component_type', 'position', 'created_at', 'updated_at']
    list_filter = ['position', 'component_type']
    search_fields = ['tag', 'component_type']
    readonly_fields = ['id', 'created_at', 'updated_at']

    fieldsets = (
        (None, {
            'fields': ('tag', 'component_type', 'component_config', 'position'),
        }),
        ('Info', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )
