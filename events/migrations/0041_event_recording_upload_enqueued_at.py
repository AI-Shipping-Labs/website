from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0040_event_calendar_uid'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='recording_upload_enqueued_at',
            field=models.DateTimeField(
                blank=True,
                editable=False,
                help_text=(
                    'When the Zoom-to-S3 upload job was durably enqueued. Used '
                    'to deduplicate webhook delivery while preserving '
                    'failed-enqueue recovery.'
                ),
                null=True,
            ),
        ),
    ]
