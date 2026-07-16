from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('integrations', '0024_mavenenrollmentevent_cohort_key_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='webhooklog', name='attempts',
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name='webhooklog', name='deduplication_key',
            field=models.CharField(
                blank=True,
                help_text='Provider delivery fingerprint; NULL for legacy logs.',
                max_length=128,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='webhooklog', name='error_message',
            field=models.TextField(blank=True, db_default='', default=''),
        ),
        migrations.AddField(
            model_name='webhooklog', name='processed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name='webhooklog',
            index=models.Index(
                fields=['service', 'processed', 'received_at'],
                name='integration_service_7b4a40_idx',
            ),
        ),
    ]
