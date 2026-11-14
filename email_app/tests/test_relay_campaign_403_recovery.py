"""Regression tests for Relay campaign API 403 retries, diagnostics, and 404 stats reads."""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaign_progress import (
    read_campaign_relay_progress,
    refresh_campaign_relay_stats_loud,
)
from email_app.services.relay_campaigns import (
    RELAY_CAMPAIGN_API_USER_AGENT,
    RelayCampaignApiError,
    _campaign_api_request,
)
from email_app.tasks.send_campaign import send_campaign_batch
from tests.fixtures import TierSetupMixin, create_user_with_membership


def _mock_response(status_code, *, text="", json_body=None, headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = headers or {}
    if json_body is not None:
        response.json.return_value = json_body
    else:
        response.json.side_effect = ValueError("not json")
    return response


@tag("core")
@override_settings(
    RELAY_BASE_URL="https://relay.test.datatalks.club",
    RELAY_API_KEY="test-relay-key-123",
)
class RelayCampaignApi403Tests(TierSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = create_user_with_membership(
            email="c22-member@test.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        self.campaign = EmailCampaign.objects.create(
            subject="Campaign 22",
            body="Body",
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

    @patch("email_app.services.relay_campaigns.time.sleep")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_get_retries_transient_edge_html_403_and_queues_campaign(
        self, mock_request, mock_sleep,
    ):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])
        edge_403 = _mock_response(
            403,
            text="<html><body>403 ERROR CloudFront</body></html>",
            headers={"Content-Type": "text/html", "Server": "CloudFront"},
        )
        ok_responses = [
            edge_403,
            _mock_response(404, json_body={}),
            _mock_response(200, json_body={"recipient_list": {}}),
            _mock_response(200, json_body={"recipient_list": {}}),
            _mock_response(200, json_body={"contact": {}}),
            _mock_response(201, json_body={"campaign": {"status": "draft"}}),
            _mock_response(200, json_body={"recipient_count": 1, "skipped_count": 0}),
            _mock_response(
                200,
                json_body={
                    "queued": True,
                    "recipient_count": 1,
                    "skipped_count": 0,
                    "campaign": {"status": "queued"},
                },
            ),
        ]
        mock_request.side_effect = ok_responses

        result = send_campaign_batch(
            self.campaign.pk, delivery_ids=[self.delivery.pk], send_delay=0,
        )

        self.assertTrue(result["relay_queued"])
        self.assertEqual(mock_sleep.call_count, 1)
        first_call = mock_request.call_args_list[0]
        self.assertEqual(first_call.kwargs["headers"]["User-Agent"], RELAY_CAMPAIGN_API_USER_AGENT)
        self.assertNotIn("Content-Type", first_call.kwargs["headers"])

    @patch("email_app.services.relay_campaigns.time.sleep")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_persistent_edge_403_includes_edge_diagnostic_after_retries(
        self, mock_request, mock_sleep,
    ):
        edge_403 = _mock_response(
            403,
            text="<html><body>Request blocked</body></html>",
            headers={"Content-Type": "text/html", "Server": "CloudFront", "x-amz-cf-id": "abc123"},
        )
        mock_request.side_effect = [edge_403, edge_403, edge_403]

        with self.assertRaises(RelayCampaignApiError) as ctx:
            _campaign_api_request("GET", "/api/campaigns/aishl-campaign-22", allow_not_found=True)

        self.assertEqual(mock_request.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertIn("edge_blocked=true", str(ctx.exception))
        self.assertIn("cf_id=abc123", str(ctx.exception))

    @patch("email_app.services.relay_campaigns.time.sleep")
    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_json_403_from_relay_is_not_retried(self, mock_request, mock_sleep):
        mock_request.return_value = _mock_response(
            403,
            text='{"error":{"code":"forbidden"}}',
            json_body={"error": {"code": "forbidden"}},
            headers={"Content-Type": "application/json"},
        )

        with self.assertRaises(RelayCampaignApiError) as ctx:
            _campaign_api_request("GET", "/api/campaigns/aishl-campaign-22", allow_not_found=True)

        self.assertEqual(mock_request.call_count, 1)
        self.assertEqual(mock_sleep.call_count, 0)
        self.assertNotIn("edge_blocked=true", str(ctx.exception))
        self.assertIn("forbidden", str(ctx.exception))

    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_failed_prequeue_campaign_404_does_not_fail_stats_read(self, mock_request):
        self.campaign.status = "needs_attention"
        self.campaign.relay_campaign_state = "failed"
        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [f"aishl-campaign-{self.campaign.pk}"]
        self.campaign.relay_campaign_error = "RelayCampaignApiError: HTTP 403"
        self.campaign.save()
        mock_request.return_value = _mock_response(404, json_body={})

        self.assertIsNone(refresh_campaign_relay_stats_loud(self.campaign))
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_error, "RelayCampaignApiError: HTTP 403")

    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_timed_out_prequeue_campaign_404_does_not_fail_stats_read(self, mock_request):
        self.campaign.status = "needs_attention"
        self.campaign.relay_campaign_state = "timed_out"
        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [f"aishl-campaign-{self.campaign.pk}"]
        self.campaign.relay_campaign_error = "No progress for 30 minutes. Last Relay status: not queued, 0 sent."
        self.campaign.save()
        mock_request.return_value = _mock_response(404, json_body={})

        self.assertIsNone(refresh_campaign_relay_stats_loud(self.campaign))
        self.campaign.refresh_from_db()
        self.assertEqual(
            self.campaign.relay_campaign_error,
            "No progress for 30 minutes. Last Relay status: not queued, 0 sent.",
        )

    @patch("email_app.services.relay_campaigns.requests.Session.request")
    def test_queued_campaign_404_still_raises_stats_error(self, mock_request):
        self.campaign.relay_campaign_state = "queued"
        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [f"aishl-campaign-{self.campaign.pk}"]
        self.campaign.save()
        mock_request.return_value = _mock_response(404, json_body={})

        with self.assertRaises(RelayCampaignApiError):
            read_campaign_relay_progress(self.campaign)
