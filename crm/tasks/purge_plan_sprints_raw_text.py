"""Bound retention for raw #plan-sprints Slack message text."""

from django.db import transaction
from django.utils import timezone

from crm.models import SlackMessage, SlackThread
from crm.services.slack_note_sync import sync_thread_to_interview_note
from integrations.config import get_config

DEFAULT_RETENTION_DAYS = 365


def _retention_days():
    try:
        value = int(get_config(
            'PLAN_SPRINTS_RAW_TEXT_RETENTION_DAYS', DEFAULT_RETENTION_DAYS,
        ))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return value if value > 0 else DEFAULT_RETENTION_DAYS


def purge_plan_sprints_raw_text():
    """Redact expired raw text and rebuild canonical notes without it."""
    cutoff = timezone.now() - timezone.timedelta(days=_retention_days())
    expired = SlackMessage.objects.filter(posted_at__lt=cutoff).exclude(text='')
    thread_ids = list(expired.values_list('thread_id', flat=True).distinct())
    if not thread_ids:
        return {'messages_redacted': 0, 'threads_refreshed': 0}

    with transaction.atomic():
        messages_redacted = expired.update(text='')
        threads_refreshed = 0
        threads = (
            SlackThread.objects
            .filter(pk__in=thread_ids, member__isnull=False)
            .select_related('member', 'plan__sprint', 'interview_note')
            .prefetch_related('messages')
        )
        for thread in threads:
            sync_thread_to_interview_note(thread)
            threads_refreshed += 1

    return {
        'messages_redacted': messages_redacted,
        'threads_refreshed': threads_refreshed,
    }
