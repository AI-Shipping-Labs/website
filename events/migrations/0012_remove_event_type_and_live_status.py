from django.db import migrations, models


def live_to_upcoming(apps, schema_editor):
    Event = apps.get_model('events', 'Event')
    Event.objects.filter(status='live').update(status='upcoming')


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0011_event_content_recap'),
    ]

    operations = [
        migrations.RunPython(live_to_upcoming, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='event',
            name='event_type',
        ),
        migrations.AlterField(
            model_name='event',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('upcoming', 'Upcoming'),
                    ('completed', 'Completed'),
                    ('cancelled', 'Cancelled'),
                ],
                default='draft',
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='location',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    'Location description, such as Zoom, Discord, or an '
                    'external resource.'
                ),
                max_length=300,
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='zoom_join_url',
            field=models.URLField(
                blank=True,
                default='',
                help_text='Join URL for Zoom or custom-platform events.',
                max_length=500,
            ),
        ),
        migrations.AlterField(
            model_name='event',
            name='zoom_meeting_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Zoom meeting ID for events hosted on Zoom.',
                max_length=255,
            ),
        ),
    ]
