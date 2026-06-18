"""Create and update canonical CRM notes for matched Slack threads."""

from accounts.utils.tags import normalize_tag
from crm.models import CRMRecord
from plans.interview_note_utils import (
    SLACK_NOTE_BASE_TAGS,
    SLACK_SOURCE_TYPE,
    normalize_note_tags,
    normalize_slack_note_body,
)
from plans.models import InterviewNote


def _latest_message(thread):
    messages = list(thread.messages.all())
    if not messages:
        return None
    return max(messages, key=lambda message: (message.posted_at, message.ts))


def _thread_messages(thread):
    return list(thread.messages.all().order_by('posted_at', 'id'))


def _message_author(message):
    return (message.author_display or message.slack_user_id or 'Unknown').strip()


def _message_block(message, *, include_prefix):
    text = normalize_slack_note_body(message.text)
    if not text:
        return ''
    if not include_prefix:
        return text
    timestamp = message.posted_at.strftime('%Y-%m-%d %H:%M')
    return f'{_message_author(message)} · {timestamp}\n{text}'


def build_thread_note_body(thread):
    """Return the clean readable note body for a captured Slack thread."""
    messages = _thread_messages(thread)
    include_prefix = len(messages) > 1
    blocks = [
        _message_block(message, include_prefix=include_prefix)
        for message in messages
    ]
    return '\n\n'.join(block for block in blocks if block).strip()


def slack_note_tags(thread):
    """Tags for the canonical note representing ``thread``."""
    tags = list(SLACK_NOTE_BASE_TAGS)
    sprint = getattr(getattr(thread, 'plan', None), 'sprint', None)
    if sprint is not None:
        sprint_slug = sprint.slug or sprint.name
        sprint_tag = normalize_tag(f'sprint:{sprint_slug}')
        if sprint_tag:
            tags.append(sprint_tag)
    return normalize_note_tags(tags)


def slack_note_metadata(thread, *, ingest=None):
    """Collapsed provenance metadata stored on the canonical note."""
    latest = _latest_message(thread)
    root = thread.root_message
    sprint = getattr(getattr(thread, 'plan', None), 'sprint', None)
    metadata = {
        'channel_id': thread.channel_id,
        'thread_ts': thread.thread_ts,
        'latest_message_ts': latest.ts if latest else thread.thread_ts,
        'permalink': thread.permalink,
        'author_display': _message_author(root) if root else '',
        'slack_user_id': thread.slack_user_id,
        'plan_id': thread.plan_id,
        'ingest_id': ingest.pk if ingest is not None else thread.last_seen_ingest_id,
    }
    if sprint is not None:
        metadata['sprint_name'] = sprint.name
        metadata['sprint_slug'] = sprint.slug
    return {key: value for key, value in metadata.items() if value not in (None, '')}


def sync_thread_to_interview_note(thread, *, ingest=None):
    """Upsert one internal ``InterviewNote`` for a matched Slack thread."""
    if thread.member_id is None:
        return None

    CRMRecord.objects.get_or_create(
        user_id=thread.member_id,
        defaults={'status': 'active'},
    )

    body = build_thread_note_body(thread)
    if not body:
        body = '(Slack update had no readable text.)'
    defaults = {
        'plan': thread.plan,
        'member': thread.member,
        'visibility': 'internal',
        'kind': 'general',
        'body': body,
        'tags': slack_note_tags(thread),
        'source_type': SLACK_SOURCE_TYPE,
        'source_metadata': slack_note_metadata(thread, ingest=ingest),
    }

    note = thread.interview_note
    if note is None:
        note = InterviewNote.objects.create(**defaults)
        thread.interview_note = note
        thread.save(update_fields=['interview_note'])
    else:
        for field, value in defaults.items():
            setattr(note, field, value)
        note.save(update_fields=[
            'plan',
            'member',
            'visibility',
            'kind',
            'body',
            'tags',
            'source_type',
            'source_metadata',
            'updated_at',
        ])
    return note
