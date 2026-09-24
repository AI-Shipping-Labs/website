"""Open the topics wiki to everyone (#1804).

Two non-destructive operations:

- the ``required_level`` default moves from Basic to Open, so every
  future page is public unless a later decision says otherwise;
- a data pass moves every existing row to ``LEVEL_OPEN``. The column and
  its ``VISIBILITY_CHOICES`` constraint stay, so a future premium-topic
  decision can reuse them without another schema change.

Rows are updated with a queryset update, so ``updated_at`` (and with it
the sitemap ``lastmod``) does not churn for pages whose content did not
change. The reverse migration is a no-op: the previous per-row levels
cannot be reconstructed.
"""

from django.db import migrations, models

from content.access import LEVEL_OPEN


def set_all_topic_pages_open(apps, schema_editor):
    TopicPage = apps.get_model('topics', 'TopicPage')
    database = schema_editor.connection.alias
    TopicPage.objects.using(database).update(required_level=LEVEL_OPEN)


class Migration(migrations.Migration):

    dependencies = [
        ('topics', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='topicpage',
            name='required_level',
            field=models.PositiveIntegerField(choices=[(0, 'Open (everyone)'), (10, 'Basic and above'), (20, 'Main and above'), (30, 'Premium only')], default=0),
        ),
        migrations.RunPython(
            set_all_topic_pages_open,
            migrations.RunPython.noop,
        ),
    ]
