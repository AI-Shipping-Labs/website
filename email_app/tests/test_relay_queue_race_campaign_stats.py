"""A Relay 404 between begin_native_queue and Relay creating the row is not an outage.

begin_native_queue stores ``relay_campaign_key`` before the submit call, so a
GET /api/campaigns/<id> in that window reads a key Relay does not know yet.
"""

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from accounts.models import Token
from email_app.models import EmailCampaign
from email_app.services.relay_campaign_progress import begin_native_queue
from email_app.services.relay_campaigns import RelayCampaignApiError
from integrations.config import clear_config_cache

RELAY_NOT_FOUND_BODY = {"error": {"code": "validation_error", "fields": {"external_key": "not_found"}}}


def _relay_response(status, body):
    response = MagicMock()
    response.status_code = status
    response.headers = {"Content-Type": "application/json"}
    response.json.return_value = body
    response.text = json.dumps(body)
    response.content = response.text.encode()
    return response


def _relay_request(response):
    """Patch the Relay HTTP client; yields the mocked ``Session.request``."""
    session_cls = MagicMock()
    session_cls.return_value.request.return_value = response
    return _SessionPatch(session_cls)


class _SessionPatch:
    def __init__(self, session_cls):
        self._session_cls = session_cls
        self._patch = patch("email_app.services.relay_campaigns.requests.Session", session_cls)

    def __enter__(self):
        self._patch.start()
        return self._session_cls.return_value.request

    def __exit__(self, *exc):
        self._patch.stop()


@tag("core")
@override_settings(RELAY_BASE_URL="https://relay.example", RELAY_API_KEY="fixture-relay-key")
class CampaignDetailQueueRaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        staff = get_user_model().objects.create_user(
            email="staff-queue-race@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=staff, name="queue-race")

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.campaign = EmailCampaign.objects.create(
            subject="Queue race", body="Body", status="sending",
        )

    def _get(self):
        return self.client.get(
            f"/api/campaigns/{self.campaign.pk}",
            HTTP_AUTHORIZATION=f"Token {self.token.key}",
        )

    def _relay_404(self):
        return _relay_request(_relay_response(404, RELAY_NOT_FOUND_BODY))

    @patch("email_app.services.relay_campaign_progress.alert_relay_campaign_failure")
    def test_reserved_key_missing_in_relay_returns_200_pending(self, mock_alert):
        begin_native_queue(self.campaign, "aishl-campaign-25")

        with self._relay_404() as mock_request:
            response = self._get()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], self.campaign.pk)
        self.assertEqual(body["subject"], "Queue race")
        self.assertEqual(body["relay_campaign_state"], "pending")
        self.assertFalse(body["relay_stats_available"])
        self.assertEqual(body["relay_stats_status"], "pending")
        self.assertIsNone(body["relay_stats"])
        self.assertEqual(body["relay_campaign_error"], "")
        mock_alert.assert_not_called()
        # Read-only: exactly one Relay GET, never a submit or resume call.
        self.assertEqual(mock_request.call_count, 1)
        self.assertEqual(mock_request.call_args.args[0], "GET")
        self.assertIn("/api/campaigns/aishl-campaign-25?", mock_request.call_args.args[1])

    @patch("email_app.services.relay_campaign_progress.alert_relay_campaign_failure")
    def test_queued_state_missing_row_still_fails_loudly(self, mock_alert):
        begin_native_queue(self.campaign, "aishl-campaign-25")
        EmailCampaign.objects.filter(pk=self.campaign.pk).update(relay_campaign_state="queued")

        with self._relay_404():
            response = self._get()

        self.assertEqual(response.status_code, 502)
        mock_alert.assert_called_once()

    @patch("email_app.services.relay_campaign_progress.alert_relay_campaign_failure")
    def test_sent_state_single_key_404_still_fails_loudly(self, mock_alert):
        begin_native_queue(self.campaign, "aishl-campaign-25")
        EmailCampaign.objects.filter(pk=self.campaign.pk).update(relay_campaign_state="sent")

        with self._relay_404():
            response = self._get()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "relay_campaign_stats_unavailable")
        mock_alert.assert_called_once()
        self.assertIsInstance(mock_alert.call_args.args[2], RelayCampaignApiError)

    @patch("email_app.services.relay_campaign_progress.alert_relay_campaign_failure")
    def test_pending_state_relay_outage_still_fails_loudly(self, mock_alert):
        begin_native_queue(self.campaign, "aishl-campaign-25")

        with _relay_request(_relay_response(500, {"error": "boom"})), patch("email_app.services.relay_campaigns.time.sleep"):
            response = self._get()

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "relay_campaign_stats_unavailable")
        mock_alert.assert_called_once()

    @patch("email_app.services.relay_campaign_progress.alert_relay_campaign_failure")
    def test_pending_returns_stats_once_relay_creates_row(self, mock_alert):
        begin_native_queue(self.campaign, "aishl-campaign-25")
        stats = {
            "status": "sending", "sent_count": 3, "delivered_count": 2,
            "unique_open_count": 0, "open_count": 0, "unique_click_count": 0,
            "click_count": 0, "unsubscribe_count": 0, "bounce_count": 0,
            "complaint_count": 0, "pending_count": 7,
        }

        with _relay_request(_relay_response(200, {"campaign": stats})):
            response = self._get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["relay_stats_status"], "available")
        self.assertEqual(response.json()["relay_stats"]["sent_count"], 3)
        mock_alert.assert_not_called()
