"""Data-migration coverage for encrypted trigger secrets and envelopes."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from triggers.secrets import decrypt_secret


class SecureTriggerMigrationTest(TransactionTestCase):
    def test_plaintext_secret_and_existing_emission_are_backfilled(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            # A preceding migration test may leave accounts one migration
            # behind. Advance it independently so the trigger rewind below
            # never mixes forward and backward operations in one plan.
            accounts_leaf = executor.loader.graph.leaf_nodes("accounts")[0]
            accounts_plan = executor.migration_plan([accounts_leaf])
            self.assertTrue(
                all(
                    migration.app_label == "accounts" and not backwards
                    for migration, backwards in accounts_plan
                ),
            )
            executor.migrate([accounts_leaf])

            executor = MigrationExecutor(connection)
            migrate_from = [("triggers", "0001_initial"), accounts_leaf]
            rewind_plan = executor.migration_plan(migrate_from)
            self.assertTrue(rewind_plan)
            self.assertTrue(
                all(
                    migration.app_label == "triggers" and backwards
                    for migration, backwards in rewind_plan
                ),
            )
            executor.migrate(migrate_from)
            old_apps = executor.loader.project_state(migrate_from).apps
            User = old_apps.get_model("accounts", "User")
            Subscription = old_apps.get_model("triggers", "TriggerSubscription")
            Emission = old_apps.get_model("triggers", "EventEmission")

            user = User.objects.create(email="legacy-trigger@test.com", password="!")
            Subscription.objects.create(
                target_url="https://handler.example.com/hook",
                secret="legacy-plaintext-secret",
            )
            emission = Emission.objects.create(
                user=user,
                event_name="legacy_claim",
                properties={"name": "legacy_claim", "min_level": 5},
                envelope_id="evt_legacy",
            )
            original_created_at = emission.created_at

            executor = MigrationExecutor(connection)
            migrate_to = [
                ("triggers", "0002_secure_delivery_state"),
                accounts_leaf,
            ]
            forward_plan = executor.migration_plan(migrate_to)
            self.assertEqual(
                [
                    (migration.app_label, migration.name, backwards)
                    for migration, backwards in forward_plan
                ],
                [("triggers", "0002_secure_delivery_state", False)],
            )
            executor.migrate(migrate_to)
            new_apps = executor.loader.project_state(migrate_to).apps
            NewSubscription = new_apps.get_model("triggers", "TriggerSubscription")
            NewEmission = new_apps.get_model("triggers", "EventEmission")

            subscription = NewSubscription.objects.get()
            self.assertNotIn("legacy-plaintext-secret", subscription.encrypted_secret)
            self.assertEqual(
                decrypt_secret(subscription.encrypted_secret),
                "legacy-plaintext-secret",
            )
            self.assertEqual(subscription.legacy_secret, "legacy-plaintext-secret")
            migrated = NewEmission.objects.get(envelope_id="evt_legacy")
            self.assertEqual(migrated.occurred_at, original_created_at)
            self.assertEqual(
                migrated.envelope["occurred_at"],
                original_created_at.isoformat(),
            )
            self.assertEqual(migrated.envelope["data"]["email"], user.email)
            self.assertEqual(migrated.envelope["data"]["min_level"], 5)
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
