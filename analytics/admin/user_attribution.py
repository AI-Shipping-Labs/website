"""Read-only Django admin for UserAttribution."""

from django.contrib import admin

from analytics.models import UserAttribution


@admin.register(UserAttribution)
class UserAttributionAdmin(admin.ModelAdmin):
    list_display = [
        'user',
        'signup_path',
        'first_touch_utm_campaign',
        'last_touch_utm_campaign',
        'created_at',
    ]
    list_filter = [
        'signup_path',
        'first_touch_utm_source',
        'first_touch_utm_medium',
    ]
    search_fields = [
        'user__email',
        'first_touch_utm_campaign',
        'last_touch_utm_campaign',
        'anonymous_id',
    ]
    readonly_fields = [
        'user',
        'first_touch_utm_source',
        'first_touch_utm_medium',
        'first_touch_utm_campaign',
        'first_touch_utm_content',
        'first_touch_utm_term',
        'first_touch_campaign',
        'first_touch_ts',
        'last_touch_utm_source',
        'last_touch_utm_medium',
        'last_touch_utm_campaign',
        'last_touch_utm_content',
        'last_touch_utm_term',
        'last_touch_campaign',
        'last_touch_ts',
        'signup_path',
        'anonymous_id',
        'created_at',
    ]
    date_hierarchy = 'created_at'
    list_select_related = ['user', 'first_touch_campaign', 'last_touch_campaign']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        # Allow viewing the change form (read-only fields), block edits.
        return request.user.is_superuser if obj is None else False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


__all__ = ['UserAttributionAdmin']
