from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('integrations', '0025_webhooklog_delivery_state'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='synclog',
            index=models.Index(
                fields=['source', 'status', '-started_at'],
                name='sync_src_status_started_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='synclog',
            index=models.Index(
                fields=['batch_id', '-started_at'],
                name='sync_batch_started_idx',
            ),
        ),
    ]
