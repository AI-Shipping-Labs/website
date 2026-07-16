"""R1 migration probes for the per-channel Slack ingest lease."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SlackIngestLeaseMigrationTest(TransactionTestCase):
    migrate_from = [('crm', '0007_slackthread_interview_note')]
    migrate_to = [('crm', '0008_slack_ingest_lease_and_refresh_count')]

    def test_duplicate_legacy_running_rows_survive_r1_expand(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()

        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldIngest = old_apps.get_model('crm', 'SlackChannelIngest')

            oldest = OldIngest.objects.create(
                channel_id='C_MIGRATION_PROBE',
                status='running',
                error='Prior diagnostic retained',
            )
            tied_lower_pk = OldIngest.objects.create(
                channel_id='C_MIGRATION_PROBE',
                status='running',
            )
            tied_winner = OldIngest.objects.create(
                channel_id='C_MIGRATION_PROBE',
                status='running',
            )
            other_channel = OldIngest.objects.create(
                channel_id='C_OTHER_CHANNEL',
                status='running',
            )
            completed = OldIngest.objects.create(
                channel_id='C_MIGRATION_PROBE',
                status='success',
                error='Historical success metadata',
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewIngest = new_apps.get_model('crm', 'SlackChannelIngest')

            running_ids = set(
                NewIngest.objects.filter(
                    channel_id='C_MIGRATION_PROBE', status='running',
                ).values_list('pk', flat=True)
            )
            self.assertEqual(
                running_ids,
                {oldest.pk, tied_lower_pk.pk, tied_winner.pk},
            )
            for legacy_id in running_ids:
                legacy = NewIngest.objects.get(pk=legacy_id)
                self.assertIsNone(legacy.finished_at)
                self.assertIsNone(legacy.lease_expires_at)
                self.assertTrue(legacy.advances_watermark)
                self.assertEqual(legacy.known_threads_checked, 0)

            self.assertEqual(
                NewIngest.objects.get(pk=oldest.pk).error,
                'Prior diagnostic retained',
            )
            self.assertEqual(
                NewIngest.objects.get(pk=other_channel.pk).status,
                'running',
            )
            self.assertEqual(
                NewIngest.objects.get(pk=completed.pk).error,
                'Historical success metadata',
            )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)
