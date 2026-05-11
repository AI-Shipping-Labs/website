"""Data migration: backfill ``Event.origin`` based on ``source_repo``.

Issue #564. Existing rows with a non-empty ``source_repo`` are
GitHub-origin; everything else is studio-origin (which matches the new
field's default but we set it explicitly so the intent is recorded).
"""

from django.db import migrations


def backfill_origin(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    # Rows that were synced from GitHub: source_repo is set.
    Event.objects.exclude(
        source_repo__isnull=True,
    ).exclude(
        source_repo='',
    ).update(origin='github')
    # Defensive: any remaining row keeps the default 'studio'.
    Event.objects.filter(
        source_repo__isnull=True,
    ).update(origin='studio')
    Event.objects.filter(
        source_repo='',
    ).update(origin='studio')


def reverse_noop(apps, schema_editor):
    # Reverse keeps everything as-is; the ``origin`` column is dropped
    # by the schema migration if a full reverse is required.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0014_event_groups_and_origin'),
    ]

    operations = [
        migrations.RunPython(backfill_origin, reverse_noop),
    ]
