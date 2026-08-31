"""Migration coverage for quarantining legacy in-flight campaigns."""

from contextlib import redirect_stdout
from io import StringIO

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

PRE_MIGRATION = [("email_app", "0021_reconcile_emaillog_subject_default")]
POST_MIGRATION = [("email_app", "0022_emailcampaign_audience_snapshotted_at_and_more")]


def migrate_to(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    with redirect_stdout(StringIO()):
        executor.migrate(targets)
    return MigrationExecutor(connection).loader.project_state(targets).apps


class CampaignDeliveryMigrationTest(TransactionTestCase):
    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        with redirect_stdout(StringIO()):
            executor.migrate(executor.loader.graph.leaf_nodes())

    def test_only_legacy_sending_is_quarantined_without_delivery_backfill(self):
        old_apps = migrate_to(PRE_MIGRATION)
        OldCampaign = old_apps.get_model("email_app", "EmailCampaign")
        campaigns = {
            status: OldCampaign.objects.create(
                subject=f"Legacy {status}",
                body="Hi",
                status=status,
            )
            for status in ("draft", "sending", "sent")
        }

        new_apps = migrate_to(POST_MIGRATION)
        NewCampaign = new_apps.get_model("email_app", "EmailCampaign")
        CampaignDelivery = new_apps.get_model("email_app", "CampaignDelivery")

        self.assertEqual(
            NewCampaign.objects.get(pk=campaigns["draft"].pk).status,
            "draft",
        )
        self.assertEqual(
            NewCampaign.objects.get(pk=campaigns["sending"].pk).status,
            "needs_attention",
        )
        self.assertEqual(
            NewCampaign.objects.get(pk=campaigns["sent"].pk).status,
            "sent",
        )
        self.assertEqual(CampaignDelivery.objects.count(), 0)
