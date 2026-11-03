"""Edge 403 circuit breaker: HTML 403 pauses the campaign, rows stay retryable."""

import uuid
from unittest.mock import patch

import requests
from django.test import TestCase, override_settings
from django.utils import timezone

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.services.relay_campaigns import (
    RelayCampaignDeliveryAmbiguous,
    RelayCampaignEdgeBlocked,
    send_campaign_delivery_to_relay,
)
from email_app.tasks.send_campaign import edge_breaker_open, send_campaign_batch
from integrations.config import clear_config_cache
from tests.fixtures import TierSetupMixin, create_user_with_membership


class FixedResponseAdapter(requests.adapters.BaseAdapter):
    def __init__(self, status, body, content_type):
        self.status, self.body, self.content_type = status, body, content_type

    def send(self, request, **kwargs):
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = self.status
        response.headers["Content-Type"] = self.content_type
        response._content = self.body
        return response

    def close(self):
        pass


@override_settings(RELAY_BASE_URL="https://relay.example", RELAY_API_KEY="fixture-key")
class RelayEdgeClassificationTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def _send(self, status, body, content_type):
        session = requests.Session()
        session.mount("https://", FixedResponseAdapter(status, body, content_type))
        return send_campaign_delivery_to_relay(
            "a@example.com", "s", "b", idempotency_key="campaign-1-delivery-1",
            session=session,
        )

    def test_html_403_is_edge_blocked(self):
        with self.assertRaises(RelayCampaignEdgeBlocked):
            self._send(403, b"<html><body>ERROR</body></html>", "text/html")

    def test_json_403_from_relay_stays_ambiguous(self):
        with self.assertRaises(RelayCampaignDeliveryAmbiguous):
            self._send(403, b'{"error":"forbidden"}', "application/json")


class EdgeBreakerBatchTest(TierSetupMixin, TestCase):
    def _campaign(self, count):
        campaign = EmailCampaign.objects.create(
            subject="c", body="Hello", status="sending",
            audience_snapshotted_at=timezone.now(),
        )
        deliveries = []
        for _ in range(count):
            user = create_user_with_membership(
                email=f"r-{uuid.uuid4().hex}@test.com", tier=self.free_tier,
                email_verified=True, unsubscribed=False,
            )
            deliveries.append(CampaignDelivery.objects.create(
                campaign=campaign, user=user, recipient_user_pk=user.pk,
                recipient_email=user.email,
            ))
        return campaign, deliveries

    def test_edge_block_stops_batch_and_keeps_rows_pending(self):
        campaign, deliveries = self._campaign(3)

        def blocked(**_kwargs):
            raise RelayCampaignEdgeBlocked("edge 403")

        with patch(
            "email_app.tasks.send_campaign._send_campaign_transport",
            side_effect=blocked,
        ) as transport:
            result = send_campaign_batch(
                campaign.pk, [d.pk for d in deliveries], send_delay=0,
            )

        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result["ambiguous_count"], 0)
        states = {d.state for d in CampaignDelivery.objects.filter(campaign=campaign)}
        self.assertEqual(states, {CampaignDelivery.State.PENDING})
        first = CampaignDelivery.objects.get(pk=deliveries[0].pk)
        self.assertEqual(first.attempt_count, 0)
        self.assertIn("edge_blocked:", first.last_error)
        self.assertTrue(edge_breaker_open(campaign.pk))

    def test_open_breaker_blocks_next_batch_without_transport(self):
        campaign, deliveries = self._campaign(2)
        CampaignDelivery.objects.filter(pk=deliveries[0].pk).update(
            last_error="edge_blocked: edge 403",
        )
        with patch("email_app.tasks.send_campaign._send_campaign_transport") as transport:
            send_campaign_batch(
                campaign.pk, [d.pk for d in deliveries], send_delay=0,
            )
        transport.assert_not_called()

    def test_cooldown_seconds_read_from_config(self):
        campaign, deliveries = self._campaign(1)
        CampaignDelivery.objects.filter(pk=deliveries[0].pk).update(
            last_error="edge_blocked: edge 403",
        )
        self.assertTrue(edge_breaker_open(campaign.pk))
        with override_settings(CAMPAIGN_EDGE_BLOCK_COOLDOWN_SECONDS=0):
            clear_config_cache()
            try:
                self.assertFalse(edge_breaker_open(campaign.pk))
            finally:
                clear_config_cache()
