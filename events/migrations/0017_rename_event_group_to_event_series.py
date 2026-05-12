"""Rename ``EventGroup`` to ``EventSeries`` and ``Event.event_group`` to
``Event.event_series`` (issue #575).

Uses :class:`migrations.RenameModel` and :class:`migrations.RenameField`
so existing rows survive the rename. The trailing ``AlterField`` calls
update the FK target reference (``events.eventgroup`` ->
``events.eventseries``) and the human-facing ``help_text`` strings to
match the renamed concept; they are pure metadata operations and do not
touch row data. The historical migrations
``0014_event_groups_and_origin`` and ``0015_backfill_event_origin`` are
left untouched as the audit trail of how the table was originally
created.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0016_event_external_host'),
        # Force ``plans.0008_sprint_event_group`` to run BEFORE the rename so
        # that the historical migration sees the original ``EventGroup``
        # model name when adding the FK column. Without this dep, Django's
        # graph would happily run events 0017 first (renaming the model)
        # and then plans 0008's ``to='events.eventgroup'`` reference would
        # fail to resolve. See issue #575.
        ('plans', '0008_sprint_event_group'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='EventGroup',
            new_name='EventSeries',
        ),
        migrations.RenameField(
            model_name='event',
            old_name='event_group',
            new_name='event_series',
        ),
        migrations.AlterField(
            model_name='event',
            name='event_series',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Optional parent series. Deleting the series preserves '
                    'the events but unlinks them.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='events',
                to='events.eventseries',
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='series_position',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='1-indexed position within the parent event series.',
                null=True,
            ),
        ),
    ]
