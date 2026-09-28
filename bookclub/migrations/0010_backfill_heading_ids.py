"""Backfill section-anchor heading ids into stored note HTML (issue #1833).

Note bodies render through ``render_description_html``, which now keeps
heading ids. Existing rows get them from the shared ``add_heading_ids``
helper, written back with a queryset ``update()`` (never ``save()``).

Field covered: ``bookclub.Note.body_html``. The reverse is a no-op.
"""

from django.db import migrations

from content.utils.heading_ids import backfill_heading_ids


def backfill(apps, schema_editor):
    backfill_heading_ids(
        apps.get_model('bookclub', 'Note'),
        ('body_html',),
        using=schema_editor.connection.alias,
        label='bookclub.Note',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('bookclub', '0009_readerprofile_notes_public_default'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
