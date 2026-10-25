from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, tag


@tag("slow_platform")
class LegacyEmailRemovalMigrationTest(TransactionTestCase):
    migrate_from = [
        ("cb_config", "0001_initial"),
        ("email_app", "0026_relaytransactionaldelivery"),
        ("integrations", "0034_mavenenrollmentevent_tagging_attempted_at_and_more"),
    ]
    migrate_to = [
        ("cb_config", "0001_initial"),
        ("email_app", "0027_remove_emailtemplateoverride"),
        ("integrations", "0035_rename_relay_sender_settings"),
    ]

    def test_sender_values_survive_and_override_guard_is_rollback_safe(self):
        executor = MigrationExecutor(connection)
        latest_targets = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(self.migrate_from)
            old_apps = executor.loader.project_state(self.migrate_from).apps
            Donor = old_apps.get_model("integrations", "IntegrationSetting")
            Setting = old_apps.get_model("cb_config", "Setting")
            Override = old_apps.get_model("email_app", "EmailTemplateOverride")

            Setting.objects.create(
                key="SES_FROM_EMAIL", value="general@example.com",
                value_type="str", source="db",
            )
            Donor.objects.create(
                key="SES_TRANSACTIONAL_FROM_EMAIL",
                value="transactions@example.com", group="ses",
            )
            Setting.objects.create(
                key="RELAY_PROMOTIONAL_FROM_EMAIL", value="campaigns@example.com",
                value_type="str", source="db",
            )
            Donor.objects.create(
                key="SES_WELCOME_REPLY_TO_EMAIL",
                value="reply@example.com", group="ses",
            )
            Override.objects.create(
                template_name="welcome",
                subject="Private subject that must not enter diagnostics",
                body_markdown="Private body that must not enter diagnostics",
            )

            executor = MigrationExecutor(connection)
            with self.assertRaises(RuntimeError) as caught:
                executor.migrate(self.migrate_to)

            message = str(caught.exception)
            self.assertIn("Affected template keys: welcome", message)
            self.assertNotIn("Private subject", message)
            self.assertNotIn("Private body", message)

            # The copy migration committed before the guarded migration. Both
            # old and new rows remain so the previous release can roll back.
            apps_after_failure = MigrationExecutor(connection).loader.project_state(
                [
                    ("cb_config", "0001_initial"),
                    ("email_app", "0026_relaytransactionaldelivery"),
                    ("integrations", "0035_rename_relay_sender_settings"),
                ]
            ).apps
            FailedSetting = apps_after_failure.get_model("cb_config", "Setting")
            self.assertEqual(
                FailedSetting.objects.get(key="SES_FROM_EMAIL").value,
                "general@example.com",
            )
            self.assertEqual(
                FailedSetting.objects.get(key="RELAY_TRANSACTIONAL_FROM_EMAIL").value,
                "transactions@example.com",
            )
            self.assertEqual(
                FailedSetting.objects.get(key="RELAY_PROMOTIONAL_FROM_EMAIL").value,
                "campaigns@example.com",
            )
            self.assertEqual(
                FailedSetting.objects.get(key="RELAY_WELCOME_FROM_EMAIL").value,
                "general@example.com",
            )
            self.assertEqual(
                FailedSetting.objects.get(key="RELAY_WELCOME_REPLY_TO_EMAIL").value,
                "reply@example.com",
            )

            FailedOverride = apps_after_failure.get_model(
                "email_app", "EmailTemplateOverride"
            )
            FailedOverride.objects.all().delete()
            FailedOverride.objects.create(template_name="compatibility-empty")
            MigrationExecutor(connection).migrate(self.migrate_to)

            migrated_apps = MigrationExecutor(connection).loader.project_state(
                self.migrate_to
            ).apps
            MigratedSetting = migrated_apps.get_model("cb_config", "Setting")
            self.assertEqual(
                MigratedSetting.objects.get(key="SES_FROM_EMAIL").value,
                "general@example.com",
            )
            self.assertEqual(
                Donor.objects.get(key="SES_TRANSACTIONAL_FROM_EMAIL").value,
                "transactions@example.com",
            )
            self.assertEqual(
                MigratedSetting.objects.get(key="RELAY_WELCOME_FROM_EMAIL").value,
                "general@example.com",
            )
            with self.assertRaises(LookupError):
                migrated_apps.get_model("email_app", "EmailTemplateOverride")
            self.assertIn(
                "email_app_emailtemplateoverride",
                connection.introspection.table_names(),
            )
            self.assertEqual(FailedOverride.objects.count(), 0)
        finally:
            MigrationExecutor(connection).migrate(latest_targets)
