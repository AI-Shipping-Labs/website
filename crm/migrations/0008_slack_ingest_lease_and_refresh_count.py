from django.db import migrations, models
from django.utils import timezone


def terminalize_duplicate_running_ingests(apps, schema_editor):
    """Keep one deterministic active run per channel before uniqueness.

    ``crm.0007`` allowed overlapping ``running`` rows.  Preserve the newest
    row (breaking equal-start ties by primary key) as the run most likely to
    still be active, and retain every older row as a terminal audit record.
    """
    SlackChannelIngest = apps.get_model('crm', 'SlackChannelIngest')
    now = timezone.now()
    running = (
        SlackChannelIngest.objects
        .filter(status='running')
        .order_by('channel_id', '-started_at', '-pk')
    )

    winner_by_channel = {}
    for ingest in running.iterator():
        winner = winner_by_channel.get(ingest.channel_id)
        if winner is None:
            winner_by_channel[ingest.channel_id] = ingest
            continue

        reason = (
            'Legacy duplicate running ingest terminalized during crm.0008; '
            f'kept ingest #{winner.pk} active for this channel.'
        )
        prior_error = (ingest.error or '').strip()
        ingest.status = 'error'
        ingest.error = f'{prior_error}\n{reason}'.strip()
        ingest.finished_at = ingest.finished_at or now
        ingest.lease_expires_at = None
        ingest.save(update_fields=[
            'status', 'error', 'finished_at', 'lease_expires_at',
        ])


def preserve_terminalized_ingests(apps, schema_editor):
    """Do not resurrect duplicate active leases when reversing the schema."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0007_slackthread_interview_note'),
    ]

    operations = [
        migrations.AddField(
            model_name='slackchannelingest',
            name='advances_watermark',
            field=models.BooleanField(db_default=True, default=True),
        ),
        migrations.AddField(
            model_name='slackchannelingest',
            name='known_threads_checked',
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name='slackchannelingest',
            name='lease_expires_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='slackthread',
            name='privacy_erased',
            field=models.BooleanField(db_default=False, db_index=True, default=False),
        ),
    ]
