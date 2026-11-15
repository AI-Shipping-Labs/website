"""Cancellation remains durable when the provider or reconciliation fails."""

from unittest.mock import patch

from django.db import connection
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.campaign_batch_guard import CampaignBatchBlocked
from email_app.services.campaign_resume import CampaignResumeConflict, resume_pending_campaign
from email_app.services.relay_campaign_control import cancel_campaign, reconcile_native_campaign_deliveries
from email_app.tasks.campaign_delivery_recovery import recover_campaign_deliveries
from tests.fixtures import TierSetupMixin, create_user_with_membership


class CampaignCancelSafetyTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_user_with_membership(email="cancel-safety@example.com")

    def make_campaign(self):
        campaign = EmailCampaign.objects.create(
            subject="Cancelled", body="Body", status="needs_attention",
            relay_campaign_key="cancel-safety-key", relay_campaign_state="timed_out",
            scheduled_at=timezone.now(), scheduled_timezone="Europe/Berlin",
        )
        delivery = CampaignDelivery.objects.create(
            campaign=campaign, user=self.user, recipient_user_pk=self.user.pk,
            recipient_email=self.user.email, state=CampaignDelivery.State.PENDING,
        )
        return campaign, delivery

    @patch("email_app.services.relay_campaign_control.read_campaign_relay_progress")
    @patch("email_app.services.relay_campaign_control._halt_active_relay_keys", return_value=[])
    @patch("email_app.services.relay_campaign_control.reconcile_native_campaign_deliveries")
    def test_reconciliation_failure_blocks_recovery_and_allows_cancel_retry(self, reconcile, halt, progress):
        campaign, _delivery = self.make_campaign()
        reconcile.side_effect = RuntimeError("reconciliation unavailable")

        with self.assertRaisesMessage(RuntimeError, "reconciliation unavailable"):
            cancel_campaign(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "cancelled")
        self.assertIsNone(campaign.scheduled_at)
        self.assertEqual(campaign.scheduled_timezone, "")
        self.assertEqual(recover_campaign_deliveries()["requeued_batches"], 0)
        with self.assertRaises(CampaignResumeConflict) as context:
            resume_pending_campaign(campaign.pk)
        self.assertEqual(context.exception.code, "campaign_cancelled")
        reconcile.side_effect = None
        result = cancel_campaign(campaign.pk)
        self.assertEqual(result.campaign.status, "cancelled")
        self.assertEqual(result.campaign.relay_campaign_state, "cancelled")

    @patch("email_app.services.relay_campaign_control._campaign_api_request")
    def test_sent_without_provider_id_is_reconciled_and_confirmed_id_is_imported(self, request):
        expected_states = {"sent": CampaignDelivery.State.PENDING, "skipped": CampaignDelivery.State.SKIPPED}
        for relay_status in ("sent", "skipped"):
            with self.subTest(relay_status=relay_status):
                campaign, delivery = self.make_campaign()
                delivery.state = CampaignDelivery.State.SENT
                delivery.email_log = EmailLog.objects.create(
                    campaign=campaign, user=self.user, recipient_email=self.user.email,
                    subject=campaign.subject, email_type="campaign",
                )
                delivery.save(update_fields=["state", "email_log"])
                request.side_effect = [
                    {"campaign": {"status": "cancelled"}},
                    {"recipients": [{
                        "client_recipient_key": str(delivery.pk), "status": relay_status,
                        "sent_at": "2026-11-15T11:58:39Z",
                    }]},
                ]
                reconcile_native_campaign_deliveries(campaign, revert_unconfirmed=True)
                delivery.refresh_from_db()
                self.assertEqual(delivery.state, expected_states[relay_status])
                self.assertIsNone(delivery.email_log_id)
                self._assert_provider_import(campaign, delivery, request)

    def _assert_provider_import(self, campaign, delivery, request):
        delivery.state = CampaignDelivery.State.SENT
        delivery.email_log = EmailLog.objects.create(
            campaign=campaign, user=self.user, recipient_email=self.user.email,
            subject=campaign.subject, email_type="campaign",
        )
        delivery.save(update_fields=["state", "email_log"])
        request.side_effect = [
            {"campaign": {"status": "cancelled"}},
            {"recipients": [{
                "client_recipient_key": str(delivery.pk), "status": "unsubscribed",
                "ses_message_id": f"ses-confirmed-{delivery.pk}",
            }]},
        ]
        reconcile_native_campaign_deliveries(campaign, revert_unconfirmed=True)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(delivery.ses_message_id, f"ses-confirmed-{delivery.pk}")
        self.assertEqual(delivery.email_log.ses_message_id, f"ses-confirmed-{delivery.pk}")

    @patch("email_app.tasks.send_campaign.queue_native_campaign")
    @patch("email_app.services.campaign_resume._confirmed_keys_from_prior_campaigns")
    def test_queued_batches_cannot_dispatch_stopped_campaign_with_a_new_key(self, confirmed, queue):
        from email_app.services.campaign_resume import resume_campaign_batch
        from email_app.tasks.send_campaign import send_campaign_batch

        campaign, delivery = self.make_campaign()
        for status in ("paused", "cancelled"):
            campaign.status = status
            campaign.save(update_fields=["status"])
            with self.subTest(status=status):
                with self.assertRaises(CampaignBatchBlocked):
                    send_campaign_batch(campaign.pk, [delivery.pk], external_key="new-key")
                with self.assertRaises(CampaignBatchBlocked):
                    resume_campaign_batch(campaign.pk, [delivery.pk], "new-key")
        queue.assert_not_called()
        confirmed.assert_not_called()

    @tag("postgresql")
    @patch("email_app.services.relay_campaign_control.fetch_relay_recipient_statuses")
    def test_reconciliation_locks_delivery_with_nullable_log_on_postgresql(self, statuses):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL enforces FOR UPDATE outer join restrictions")
        campaign, delivery = self.make_campaign()
        delivery.state = CampaignDelivery.State.SENT
        delivery.save(update_fields=["state"])
        statuses.return_value = ({}, set())

        result = reconcile_native_campaign_deliveries(campaign, revert_unconfirmed=True)

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, CampaignDelivery.State.PENDING)
        self.assertEqual(result["reverted"], 1)
