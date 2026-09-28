"""Backfill section-anchor heading ids into stored topic HTML (issue #1833).

Topic bodies are rendered by the community-base renderer, which already
injects heading ids, so current rows normally carry them. Rows without ids
get them from the shared ``add_heading_ids`` helper, written back with a
queryset ``update()`` (never ``save()``). Headings that already have an id
are left untouched.

Field covered: ``topics.TopicPage.body_html``. The reverse is a no-op.
"""

from django.db import migrations

from content.utils.heading_ids import backfill_heading_ids


def backfill(apps, schema_editor):
    backfill_heading_ids(
        apps.get_model('topics', 'TopicPage'),
        ('body_html',),
        using=schema_editor.connection.alias,
        label='topics.TopicPage',
    )


class Migration(migrations.Migration):

    dependencies = [
        ('topics', '0002_topic_pages_open_access'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
