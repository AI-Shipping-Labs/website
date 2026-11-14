"""Campaigns go to Relay in ONE request; AISL keeps no send queue or pacing."""

from unittest.mock import patch

from django.test import TestCase, tag
from django_q.models import Schedule

from email_app.models import EmailCampaign, EmailLog
from email_app.services.campaign_dispatch import claim_and_submit_campaign
from email_app.services.relay_campaigns import (
    RELAY_CAMPAIGN_AUDIENCE,
    RELAY_CAMPAIGN_CLIENT,
    submit_native_campaign,
)
from tests.fixtures import TierSetupMixin, create_user_with_membership


@tag("core")
class CampaignSubmitTest(TierSetupMixin, TestCase):
    def setUp(self):
        for i in range(5):
            create_user_with_membership(
                email=f"member{i}@test.com", tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )
        create_user_with_membership(
            email="unsub@test.com", tier=self.free_tier,
            email_verified=True, unsubscribed=True,
        )
        self.campaign = EmailCampaign.objects.create(
            subject="Hello", body="# Body", target_min_level=0, status="draft",
        )

    @patch("email_app.services.campaign_submit.submit_native_campaign")
    def test_submits_all_recipients_in_one_call_without_local_queue(self, submit):
        submit.return_value = {"queued": True}
        with patch("time.sleep") as sleep:
            result = claim_and_submit_campaign(self.campaign.pk, source="test")

        self.assertTrue(result.claimed)
        self.assertEqual(submit.call_count, 1)
        kwargs = submit.call_args.kwargs
        self.assertEqual(kwargs["external_key"], f"aishl-campaign-{self.campaign.pk}")
        emails = {r["email"] for r in kwargs["recipients"]}
        self.assertEqual(emails, {f"member{i}@test.com" for i in range(5)})
        # No AISL-side pacing, worker queue or batch schedules.
        sleep.assert_not_called()
        self.assertEqual(Schedule.objects.count(), 0)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "sending")
        self.assertEqual(self.campaign.relay_campaign_state, "queued")
        self.assertEqual(EmailLog.objects.filter(campaign=self.campaign).count(), 5)

    @patch("email_app.services.campaign_submit.alert_relay_campaign_failure")
    @patch("email_app.services.campaign_submit.submit_native_campaign")
    def test_failed_submit_returns_draft_with_error_and_can_resend(self, submit, alert):
        submit.side_effect = RuntimeError("relay down")
        with self.assertRaises(RuntimeError):
            claim_and_submit_campaign(self.campaign.pk, source="test")
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "draft")
        self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertIn("relay down", self.campaign.relay_campaign_error)
        alert.assert_called_once()

        submit.side_effect = None
        submit.return_value = {"queued": True}
        self.assertTrue(claim_and_submit_campaign(self.campaign.pk, source="test").claimed)

    @patch("email_app.services.campaign_submit.submit_native_campaign")
    def test_second_claim_never_submits_again(self, submit):
        submit.return_value = {"queued": True}
        claim_and_submit_campaign(self.campaign.pk, source="test")
        self.assertFalse(claim_and_submit_campaign(self.campaign.pk, source="test").claimed)
        self.assertEqual(submit.call_count, 1)

    @patch("email_app.services.campaign_submit.submit_native_campaign")
    def test_no_recipients_marks_sent_without_calling_relay(self, submit):
        EmailCampaign.objects.filter(pk=self.campaign.pk).update(target_min_level=99)
        claim_and_submit_campaign(self.campaign.pk, source="test")
        submit.assert_not_called()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "sent")


@tag("core")
class SubmitNativeCampaignRequestTest(TestCase):
    @patch("email_app.services.relay_campaigns._campaign_api_request")
    def test_one_post_to_submit_endpoint(self, request):
        request.return_value = {"queued": True}
        submit_native_campaign(
            external_key="aishl-campaign-7", subject="S", template_key="t",
            recipients=[{"email": "a@test.com"}],
        )
        self.assertEqual(request.call_count, 1)
        args = request.call_args
        self.assertEqual(args.args, ("POST", "/api/campaigns/aishl-campaign-7/submit"))
        self.assertEqual(args.kwargs["payload"]["audience"], RELAY_CAMPAIGN_AUDIENCE)
        self.assertEqual(args.kwargs["payload"]["client"], RELAY_CAMPAIGN_CLIENT)
