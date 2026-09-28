"""Backfill section-anchor heading ids into stored event HTML (issue #1833).

Same approach as ``content/migrations/0079_backfill_heading_ids.py``: the
shared ``add_heading_ids`` helper adds missing heading ids and each changed
row is written back with a queryset ``update()``, never ``save()`` (a save
re-renders and would drop recap include expansion).

Fields covered:

- ``events.Event.description_html``
- ``events.Event.recap_html`` (synced recap, rendered with includes)
- ``events.Event.recap_notes_html``
- ``events.EventSeries.description_html``
- ``events.Host.bio_html``

The reverse is a no-op.
"""

from django.db import migrations

from content.utils.heading_ids import backfill_heading_ids

TARGETS = (
    ('Event', ('description_html', 'recap_html', 'recap_notes_html')),
    ('EventSeries', ('description_html',)),
    ('Host', ('bio_html',)),
)


def backfill(apps, schema_editor):
    using = schema_editor.connection.alias
    for model_name, fields in TARGETS:
        backfill_heading_ids(
            apps.get_model('events', model_name),
            fields,
            using=using,
            label=f'events.{model_name}',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0052_eventseries_visibility'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
