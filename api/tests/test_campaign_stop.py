"""Tests for POST /api/campaigns/<id>/stop."""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from email_app.models import EmailCampaign
from email_app.services.relay_campaign_control import CampaignStopConflict

User = get_user_model()


class CampaignStopApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="dev+staff-stop@dtcdev.click",
            password="pass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff)

    def test_stop_endpoint_requires_auth(self):
        campaign = EmailCampaign.objects.create(subject="S", body="B", status="sending")
        resp = self.client.post(f"/api/campaigns/{campaign.pk}/stop")
        self.assertEqual(resp.status_code, 401)

    @patch("api.views.campaign_stop.stop_campaign")
    def test_stop_endpoint_returns_reconciled_counts(self, mock_stop):
        campaign = EmailCampaign.objects.create(
            subject="S", body="B", status="needs_attention",
        )
        paused = EmailCampaign(pk=campaign.pk, status="paused")
        mock_stop.return_value = SimpleNamespace(
            campaign=paused,
            stopped_keys=(f"aishl-campaign-{campaign.pk}-resume-4",),
            sent_count=196,
            pending_count=1186,
        )

        resp = self.client.post(
            f"/api/campaigns/{campaign.pk}/stop",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["stopped"])
        self.assertEqual(data["sent_count"], 196)
        self.assertEqual(data["pending_count"], 1186)
        self.assertEqual(data["status"], "paused")

    @patch(
        "api.views.campaign_stop.stop_campaign",
        side_effect=CampaignStopConflict("No Relay key", code="campaign_no_relay_key"),
    )
    def test_stop_endpoint_returns_409_on_conflict(self, _mock_stop):
        campaign = EmailCampaign.objects.create(subject="S", body="B", status="draft")
        resp = self.client.post(
            f"/api/campaigns/{campaign.pk}/stop",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "campaign_no_relay_key")

    @patch("api.views.campaign_stop.cancel_campaign")
    def test_cancel_endpoint_marks_campaign_cancelled(self, mock_cancel):
        campaign = EmailCampaign.objects.create(
            subject="S", body="B", status="needs_attention",
        )
        cancelled = EmailCampaign(
            pk=campaign.pk, status="cancelled", relay_campaign_state="cancelled",
        )
        mock_cancel.return_value = SimpleNamespace(
            campaign=cancelled,
            stopped_keys=(f"aishl-campaign-{campaign.pk}-resume-4",),
            sent_count=196,
            pending_count=1186,
        )

        resp = self.client.post(
            f"/api/campaigns/{campaign.pk}/cancel",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["cancelled"])
        self.assertEqual(data["status"], "cancelled")
        self.assertEqual(data["relay_campaign_state"], "cancelled")
        self.assertEqual(data["sent_count"], 196)
        self.assertEqual(data["pending_count"], 1186)

    @patch(
        "api.views.campaign_stop.cancel_campaign",
        side_effect=CampaignStopConflict("Already sent", code="campaign_already_sent"),
    )
    def test_cancel_endpoint_returns_409_when_already_sent(self, _mock_cancel):
        campaign = EmailCampaign.objects.create(subject="S", body="B", status="sent")
        resp = self.client.post(
            f"/api/campaigns/{campaign.pk}/cancel",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "campaign_already_sent")
