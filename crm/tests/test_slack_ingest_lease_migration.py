"""Migration probes for the per-channel Slack ingest lease."""

import datetime

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class SlackIngestLeaseMigrationTest(TransactionTestCase):
    migrate_from = [('crm', '0007_slackthread_interview_note')]
    migrate_to = [('crm', '0008_slack_ingest_lease_and_refresh_count')]

    def test_duplicate_running_rows_survive_forward_reverse_forward(self):
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

            older_started_at = datetime.datetime(
                2026, 7, 14, 10, 0, tzinfo=datetime.UTC,
            )
            winning_started_at = datetime.datetime(
                2026, 7, 14, 11, 0, tzinfo=datetime.UTC,
            )
            OldIngest.objects.filter(pk=oldest.pk).update(
                started_at=older_started_at,
            )
            OldIngest.objects.filter(
                pk__in=[tied_lower_pk.pk, tied_winner.pk],
            ).update(started_at=winning_started_at)

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            NewIngest = new_apps.get_model('crm', 'SlackChannelIngest')

            running_ids = list(
                NewIngest.objects.filter(
                    channel_id='C_MIGRATION_PROBE', status='running',
                ).values_list('pk', flat=True)
            )
            self.assertEqual(running_ids, [tied_winner.pk])

            for loser_id in [oldest.pk, tied_lower_pk.pk]:
                loser = NewIngest.objects.get(pk=loser_id)
                self.assertEqual(loser.status, 'error')
                self.assertIsNotNone(loser.finished_at)
                self.assertIsNone(loser.lease_expires_at)
                self.assertIn(
                    f'kept ingest #{tied_winner.pk} active', loser.error,
                )
            self.assertIn(
                'Prior diagnostic retained',
                NewIngest.objects.get(pk=oldest.pk).error,
            )
            self.assertEqual(
                NewIngest.objects.get(pk=other_channel.pk).status,
                'running',
            )
            self.assertEqual(
                NewIngest.objects.get(pk=completed.pk).error,
                'Historical success metadata',
            )
            oldest_error = NewIngest.objects.get(pk=oldest.pk).error
            oldest_finished_at = NewIngest.objects.get(
                pk=oldest.pk,
            ).finished_at

            with self.assertRaises(IntegrityError):
                NewIngest.objects.create(
                    channel_id='C_MIGRATION_PROBE', status='running',
                )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_from)
            reversed_apps = executor.loader.project_state(
                self.migrate_from,
            ).apps
            ReversedIngest = reversed_apps.get_model(
                'crm', 'SlackChannelIngest',
            )
            self.assertEqual(
                list(ReversedIngest.objects.filter(
                    channel_id='C_MIGRATION_PROBE', status='running',
                ).values_list('pk', flat=True)),
                [tied_winner.pk],
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            remigrated_apps = executor.loader.project_state(
                self.migrate_to,
            ).apps
            RemigratedIngest = remigrated_apps.get_model(
                'crm', 'SlackChannelIngest',
            )
            self.assertEqual(
                list(RemigratedIngest.objects.filter(
                    channel_id='C_MIGRATION_PROBE', status='running',
                ).values_list('pk', flat=True)),
                [tied_winner.pk],
            )
            self.assertIn(
                'Prior diagnostic retained',
                RemigratedIngest.objects.get(pk=oldest.pk).error,
            )
            self.assertEqual(
                RemigratedIngest.objects.get(pk=oldest.pk).error,
                oldest_error,
            )
            self.assertEqual(
                RemigratedIngest.objects.get(pk=oldest.pk).finished_at,
                oldest_finished_at,
            )
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(latest_targets)
