"""R1 migration probes for the per-channel Slack ingest lease."""

import sys

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


def _drop_orphaned_migration_modules():
    """Evict imported ``<app>.migrations`` modules whose parent is gone.

    Under the parallel runner another test may have pruned a parent app
    package (e.g. ``booking``) from ``sys.modules`` while its
    ``migrations`` submodule stayed imported. Django's MigrationLoader
    then ``reload()``s the orphaned submodule at executor construction
    and dies with ``ImportError: parent ... not in sys.modules``.
    Dropping the orphaned entries first makes the loader re-import them
    fresh from disk.
    """
    for name in list(sys.modules):
        if name.endswith('.migrations') or '.migrations.' in name:
            parent = name.rpartition('.')[0]
            if parent and parent not in sys.modules:
                del sys.modules[name]


class SlackIngestLeaseMigrationTest(TransactionTestCase):
    migrate_from = [('crm', '0007_slackthread_interview_note')]
    migrate_to = [('crm', '0008_slack_ingest_lease_and_refresh_count')]

    def _fresh_executor(self):
        _drop_orphaned_migration_modules()
        return MigrationExecutor(connection)

    def test_duplicate_legacy_running_rows_survive_r1_expand(self):
        executor = self._fresh_executor()
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

            executor = self._fresh_executor()
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
            executor = self._fresh_executor()
            executor.migrate(latest_targets)
