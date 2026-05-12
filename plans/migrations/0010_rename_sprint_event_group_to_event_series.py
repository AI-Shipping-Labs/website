"""Rename ``Sprint.event_group`` to ``Sprint.event_series`` (issue #575).

Depends on the ``events`` app's rename migration so the FK target
``EventSeries`` exists before the column is renamed. Uses
:class:`migrations.RenameField` to preserve existing FK values, then
:class:`migrations.AlterField` to update the FK target reference
(``events.eventgroup`` -> ``events.eventseries``) and the human-facing
``help_text``; the alter step is metadata-only and does not touch row
data.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0018_rename_event_group_to_event_series'),
        # Depend on ``0009_planrequest`` (the other 0009 leaf that landed
        # concurrently on main while this rename was in review) so this
        # rename is the new sole leaf node for the plans app graph.
        ('plans', '0009_planrequest'),
    ]

    operations = [
        migrations.RenameField(
            model_name='sprint',
            old_name='event_group',
            new_name='event_series',
        ),
        migrations.AlterField(
            model_name='sprint',
            name='event_series',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Optional recurring meeting series whose occurrences '
                    'are surfaced on the sprint detail page. Deleting the '
                    'series unlinks the sprint; the sprint itself is '
                    'preserved.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sprints',
                to='events.eventseries',
            ),
        ),
    ]
