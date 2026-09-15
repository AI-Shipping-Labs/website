"""Migration safety coverage for the #1659 Maven ``enrollment`` ledger step."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag("slow_platform")
class MavenEnrollmentStepMigrationTest(TransactionTestCase):
    migrate_from = [("integrations", "0032_copy_content_sync_rows")]
    migrate_to = [
        ("integrations", "0033_mavenenrollmentevent_enrollment_attempted_at_and_more")
    ]

    def test_existing_occurrences_are_terminally_skipped(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldOccurrence = old_apps.get_model("integrations", "MavenEnrollmentEvent")
            old = OldOccurrence.objects.create(
                dedupe_key="legacy-before-1659",
                identity_hash="legacy-before-1659",
                lifecycle="active",
                event_type="user_cohort.enrolled",
            )

            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            migrated = new_apps.get_model(
                "integrations",
                "MavenEnrollmentEvent",
            ).objects.get(pk=old.pk)
            self.assertEqual(migrated.enrollment_status, "skipped")
            self.assertEqual(migrated.enrollment_attempts, 0)
            self.assertIsNone(migrated.enrollment_attempted_at)
            self.assertIsNone(migrated.enrollment_completed_at)
            self.assertEqual(migrated.enrollment_error, "")
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
