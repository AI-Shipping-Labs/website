"""Data migration: copy Recording rows to Event records.

For each Recording with an event FK: copy recording fields to that Event.
For each Recording without an event FK: create a new Event with status='completed'.
"""

import datetime

from django.db import migrations
from django.utils import timezone


def copy_recordings_to_events(apps, schema_editor):
    Recording = apps.get_model('content', 'Recording')
    Event = apps.get_model('events', 'Event')

    for rec in Recording.objects.all():
        # Determine which Event to update
        # Recording has an 'event' FK (nullable)
        if rec.event_id:
            try:
                event = Event.objects.get(pk=rec.event_id)
            except Event.DoesNotExist:
                event = None
        else:
            event = None

        if event is None:
            # Create a new Event for standalone recordings
            # Use rec.date at 00:00 UTC for start_datetime
            start_dt = datetime.datetime.combine(
                rec.date, datetime.time.min, tzinfo=datetime.timezone.utc,
            )
            event = Event(
                title=rec.title,
                slug=rec.slug,
                description=rec.description,
                event_type='live',
                platform='zoom',
                start_datetime=start_dt,
                status='completed',
                tags=rec.tags or [],
                required_level=rec.required_level,
            )

        # Copy recording fields to the Event
        event.content_id = rec.content_id
        event.recording_url = rec.youtube_url or ''
        event.recording_s3_url = rec.s3_url or ''
        event.recording_embed_url = rec.google_embed_url or ''
        event.transcript_url = rec.transcript_url or ''
        event.transcript_text = rec.transcript_text or ''
        event.timestamps = rec.timestamps or []
        event.materials = rec.materials or []
        event.core_tools = rec.core_tools or []
        event.learning_objectives = rec.learning_objectives or []
        event.outcome = rec.outcome or ''
        event.related_course = rec.related_course or ''
        event.published = rec.published
        event.published_at = rec.published_at
        event.source_repo = rec.source_repo
        event.source_path = rec.source_path
        event.source_commit = rec.source_commit

        # Update tags and required_level from recording if the event had defaults
        if rec.tags and not event.tags:
            event.tags = rec.tags
        if rec.required_level and event.required_level == 0:
            event.required_level = rec.required_level

        event.save()


def noop(apps, schema_editor):
    """Reverse migration is a no-op (Recording data is preserved until model deletion)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0003_remove_event_recording_event_content_id_and_more'),
        ('content', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(copy_recordings_to_events, noop),
    ]
