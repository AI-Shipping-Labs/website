"""Backfill ``Event.title_is_auto`` for existing series occurrences (#876).

The new field defaults to ``True``. That is correct for the common case
(auto-named occurrences), but it would wrongly mark legacy operator titles
as auto and let the first series rename overwrite them. So this data
migration sets ``title_is_auto=False`` for any series occurrence whose
stored title does NOT match the auto-pattern ``"… — Session N"`` (em-dash
separator, trailing ``Session`` + integer). Rows that match the pattern
keep ``True``; non-series events are left at the default (irrelevant —
they never get renamed by the series machinery).
"""

import re

from django.db import migrations

# Auto-titles are minted as ``"{series.name} — Session {n}"``. The series
# name is free-form (may itself contain digits / dashes), so anchor on the
# em-dash separated trailing ``Session <int>`` suffix.
AUTO_TITLE_RE = re.compile(r" — Session \d+$")


def backfill(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    occurrences = Event.objects.filter(event_series__isnull=False)
    operator_ids = [
        event.pk
        for event in occurrences.only("pk", "title")
        if not AUTO_TITLE_RE.search(event.title or "")
    ]
    if operator_ids:
        Event.objects.filter(pk__in=operator_ids).update(title_is_auto=False)


def noop_reverse(apps, schema_editor):
    # The forward pass only narrows the default; nothing to restore.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0030_event_title_is_auto"),
    ]

    operations = [
        migrations.RunPython(backfill, noop_reverse),
    ]
