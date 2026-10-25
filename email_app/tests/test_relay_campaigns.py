"""Unit and integration tests for Relay campaign and newsletter delivery (Path B)."""

import uuid
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.urls import reverse
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.tasks.send_campaign import (
    send_campaign_batch,
)
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


@tag("core")
class RelayCampaignDeliveryTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user_with_membership(
            email=f"recipient-{uuid.uuid4().hex[:8]}@test.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        self.campaign = EmailCampaign.objects.create(
            subject="Relay Campaign Test Subject",
            body="Hello **member**, this is a test newsletter body.",
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        self.delivery = CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.user,
            recipient_user_pk=self.user.pk,
            recipient_email=self.user.email,
            state=CampaignDelivery.State.PENDING,
        )

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_send_campaign_batch_success(self, mock_request):
        """Standard campaign batch sends via Relay transactional send API and updates ledger."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {
                "id": "relay-msg-test-789",
                "status": "queued",
                "idempotency_key": f"campaign-{self.campaign.pk}-delivery-{self.delivery.pk}",
            },
            "idempotent_replay": False,
        }
        mock_request.return_value = mock_response

        result = send_campaign_batch(
            self.campaign.pk,
            delivery_ids=[self.delivery.pk],
            send_delay=0,
        )

        self.assertEqual(result["sent_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["ambiguous_count"], 0)

        # Verify Relay request payload
        self.assertEqual(mock_request.call_count, 1)
        method, url = mock_request.call_args[0]
        kwargs = mock_request.call_args[1]

        self.assertEqual(method, "POST")
        self.assertEqual(url, "https://relay.test.datatalks.club/api/transactional/send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-relay-key-123")

        payload = kwargs["json"]
        self.assertEqual(payload["email"], self.user.email)
        self.assertEqual(payload["template_key"], "campaign-broadcast")
        self.assertEqual(
            payload["idempotency_key"],
            f"campaign-{self.campaign.pk}-delivery-{self.delivery.pk}",
        )
        self.assertEqual(payload["category"], "newsletter")
        self.assertIn("headers", payload)
        self.assertIn("List-Unsubscribe", payload["headers"])
        self.assertIn("List-Unsubscribe-Post", payload["headers"])
        self.assertEqual(payload["headers"]["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")

        context = payload["context"]
        self.assertEqual(context["subject"], self.campaign.subject)
        self.assertEqual(context["body_markdown"], self.campaign.body)

        # Verify delivery ledger updated
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.SENT)
        self.assertEqual(self.delivery.ses_message_id, "relay-msg-test-789")
        self.assertIsNotNone(self.delivery.completed_at)
        self.assertIsNotNone(self.delivery.email_log)

        # Verify EmailLog created
        log = EmailLog.objects.filter(campaign=self.campaign, user=self.user).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.ses_message_id, "relay-msg-test-789")
        self.assertEqual(log.email_type, "campaign")

        # Verify campaign status updated to sent
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "sent")
        self.assertEqual(self.campaign.sent_count, 1)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_deduplication_and_idempotency_prevents_double_send(self, mock_request):
        """Deliveries already sent or with existing logs are not sent again."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"id": "relay-msg-initial", "status": "queued"},
            "idempotent_replay": False,
        }
        mock_request.return_value = mock_response

        # First run succeeds
        send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0)
        self.assertEqual(mock_request.call_count, 1)

        # Second run: delivery is already SENT
        result2 = send_campaign_batch(self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0)
        self.assertEqual(result2["sent_count"], 0)
        # Mock request was not called again
        self.assertEqual(mock_request.call_count, 1)

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_relay_rejection_fails_loud_with_error_log(self, mock_request):
        """Relay definitive rejection (409 suppression) marks delivery FAILED and logs ERROR."""
        mock_response = MagicMock()
        mock_response.status_code = 409
        mock_response.json.return_value = {
            "error": {"code": "transactional_suppressed", "reason": "hard_bounce"},
            "message": {"status": "suppressed"},
        }
        mock_request.return_value = mock_response

        with self.assertLogs("email_app.tasks.send_campaign", level="ERROR") as logs:
            result = send_campaign_batch(
                self.campaign.pk,
                delivery_ids=[self.delivery.pk],
                send_delay=0,
            )

        self.assertEqual(result["sent_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertIn("Relay definitively rejected", logs.output[0])

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.FAILED)
        self.assertIn("hard_bounce", self.delivery.last_error)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "needs_attention")

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_relay_ambiguous_failure_fails_loud_with_error_log(self, mock_request):
        """Relay network timeout marks delivery AMBIGUOUS and suppresses automatic retry."""
        mock_request.side_effect = requests.exceptions.Timeout("Read timeout from Relay")

        with self.assertLogs("email_app.tasks.send_campaign", level="ERROR") as logs:
            result = send_campaign_batch(
                self.campaign.pk,
                delivery_ids=[self.delivery.pk],
                send_delay=0,
            )

        self.assertEqual(result["sent_count"], 0)
        self.assertEqual(result["ambiguous_count"], 1)
        self.assertIn("Relay transport outcome was indeterminate", logs.output[0])

        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.state, CampaignDelivery.State.AMBIGUOUS)
        self.assertIn("timed out", self.delivery.last_error)

        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.status, "needs_attention")


@tag("core")
class RelayStudioCampaignTestSendTest(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = create_user_with_membership(
            email="staff@aishippinglabs.com",
            is_staff=True,
            tier=self.free_tier,
        )
        self.client.force_login(self.staff_user)
        self.campaign = EmailCampaign.objects.create(
            subject="Studio Test Newsletter",
            body="Hello from Studio test send.",
            status="draft",
        )

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_studio_test_send_calls_relay(self, mock_request):
        """Studio campaign test send sends via Relay with [TEST] prefix and returns success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"id": "relay-msg-studio-test", "status": "queued"},
            "idempotent_replay": False,
        }
        mock_request.return_value = mock_response

        response = self.client.post(
            reverse("studio_campaign_test_send", args=[self.campaign.pk]),
            {"test_recipients": "alexey.s.grigoriev@gmail.com"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mock_request.call_count, 1)
        payload = mock_request.call_args[1]["json"]

        self.assertEqual(payload["email"], "alexey.s.grigoriev@gmail.com")
        self.assertEqual(payload["template_key"], "campaign-broadcast")
        self.assertEqual(payload["context"]["subject"], "[TEST] Studio Test Newsletter")
        self.assertEqual(
            payload["context"]["body_markdown"],
            "Hello from Studio test send.",
        )
        self.assertTrue(payload["metadata"]["is_test_send"])
