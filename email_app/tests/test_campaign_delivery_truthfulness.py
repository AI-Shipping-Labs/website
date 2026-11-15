"""Regression tests for truthful campaign sent_count, reconciliation, and stop."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.campaign_resume import resume_pending_campaign
from email_app.services.relay_campaign_control import (
    CampaignStopConflict,
    stop_campaign,
)
from email_app.services.relay_campaign_progress import read_campaign_relay_progress
from email_app.tasks.send_campaign import refresh_campaign_status, send_campaign_batch
from tests.fixtures import TierSetupMixin, create_user_with_membership


def _json_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


class CampaignDeliveryTruthfulnessTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = [
            create_user_with_membership(
                email=f"dev+truth-{i}@dtcdev.click",
                email_verified=True,
            )
            for i in range(3)
        ]

    def _make_campaign(self, *, status="sending", relay_state="pending"):
        campaign = EmailCampaign.objects.create(
            subject="Truthful Campaign",
            body="Body.",
            status=status,
            relay_campaign_state=relay_state,
            audience_snapshotted_at=timezone.now() - timedelta(minutes=10),
        )
        deliveries = [
            CampaignDelivery.objects.create(
                campaign=campaign,
                user=user,
                recipient_user_pk=user.pk,
                recipient_email=user.email,
                state=CampaignDelivery.State.PENDING,
            )
            for user in self.users
        ]
        return campaign, deliveries

    @patch("email_app.tasks.send_campaign.queue_native_campaign")
    def test_queue_marks_deliveries_dispatching_and_keeps_sent_count_zero(self, mock_queue):
        campaign, deliveries = self._make_campaign()
        delivery_ids = [d.pk for d in deliveries]
        mock_queue.return_value = {
            "queued": True,
            "included_delivery_ids": delivery_ids,
            "recipient_count": 3,
            "skipped_count": 0,
        }

        send_campaign_batch(campaign.pk, delivery_ids=delivery_ids)

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sending")
        self.assertEqual(campaign.sent_count, 0)
        self.assertEqual(EmailLog.objects.filter(campaign=campaign).count(), 0)
        self.assertEqual(
            campaign.deliveries.filter(state=CampaignDelivery.State.DISPATCHING).count(),
            3,
        )

    @patch("email_app.services.relay_campaign_control.requests.Session.request")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_progress_read_updates_sent_count_and_reconciles_on_completion(
        self, mock_stats_req, mock_ctrl_req,
    ):
        campaign, deliveries = self._make_campaign(relay_state="queued")
        key = f"aishl-campaign-{campaign.pk}"
        campaign.relay_campaign_key = key
        campaign.relay_campaign_keys = [key]
        campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])
        campaign.deliveries.update(state=CampaignDelivery.State.DISPATCHING)

        sending_stats = {
            "status": "sending",
            "sent_count": 1,
            "delivered_count": 1,
            "unique_open_count": 0,
            "open_count": 0,
            "unique_click_count": 0,
            "click_count": 0,
            "unsubscribe_count": 0,
            "bounce_count": 0,
            "complaint_count": 0,
            "recipient_count": 3,
            "skipped_count": 0,
        }
        mock_stats_req.return_value = _json_response({"campaign": sending_stats})

        read_campaign_relay_progress(campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(campaign.status, "sending")

        refresh_campaign_status(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.sent_count, 1)

        sent_stats = dict(sending_stats, status="sent", sent_count=3, delivered_count=3)
        mock_stats_req.return_value = _json_response({"campaign": sent_stats})
        mock_ctrl_req.side_effect = [
            _json_response({"campaign": {"status": "sent"}}),
            _json_response({
                "recipients": [
                    {"client_recipient_key": str(d.pk), "status": "sent"}
                    for d in deliveries
                ],
            }),
        ]

        read_campaign_relay_progress(campaign)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sent")
        self.assertEqual(campaign.sent_count, 3)
        self.assertEqual(
            campaign.deliveries.filter(state=CampaignDelivery.State.SENT).count(),
            3,
        )

    @patch("email_app.services.relay_campaign_control.requests.Session.request")
    def test_stop_campaign_halts_relay_and_reverts_premature_sent_deliveries(
        self, mock_request,
    ):
        campaign, deliveries = self._make_campaign(
            status="needs_attention", relay_state="timed_out",
        )
        key = f"aishl-campaign-{campaign.pk}-resume-4"
        campaign.relay_campaign_key = key
        campaign.relay_campaign_keys = [key]
        campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])
        for d in deliveries:
            log = EmailLog.objects.create(
                campaign=campaign,
                user=d.user,
                recipient_email=d.recipient_email,
                email_type="campaign",
                subject=campaign.subject,
                ses_message_id="",
            )
            d.state = CampaignDelivery.State.SENT
            d.email_log = log
            d.save(update_fields=["state", "email_log"])

        sending_payload = {
            "campaign": {
                "status": "sending",
                "sent_count": 1,
                "delivered_count": 1,
                "unique_open_count": 0,
                "open_count": 0,
                "unique_click_count": 0,
                "click_count": 0,
                "unsubscribe_count": 0,
                "bounce_count": 0,
                "complaint_count": 0,
                "recipient_count": 3,
                "skipped_count": 0,
            },
        }
        cancelled_payload = {
            "campaign": dict(sending_payload["campaign"], status="cancelled"),
            "stopped": True,
        }
        recipients_payload = {
            "recipients": [
                {"client_recipient_key": str(deliveries[0].pk), "status": "sent"},
                {"client_recipient_key": str(deliveries[1].pk), "status": "pending"},
                {"client_recipient_key": str(deliveries[2].pk), "status": "pending"},
            ],
        }
        mock_request.side_effect = [
            _json_response(sending_payload),
            _json_response(cancelled_payload),
            _json_response(cancelled_payload),
            _json_response({"campaign": {"status": "cancelled"}}),
            _json_response(recipients_payload),
        ]

        result = stop_campaign(campaign.pk)

        self.assertEqual(result.stopped_keys, (key,))
        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.pending_count, 2)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "paused")
        self.assertEqual(campaign.relay_campaign_state, "cancelled")
        self.assertEqual(campaign.sent_count, 1)
        self.assertEqual(
            campaign.deliveries.filter(state=CampaignDelivery.State.SENT).count(), 1,
        )
        self.assertEqual(
            campaign.deliveries.filter(state=CampaignDelivery.State.PENDING).count(), 2,
        )

    def test_stop_campaign_rejects_campaign_without_relay_key(self):
        campaign, _deliveries = self._make_campaign()
        with self.assertRaises(CampaignStopConflict) as ctx:
            stop_campaign(campaign.pk)
        self.assertEqual(ctx.exception.code, "campaign_no_relay_key")

    @patch("jobs.tasks.async_task", return_value="task-resume-reconcile")
    @patch("email_app.services.relay_campaign_control.requests.Session.request")
    def test_resume_reconciles_premature_sent_deliveries_before_enqueue(
        self, mock_request, mock_async,
    ):
        campaign, deliveries = self._make_campaign(
            status="needs_attention", relay_state="timed_out",
        )
        key = f"aishl-campaign-{campaign.pk}"
        campaign.relay_campaign_key = key
        campaign.relay_campaign_keys = [key]
        campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])
        campaign.deliveries.update(state=CampaignDelivery.State.SENT)

        mock_request.side_effect = [
            _json_response({"campaign": {"status": "cancelled"}}),
            _json_response({
                "recipients": [
                    {"client_recipient_key": str(deliveries[0].pk), "status": "sent"},
                    {"client_recipient_key": str(deliveries[1].pk), "status": "pending"},
                    {"client_recipient_key": str(deliveries[2].pk), "status": "pending"},
                ],
            }),
        ]

        result = resume_pending_campaign(campaign.pk)

        self.assertEqual(result.pending_count, 2)
        self.assertEqual(mock_async.call_count, 1)
        self.assertEqual(
            mock_async.call_args.args[2],
            [deliveries[1].pk, deliveries[2].pk],
        )

    @patch("email_app.services.relay_campaign_control.requests.Session.request")
    def test_cancel_campaign_halts_relay_and_blocks_recovery_and_resume(self, mock_request):
        from email_app.services.campaign_resume import CampaignResumeConflict
        from email_app.services.relay_campaign_control import cancel_campaign
        from email_app.tasks.campaign_delivery_recovery import recover_campaign_deliveries
        from email_app.tasks.send_campaign import refresh_campaign_status

        campaign, deliveries = self._make_campaign(
            status="needs_attention", relay_state="timed_out",
        )
        key = f"aishl-campaign-{campaign.pk}"
        campaign.relay_campaign_key = key
        campaign.relay_campaign_keys = [key]
        campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])
        campaign.deliveries.update(state=CampaignDelivery.State.SENT)

        sending_payload = {
            "campaign": {
                "external_key": key,
                "status": "sending",
                "sent_count": 1,
                "delivered_count": 1,
                "unique_open_count": 0,
                "open_count": 0,
                "unique_click_count": 0,
                "click_count": 0,
                "unsubscribe_count": 0,
                "bounce_count": 0,
                "complaint_count": 0,
            },
        }
        cancelled_payload = {
            "campaign": dict(sending_payload["campaign"], status="cancelled"),
        }
        recipients_payload = {
            "recipients": [
                {"client_recipient_key": str(deliveries[0].pk), "status": "sent"},
                {"client_recipient_key": str(deliveries[1].pk), "status": "pending"},
                {"client_recipient_key": str(deliveries[2].pk), "status": "pending"},
            ],
        }
        mock_request.side_effect = [
            _json_response(sending_payload),
            _json_response(cancelled_payload),
            _json_response(cancelled_payload),
            _json_response({"campaign": {"status": "cancelled"}}),
            _json_response(recipients_payload),
        ]

        result = cancel_campaign(campaign.pk)

        self.assertEqual(result.stopped_keys, (key,))
        self.assertEqual(result.sent_count, 1)
        self.assertEqual(result.pending_count, 2)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "cancelled")
        self.assertEqual(campaign.relay_campaign_state, "cancelled")

        refresh_campaign_status(campaign.pk)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "cancelled")

        recovery = recover_campaign_deliveries()
        self.assertEqual(recovery["requeued_batches"], 0)

        with self.assertRaises(CampaignResumeConflict) as ctx:
            resume_pending_campaign(campaign.pk)
        self.assertEqual(ctx.exception.code, "campaign_cancelled")
