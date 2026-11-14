"""Tests for POST /api/campaigns/<id>/resume."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from email_app.models import CampaignDelivery, EmailCampaign
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


class CampaignResumeApiTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email="staff-resume@example.com",
            password="pass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff_user)
        cls.recipient = create_user_with_membership(
            email="resume-recipient@example.com",
            email_verified=True,
        )

    def _headers(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    @patch("jobs.tasks.async_task", return_value="task-resume-1")
    def test_resume_endpoint_enqueues_pending_deliveries(self, mock_async):
        campaign = EmailCampaign.objects.create(
            subject="Resume API",
            body="Body.",
            status="needs_attention",
            relay_campaign_state="failed",
            audience_snapshotted_at=timezone.now(),
        )
        key = f"aishl-campaign-{campaign.pk}"
        campaign.relay_campaign_key = key
        campaign.relay_campaign_keys = [key]
        campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])
        CampaignDelivery.objects.create(
            campaign=campaign,
            user=self.recipient,
            recipient_user_pk=self.recipient.pk,
            recipient_email=self.recipient.email,
            state=CampaignDelivery.State.PENDING,
        )

        resp = self.client.post(
            f"/api/campaigns/{campaign.pk}/resume",
            data="{}",
            content_type="application/json",
            **self._headers(),
        )

        data = resp.json()
        self.assertTrue(data["resumed"])
        self.assertEqual(data["pending_count"], 1)
        self.assertEqual(data["external_key"], f"aishl-campaign-{campaign.pk}-resume-1")
        self.assertEqual(data["status"], "sending")
        self.assertEqual(mock_async.call_count, 1)

    def test_resume_endpoint_rejects_draft_with_conflict(self):
        campaign = EmailCampaign.objects.create(
            subject="Draft",
            body="Body.",
            status="draft",
        )

        resp = self.client.post(
            f"/api/campaigns/{campaign.pk}/resume",
            data="{}",
            content_type="application/json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "campaign_is_draft")
