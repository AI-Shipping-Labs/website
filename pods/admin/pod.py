from django.contrib import admin

from pods.models import (
    AvailabilityProfile,
    AvailabilityWindow,
    Pod,
    PodJoinRequest,
    PodMembership,
)

__all__ = []


class PodMembershipInline(admin.TabularInline):
    model = PodMembership
    extra = 0
    raw_id_fields = ('user', 'added_by')


@admin.register(Pod)
class PodAdmin(admin.ModelAdmin):
    list_display = ('name', 'cohort', 'status', 'max_members', 'owner', 'source', 'created_at')
    list_filter = ('status', 'source')
    search_fields = ('name', 'purpose')
    raw_id_fields = ('cohort', 'sprint', 'owner', 'created_by')
    inlines = [PodMembershipInline]


@admin.register(PodJoinRequest)
class PodJoinRequestAdmin(admin.ModelAdmin):
    list_display = ('pod', 'user', 'status', 'created_at', 'decided_at')
    list_filter = ('status',)
    raw_id_fields = ('pod', 'user', 'decided_by')


class AvailabilityWindowInline(admin.TabularInline):
    model = AvailabilityWindow
    extra = 0


@admin.register(AvailabilityProfile)
class AvailabilityProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'timezone', 'updated_at')
    raw_id_fields = ('user',)
    inlines = [AvailabilityWindowInline]
