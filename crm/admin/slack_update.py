from django.contrib import admin

from crm.models import SlackChannelIngest, SlackMessage, SlackThread


@admin.register(SlackChannelIngest)
class SlackChannelIngestAdmin(admin.ModelAdmin):
    list_display = (
        'channel_id', 'status', 'started_at', 'finished_at',
        'messages_seen', 'threads_persisted', 'replies_added',
        'members_matched',
    )
    list_filter = ('status',)
    readonly_fields = (
        'started_at', 'finished_at', 'channel_id', 'oldest_ts',
        'latest_ts', 'messages_seen', 'threads_persisted', 'replies_added',
        'members_matched', 'status', 'error',
    )


@admin.register(SlackThread)
class SlackThreadAdmin(admin.ModelAdmin):
    list_display = (
        'thread_ts', 'channel_id', 'slack_user_id', 'member', 'plan',
        'reply_count', 'posted_at',
    )
    list_filter = ('channel_id',)
    search_fields = ('thread_ts', 'slack_user_id', 'member__email')
    raw_id_fields = ('member', 'plan', 'ingest', 'last_seen_ingest')


@admin.register(SlackMessage)
class SlackMessageAdmin(admin.ModelAdmin):
    list_display = (
        'ts', 'thread', 'slack_user_id', 'author_display', 'is_root',
        'posted_at',
    )
    list_filter = ('is_root',)
    search_fields = ('ts', 'slack_user_id', 'author_display', 'text')
    raw_id_fields = ('thread', 'first_seen_ingest')
