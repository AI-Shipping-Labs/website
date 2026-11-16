"""Regression tests preventing double sends on campaign resume and duplicate stats."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone
from django_q.models import OrmQ, Schedule
from django_q.signing import SignedPackage

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.campaign_resume import (
    CampaignResumeConflict,
    resume_pending_campaign,
)
from email_app.services.relay_campaign_progress import read_campaign_relay_progress
from email_app.tasks.campaign_delivery_recovery import (
    BATCH_FUNC,
    RESUME_BATCH_FUNC,
    live_batch_delivery_ids,
    recover_campaign_deliveries,
)
from tests.fixtures import TierSetupMixin, create_user_with_membership


def _json_response(payload, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


class CampaignResumeDoubleSendRegressionTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = [
            create_user_with_membership(
                email=f"resume-guard-{i}@example.com",
                email_verified=True,
            )
            for i in range(2)
        ]

    def _make_timed_out_campaign(self):
        campaign = EmailCampaign.objects.create(
            subject="Timed Out Campaign",
            body="Body.",
            status="needs_attention",
            relay_campaign_state="timed_out",
            audience_snapshotted_at=timezone.now() - timedelta(hours=1),
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

    def test_live_batch_delivery_ids_recognizes_resume_tasks_in_ormq_and_schedule(self):
        campaign, deliveries = self._make_timed_out_campaign()
        OrmQ.objects.create(
            key="resume-ormq",
            payload=SignedPackage.dumps({
                "func": RESUME_BATCH_FUNC,
                "args": (campaign.pk, [deliveries[0].pk], "aishl-campaign-x"),
                "kwargs": {},
            }),
        )
        Schedule.objects.create(
            name="resume-sched",
            func=RESUME_BATCH_FUNC,
            schedule_type=Schedule.ONCE,
            repeats=1,
            next_run=timezone.now(),
            args=str((campaign.pk, [deliveries[1].pk], "aishl-campaign-y")),
        )

        covered = live_batch_delivery_ids()

        self.assertIn(deliveries[0].pk, covered)
        self.assertIn(deliveries[1].pk, covered)

    @patch("jobs.tasks.async_task", return_value="task-resume-1")
    def test_resume_records_key_and_recovery_never_reenqueues_in_flight_resume(self, mock_async):
        campaign, deliveries = self._make_timed_out_campaign()

        result = resume_pending_campaign(campaign.pk)

        campaign.refresh_from_db()
        self.assertEqual(mock_async.call_count, 1)
        self.assertEqual(campaign.status, "sending")
        self.assertEqual(campaign.relay_campaign_state, "pending")
        self.assertEqual(campaign.relay_campaign_keys, [result.external_key])

        # Simulate the in-flight window after the worker dequeues resume_campaign_batch
        # from OrmQ before deliveries transition from PENDING to DISPATCHING.
        recovery = recover_campaign_deliveries()

        self.assertEqual(recovery["requeued_batches"], 0)
        self.assertEqual(Schedule.objects.filter(func=BATCH_FUNC).count(), 0)
        self.assertEqual(
            campaign.deliveries.filter(state=CampaignDelivery.State.PENDING).count(),
            len(deliveries),
        )

    def test_resume_rejects_duplicate_call_while_resume_is_queued(self):
        campaign, deliveries = self._make_timed_out_campaign()
        OrmQ.objects.create(
            key="resume-queued",
            payload=SignedPackage.dumps({
                "func": RESUME_BATCH_FUNC,
                "args": (campaign.pk, [d.pk for d in deliveries], f"aishl-campaign-{campaign.pk}"),
                "kwargs": {},
            }),
        )

        with self.assertRaises(CampaignResumeConflict) as ctx:
            resume_pending_campaign(campaign.pk)

        self.assertEqual(ctx.exception.code, "campaign_resume_in_progress")

    @patch("email_app.services.relay_campaigns._campaign_api_request")
    def test_progress_deduplicates_per_recipient_counts_across_multiple_keys(self, mock_api):
        campaign, deliveries = self._make_timed_out_campaign()
        key_1 = f"aishl-campaign-{campaign.pk}-resume-1"
        key_2 = f"aishl-campaign-{campaign.pk}"
        campaign.status = "sent"
        campaign.relay_campaign_state = "sent"
        campaign.relay_campaign_key = key_1
        campaign.relay_campaign_keys = [key_1, key_2]
        campaign.save(update_fields=[
            "status", "relay_campaign_state", "relay_campaign_key", "relay_campaign_keys",
        ])

        stats_1 = {
            "campaign": {
                "status": "sent",
                "sent_count": 2,
                "delivered_count": 1,
                "unique_open_count": 1,
                "open_count": 1,
                "unique_click_count": 0,
                "click_count": 0,
                "unsubscribe_count": 1,
                "bounce_count": 0,
                "complaint_count": 0,
                "recipient_count": 2,
                "skipped_count": 0,
            },
        }
        stats_2 = {
            "campaign": {
                "status": "sent",
                "sent_count": 1,
                "delivered_count": 1,
                "unique_open_count": 1,
                "open_count": 1,
                "unique_click_count": 0,
                "click_count": 0,
                "unsubscribe_count": 0,
                "bounce_count": 0,
                "complaint_count": 0,
                "recipient_count": 2,
                "skipped_count": 1,
            },
        }
        rec_1 = {
            "recipients": [
                {
                    "client_recipient_key": str(deliveries[0].pk),
                    "status": "delivered",
                    "ses_message_id": "msg-1a",
                },
                {
                    "client_recipient_key": str(deliveries[1].pk),
                    "status": "unsubscribed",
                    "ses_message_id": "msg-1b",
                },
            ],
        }
        rec_2 = {
            "recipients": [
                {
                    "client_recipient_key": str(deliveries[0].pk),
                    "status": "delivered",
                    "ses_message_id": "msg-2a",
                },
                {
                    "client_recipient_key": str(deliveries[1].pk),
                    "status": "skipped",
                    "ses_message_id": "",
                },
            ],
        }
        mock_api.side_effect = [stats_1, stats_2, rec_1, rec_2]

        read = read_campaign_relay_progress(campaign)

        campaign.refresh_from_db()
        self.assertIsNotNone(read)
        self.assertEqual(read.totals["sent_count"], 2)
        self.assertEqual(read.totals["delivered_count"], 1)
        self.assertEqual(read.totals["unsubscribe_count"], 1)
        self.assertEqual(campaign.sent_count, 2)
