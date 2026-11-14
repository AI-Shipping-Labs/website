"""Regression tests for native Relay campaign recovery, partial-send resume, and alerts."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone
from django_q.models import Schedule

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.campaign_resume import (
    CampaignResumeConflict,
    resume_campaign_batch,
    resume_pending_campaign,
)
from email_app.tasks.campaign_delivery_recovery import (
    BATCH_FUNC,
    _parse_kwargs,
    recover_campaign_deliveries,
)
from email_app.tasks.relay_campaign_watchdog import run_relay_campaign_watchdog
from notifications.services.staff_slack import SlackPostResult
from tests.fixtures import TierSetupMixin, create_user_with_membership


class NativeRelayRecoveryChunkingTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.users = [
            create_user_with_membership(
                email=f"recovery-user-{i}@example.com",
                email_verified=True,
            )
            for i in range(4)
        ]

    def test_native_relay_campaign_orphans_are_not_split_by_batch_size(self):
        campaign = EmailCampaign.objects.create(
            subject="Native Relay Recovery",
            body="Body.",
            status="sending",
            relay_campaign_state="pending",
            audience_snapshotted_at=timezone.now(),
        )
        for user in self.users:
            CampaignDelivery.objects.create(
                campaign=campaign,
                user=user,
                recipient_user_pk=user.pk,
                recipient_email=user.email,
                state=CampaignDelivery.State.PENDING,
            )

        with patch(
            "email_app.tasks.campaign_delivery_recovery._get_batch_size",
            return_value=2,
        ):
            recover_campaign_deliveries()

        schedules = list(Schedule.objects.filter(func=BATCH_FUNC))
        self.assertEqual(len(schedules), 1)
        kwargs = _parse_kwargs(schedules[0].kwargs)
        self.assertEqual(len(kwargs["delivery_ids"]), 4)

    def test_needs_attention_native_relay_campaign_is_not_auto_retried(self):
        campaign = EmailCampaign.objects.create(
            subject="Partial Failed Campaign",
            body="Body.",
            status="needs_attention",
            relay_campaign_state="failed",
            relay_campaign_key="aishl-campaign-99",
            relay_campaign_keys=["aishl-campaign-99"],
            audience_snapshotted_at=timezone.now(),
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.users[0],
            recipient_user_pk=self.users[0].pk,
            recipient_email=self.users[0].email,
            state=CampaignDelivery.State.PENDING,
        )

        recover_campaign_deliveries()

        self.assertEqual(Schedule.objects.filter(func=BATCH_FUNC).count(), 0)


class CampaignResumeServiceTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sent_user = create_user_with_membership(
            email="already-sent@example.com",
            email_verified=True,
        )
        cls.pending_user_1 = create_user_with_membership(
            email="pending-one@example.com",
            email_verified=True,
        )
        cls.pending_user_2 = create_user_with_membership(
            email="pending-two@example.com",
            email_verified=True,
        )

    def _make_partial_campaign(self):
        now = timezone.now()
        campaign = EmailCampaign.objects.create(
            subject="Partial Campaign",
            body="Body.",
            status="needs_attention",
            relay_campaign_state="failed",
            audience_snapshotted_at=now - timedelta(hours=1),
            sent_count=1,
        )
        key = f"aishl-campaign-{campaign.pk}"
        campaign.relay_campaign_key = key
        campaign.relay_campaign_keys = [key]
        campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])
        log = EmailLog.objects.create(
            campaign=campaign,
            user=self.sent_user,
            recipient_email=self.sent_user.email,
            email_type="campaign",
            subject=campaign.subject,
        )
        sent_delivery = CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.sent_user,
            recipient_user_pk=self.sent_user.pk,
            recipient_email=self.sent_user.email,
            state=CampaignDelivery.State.SENT,
            email_log=log,
            completed_at=now - timedelta(minutes=30),
        )
        pending_1 = CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.pending_user_1,
            recipient_user_pk=self.pending_user_1.pk,
            recipient_email=self.pending_user_1.email,
            state=CampaignDelivery.State.PENDING,
        )
        pending_2 = CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.pending_user_2,
            recipient_user_pk=self.pending_user_2.pk,
            recipient_email=self.pending_user_2.email,
            state=CampaignDelivery.State.PENDING,
        )
        return campaign, sent_delivery, pending_1, pending_2

    @patch("jobs.tasks.async_task", return_value="task-123")
    def test_resume_pending_campaign_enqueues_only_pending_under_new_key(self, mock_async):
        campaign, sent_delivery, pending_1, pending_2 = self._make_partial_campaign()

        result = resume_pending_campaign(campaign.pk)

        self.assertEqual(result.pending_count, 2)
        self.assertEqual(result.external_key, f"aishl-campaign-{campaign.pk}-resume-1")
        self.assertEqual(mock_async.call_count, 1)
        args = mock_async.call_args.args
        self.assertEqual(args[0], "email_app.services.campaign_resume.resume_campaign_batch")
        self.assertEqual(args[1], campaign.pk)
        self.assertEqual(args[2], [pending_1.pk, pending_2.pk])
        self.assertNotIn(sent_delivery.pk, args[2])
        self.assertEqual(args[3], f"aishl-campaign-{campaign.pk}-resume-1")

    def test_resume_rejects_draft_campaign(self):
        campaign = EmailCampaign.objects.create(
            subject="Draft",
            body="Body.",
            status="draft",
        )
        with self.assertRaises(CampaignResumeConflict) as ctx:
            resume_pending_campaign(campaign.pk)
        self.assertEqual(ctx.exception.code, "campaign_is_draft")

    @patch("email_app.tasks.send_campaign.send_campaign_batch")
    @patch("email_app.services.campaign_resume.requests.Session.request")
    def test_resume_campaign_batch_reconciles_prior_key_and_queues_remainder(
        self, mock_request, mock_send_batch,
    ):
        campaign, _sent_delivery, pending_1, pending_2 = self._make_partial_campaign()
        mock_send_batch.return_value = {"sent_count": 1}

        existing_resp = MagicMock()
        existing_resp.status_code = 200
        existing_resp.json.return_value = {"campaign": {"status": "sent"}}
        rec_resp = MagicMock()
        rec_resp.status_code = 200
        rec_resp.json.return_value = {
            "recipients": [
                {"client_recipient_key": str(pending_1.pk), "status": "sent"},
            ],
        }
        mock_request.side_effect = [existing_resp, rec_resp]

        resume_campaign_batch(
            campaign.pk,
            [pending_1.pk, pending_2.pk],
            f"aishl-campaign-{campaign.pk}-resume-1",
        )

        pending_1.refresh_from_db()
        pending_2.refresh_from_db()
        self.assertEqual(pending_1.state, CampaignDelivery.State.SENT)
        self.assertEqual(pending_2.state, CampaignDelivery.State.PENDING)
        self.assertEqual(mock_send_batch.call_count, 1)
        self.assertEqual(mock_send_batch.call_args.kwargs["delivery_ids"], [pending_2.pk])
        self.assertEqual(
            mock_send_batch.call_args.kwargs["external_key"],
            f"aishl-campaign-{campaign.pk}-resume-1",
        )


class StuckPartialCampaignAlertTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sent_user = create_user_with_membership(
            email="partial-sent@example.com",
            email_verified=True,
        )
        cls.pending_user = create_user_with_membership(
            email="partial-pending@example.com",
            email_verified=True,
        )

    def test_stuck_partial_campaign_alerts_loudly_to_slack_and_dev_email(self):
        now = timezone.now()
        campaign = EmailCampaign.objects.create(
            subject="Stuck Partial",
            body="Body.",
            status="needs_attention",
            relay_campaign_state="failed",
            relay_campaign_error="Relay queued campaign recovery could not confirm 200 local recipients.",
            relay_campaign_attention_alerted_at=now - timedelta(minutes=20),
            audience_snapshotted_at=now - timedelta(hours=1),
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.sent_user,
            recipient_user_pk=self.sent_user.pk,
            recipient_email=self.sent_user.email,
            state=CampaignDelivery.State.SENT,
        )
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.pending_user,
            recipient_user_pk=self.pending_user.pk,
            recipient_email=self.pending_user.email,
            state=CampaignDelivery.State.PENDING,
        )

        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_slack,
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                return_value=1,
            ) as mock_mail,
        ):
            first = run_relay_campaign_watchdog()
            second = run_relay_campaign_watchdog()

        self.assertGreaterEqual(first["checked"], 1)
        self.assertEqual(first["failed"], 0)
        self.assertEqual(mock_slack.call_count, 1)
        self.assertEqual(mock_mail.call_count, 1)
        self.assertEqual(mock_mail.call_args.kwargs["recipient_list"], ["dev@dtcdev.click"])
        self.assertIn("pending=1, sent=1", mock_mail.call_args.kwargs["message"])
        self.assertEqual(second["failed"], 0)
