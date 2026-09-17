"""Migration safety coverage for the #1732 Maven ``tagging`` ledger step."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag("slow_platform")
class MavenTaggingStepMigrationTest(TransactionTestCase):
    migrate_from = [
        ("integrations", "0033_mavenenrollmentevent_enrollment_attempted_at_and_more")
    ]
    migrate_to = [
        ("integrations", "0034_mavenenrollmentevent_tagging_attempted_at_and_more")
    ]

    def test_existing_occurrences_are_terminally_skipped(self):
        # Cohorts 1-4 were tagged by an operator contact import outside the
        # ledger, so an occurrence that pre-dates the step must never be
        # picked up by the automatic retry pass and re-tagged.
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            OldOccurrence = old_apps.get_model("integrations", "MavenEnrollmentEvent")
            old = OldOccurrence.objects.create(
                dedupe_key="legacy-before-1732",
                identity_hash="legacy-before-1732",
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
            self.assertEqual(migrated.tagging_status, "skipped")
            self.assertEqual(migrated.tagging_attempts, 0)
            self.assertIsNone(migrated.tagging_attempted_at)
            self.assertIsNone(migrated.tagging_completed_at)
            self.assertEqual(migrated.tagging_error, "")
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
