"""Regression tests for Relay contact-verify 403s and partial-key stats reads."""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaign_progress import read_campaign_relay_progress
from email_app.tasks.send_campaign import send_campaign_batch

User = get_user_model()


def _mock_response(status_code, payload=None, *, html_text=""):
    response = MagicMock()
    response.status_code = status_code
    if html_text:
        response.headers = {"Content-Type": "text/html"}
        response.text = html_text
        response.json.side_effect = ValueError("not json")
    else:
        response.headers = {"Content-Type": "application/json"}
        response.text = ""
        response.json.return_value = payload or {}
    return response


class RelayContactVerifyResilienceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user1 = User.objects.create_user(
            email="ok-member@example.com",
            password="pw",
            email_verified=True,
        )
        cls.user2 = User.objects.create_user(
            email="suppressed-member@example.com",
            password="pw",
            email_verified=True,
        )

    def setUp(self):
        self.campaign = EmailCampaign.objects.create(
            subject="Workshop",
            body="Body",
            status="sending",
            audience_verification="verified_only",
            relay_campaign_state="pending",
        )
        self.delivery1 = CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.user1,
            recipient_user_pk=self.user1.pk,
            recipient_email=self.user1.email,
            state=CampaignDelivery.State.PENDING,
        )
        self.delivery2 = CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=self.user2,
            recipient_user_pk=self.user2.pk,
            recipient_email=self.user2.email,
            state=CampaignDelivery.State.PENDING,
        )

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.time.sleep")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_contact_403_json_treated_as_suppressed_and_edge_403_retried(
        self, mock_request, mock_sleep,
    ):
        mock_request.side_effect = [
            _mock_response(404, {}),  # GET /api/campaigns/{key}
            _mock_response(200, {"recipient_list": {}}),  # reconcile
            _mock_response(200, {"recipient_list": {}}),  # bulk-upsert
            _mock_response(403, html_text="<html>WAF</html>"),  # edge 403 on user1
            _mock_response(200, {"contact": {}}),  # retry succeeds on user1
            _mock_response(403, {"error": "Contact is unsubscribed"}),  # JSON 403 on user2
            _mock_response(201, {"campaign": {"status": "draft"}}),  # PUT campaign
            _mock_response(200, {"recipient_count": 1, "skipped_count": 1}),  # recount
            _mock_response(200, {
                "queued": True,
                "recipient_count": 1,
                "skipped_count": 1,
            }),  # queue
        ]

        result = send_campaign_batch(
            self.campaign.pk,
            delivery_ids=[self.delivery1.pk, self.delivery2.pk],
            external_key="aishl-campaign-test-resilience",
            send_delay=0,
        )

        self.assertTrue(result["relay_queued"])
        self.assertEqual(mock_sleep.call_count, 1)
        self.delivery1.refresh_from_db()
        self.delivery2.refresh_from_db()
        self.assertEqual(self.delivery1.state, CampaignDelivery.State.SENT)
        self.assertEqual(self.delivery2.state, CampaignDelivery.State.SKIPPED)
        self.assertEqual(self.delivery2.skip_reason, "suppressed_at_relay")

    @override_settings(
        RELAY_BASE_URL="https://relay.test.datatalks.club",
        RELAY_API_KEY="test-relay-key-123",
    )
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_read_campaign_relay_progress_ignores_uncreated_retry_key(self, mock_request):
        self.campaign.relay_campaign_key = "aishl-campaign-18"
        self.campaign.relay_campaign_keys = [
            "aishl-campaign-18",
            "aishl-campaign-18-resume-1",
        ]
        self.campaign.relay_campaign_state = "failed"
        self.campaign.save(update_fields=[
            "relay_campaign_key", "relay_campaign_keys", "relay_campaign_state",
        ])
        stats_payload = {
            "campaign": {
                "status": "sent",
                "sent_count": 182,
                "delivered_count": 182,
                "unique_open_count": 58,
                "open_count": 78,
                "unique_click_count": 1,
                "click_count": 1,
                "unsubscribe_count": 3,
                "bounce_count": 0,
                "complaint_count": 0,
                "recipient_count": 182,
                "skipped_count": 0,
            },
        }
        mock_request.side_effect = [
            _mock_response(200, stats_payload),
            _mock_response(404, {"error": "not found"}),
        ]

        read = read_campaign_relay_progress(self.campaign)

        self.assertIsNotNone(read)
        self.assertEqual(read.totals["sent_count"], 182)
        self.assertEqual(read.totals["status"], "sent")
