"""Capture campaign sender IDs against Relay's slug-only wire contract."""

import json
from unittest.mock import patch

import requests
from django.core.exceptions import ValidationError
from django.core.validators import validate_slug
from django.test import TestCase, override_settings

from email_app.services.relay_campaigns import send_campaign_delivery_to_relay
from integrations.config import clear_config_cache


class SenderContractAdapter(requests.adapters.BaseAdapter):
    def __init__(self):
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        payload = json.loads(request.body)
        response = requests.Response()
        response.request = request
        response.url = request.url
        try:
            validate_slug(payload.get("from_email", ""))
        except ValidationError:
            response.status_code = 400
            response._content = b'{"from_email":"invalid"}'
            return response
        response.status_code = 202
        response._content = b'{"message":{"id":177,"status":"queued"}}'
        return response

    def close(self):
        pass


@override_settings(
    RELAY_BASE_URL="https://relay.example",
    RELAY_API_KEY="fixture-relay-key",
    RELAY_PROMOTIONAL_FROM_EMAIL="",
)
class CampaignSenderWireTest(TestCase):
    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_default_campaign_uses_content_sender(self):
        self._assert_sender("content")

    @override_settings(RELAY_PROMOTIONAL_FROM_EMAIL="AI Shipping Labs <content@aishippinglabs.com>")
    def test_promotional_display_address_uses_registered_content_slug(self):
        self._assert_sender("content")

    @override_settings(RELAY_PROMOTIONAL_FROM_EMAIL="content")
    def test_already_resolved_sender_slug_is_preserved(self):
        self._assert_sender("content")

    def _assert_sender(self, expected):
        adapter = SenderContractAdapter()
        with requests.Session() as session:
            session.mount("https://", adapter)
            with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
                message_id = send_campaign_delivery_to_relay(
                    "member@example.com", "Subject", "<p>Body</p>", "Body",
                    idempotency_key="campaign:sender:fixture", dry_run=False, session=session,
                )
        self.assertEqual(message_id, "177")
        self.assertEqual(len(adapter.requests), 1)
        request = adapter.requests[0]
        self.assertEqual((request.method, request.url), (
            "POST", "https://relay.example/api/transactional/send",
        ))
        payload = json.loads(request.body)
        self.assertEqual(payload["from_email"], expected)
        self.assertEqual(payload["category"], "newsletter")
        self.assertEqual(payload["email"], "member@example.com")
