from django.contrib import admin

from voting.models import Poll, PollOption, PollVote


class PollOptionInline(admin.TabularInline):
    """Inline admin for poll options — lets admins add options when creating/editing a poll."""
    model = PollOption
    extra = 3
    fields = ['title', 'description', 'proposed_by']
    readonly_fields = ['proposed_by']


def close_polls(modeladmin, request, queryset):
    """Close selected polls."""
    queryset.update(status='closed')


close_polls.short_description = 'Close selected polls'


def reopen_polls(modeladmin, request, queryset):
    """Reopen selected polls."""
    queryset.update(status='open')


reopen_polls.short_description = 'Reopen selected polls'


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = [
        'title', 'poll_type', 'status', 'required_level',
        'allow_proposals', 'max_votes_per_user', 'closes_at', 'created_at',
    ]
    list_filter = ['status', 'poll_type']
    search_fields = ['title', 'description']
    actions = [close_polls, reopen_polls]
    inlines = [PollOptionInline]

    fieldsets = (
        (None, {
            'fields': (
                'title', 'description', 'poll_type',
            ),
        }),
        ('Settings', {
            'fields': (
                'status', 'allow_proposals', 'max_votes_per_user', 'closes_at',
            ),
        }),
    )

    readonly_fields = ['required_level']


@admin.register(PollOption)
class PollOptionAdmin(admin.ModelAdmin):
    list_display = ['title', 'poll', 'proposed_by', 'created_at']
    list_filter = ['poll']
    search_fields = ['title', 'description']


@admin.register(PollVote)
class PollVoteAdmin(admin.ModelAdmin):
    list_display = ['user', 'poll', 'option', 'created_at']
    list_filter = ['poll']
    search_fields = ['user__email']
