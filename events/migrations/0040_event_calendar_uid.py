from django.db import migrations, models


def backfill_calendar_uid(apps, schema_editor):
    """Freeze each existing event's current slug as its calendar identity."""
    Event = apps.get_model('events', 'Event')
    for event in Event.objects.only('pk', 'slug').iterator():
        Event.objects.filter(pk=event.pk).update(
            calendar_uid=f'event-{event.slug}@aishippinglabs.com',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0039_backfill_inline_bullet_description_html'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='calendar_uid',
            field=models.CharField(blank=True, default='', max_length=400),
        ),
        migrations.RunPython(
            backfill_calendar_uid,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name='event',
            name='calendar_uid',
            field=models.CharField(
                blank=True,
                editable=False,
                help_text=(
                    'Immutable iCalendar UID. Initialized from the creation-time '
                    'slug so later slug edits update the existing calendar entry.'
                ),
                max_length=400,
                null=True,
                unique=True,
            ),
        ),
    ]
