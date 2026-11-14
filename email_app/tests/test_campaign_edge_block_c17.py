"""Edge 403 circuit breaker: HTML 403 pauses the campaign, rows stay retryable."""


import requests
from django.test import TestCase, override_settings

from email_app.services.relay_campaigns import (
    RelayCampaignDeliveryAmbiguous,
    RelayCampaignEdgeBlocked,
    send_campaign_delivery_to_relay,
)
from integrations.config import clear_config_cache


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


