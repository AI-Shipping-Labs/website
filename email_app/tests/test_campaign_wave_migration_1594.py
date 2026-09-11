"""Migration coverage for monitored campaign waves (#1594)."""

from contextlib import redirect_stdout
from io import StringIO

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = [("email_app", "0024_emaillog_ses_msgid_idx")]
POST_MIGRATION = [
    ("email_app", "0025_alter_emailcampaign_audience_verification_and_more"),
]


def migrate_to(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    with redirect_stdout(StringIO()):
        executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


class CampaignWaveMigrationTest(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        with redirect_stdout(StringIO()):
            executor.migrate(executor.loader.graph.leaf_nodes())

    def test_adds_wave_schema_without_changing_historical_deliveries(self):
        old_apps = migrate_to(PRE_MIGRATION)
        OldCampaign = old_apps.get_model("email_app", "EmailCampaign")
        OldDelivery = old_apps.get_model("email_app", "CampaignDelivery")
        campaign = OldCampaign.objects.create(
            subject="Historical campaign",
            body="Body",
            status="sent",
        )
        delivery = OldDelivery.objects.create(
            campaign=campaign,
            recipient_user_pk=4242,
            recipient_email="historical@example.com",
            state="sent",
        )

        new_apps = migrate_to(POST_MIGRATION)
        NewCampaign = new_apps.get_model("email_app", "EmailCampaign")
        NewDelivery = new_apps.get_model("email_app", "CampaignDelivery")
        NewWave = new_apps.get_model("email_app", "CampaignWave")

        migrated_campaign = NewCampaign.objects.get(pk=campaign.pk)
        migrated_delivery = NewDelivery.objects.get(pk=delivery.pk)
        self.assertEqual(migrated_delivery.recipient_email, "historical@example.com")
        self.assertEqual(migrated_delivery.state, "sent")
        self.assertIsNone(migrated_delivery.wave_id)
        choice_values = {value for value, _label in NewCampaign._meta.get_field("audience_verification").choices}
        self.assertIn("unverified_only", choice_values)

        wave = NewWave.objects.create(campaign=migrated_campaign, number=1)
        migrated_delivery.wave_id = wave.pk
        migrated_delivery.save(update_fields=["wave"])
        self.assertEqual(
            NewDelivery.objects.get(pk=delivery.pk).wave_id,
            wave.pk,
        )
