"""Tests for outbound delivery signing + the deliver_webhook task (#1070)."""

import hashlib
import hmac
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from triggers.models import EventEmission, TriggerSubscription, WebhookDelivery
from triggers.signing import compute_signature
from triggers.tasks import deliver_webhook

User = get_user_model()


@tag("core")
class SigningTest(TestCase):
    def test_signature_matches_hmac_of_timestamp_dot_body(self):
        secret = "shhh"
        timestamp = 1700000000
        body = '{"event":"v0_workshop"}'
        expected = "sha256=" + hmac.new(
            secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256,
        ).hexdigest()
        self.assertEqual(compute_signature(secret, timestamp, body), expected)

    def test_signature_changes_with_timestamp(self):
        a = compute_signature("s", 1, "body")
        b = compute_signature("s", 2, "body")
        self.assertNotEqual(a, b)


class _FakeResponse:
    def __init__(self, status_code, text="ok"):
        self.status_code = status_code
        self.text = text


@tag("core")
class DeliverWebhookTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="m@example.com", password="x", email_verified=True,
        )
        cls.emission = EventEmission.objects.create(
            user=cls.user,
            event_name="v0_workshop",
            properties={"name": "v0_workshop", "min_level": 5},
            envelope_id="evt_abc123",
        )
        cls.subscription = TriggerSubscription.objects.create(
            event_type="custom",
            property_filter={"name": "v0_workshop"},
            target_url="https://handler.example.com/hook",
            secret="topsecret",
        )

    def test_successful_delivery_records_row_and_signed_headers(self):
        with patch("triggers.tasks.post_pinned_https") as mock_post:
            mock_post.return_value = _FakeResponse(200)
            deliver_webhook(self.emission.id, self.subscription.id)

        self.assertEqual(mock_post.call_count, 1)
        _args, kwargs = mock_post.call_args
        headers = kwargs["headers"]
        sent_body = kwargs["body"].decode("utf-8")

        # Headers present and signature verifies over "<timestamp>.<body>".
        self.assertIn("X-AISL-Signature", headers)
        self.assertEqual(headers["X-AISL-Event-Id"], "evt_abc123")
        timestamp = headers["X-AISL-Timestamp"]
        expected_sig = compute_signature("topsecret", timestamp, sent_body)
        self.assertEqual(headers["X-AISL-Signature"], expected_sig)
        self.assertTrue(headers["X-AISL-Signature"].startswith("sha256="))

        # Envelope body shape.
        envelope = json.loads(sent_body)
        self.assertEqual(envelope["event"], "v0_workshop")
        self.assertEqual(envelope["id"], "evt_abc123")
        self.assertEqual(envelope["data"]["email"], "m@example.com")
        self.assertEqual(envelope["data"]["properties"]["name"], "v0_workshop")
        # data.min_level carries the real value from the emission properties
        # (regression: it was serialised as null before #1070 review fix).
        self.assertEqual(envelope["data"]["min_level"], 5)

        # WebhookDelivery row recorded as succeeded.
        delivery = WebhookDelivery.objects.get()
        self.assertTrue(delivery.succeeded)
        self.assertEqual(delivery.response_status, 200)
        self.assertEqual(delivery.attempt, 1)

    def test_non_2xx_records_failed_row_and_raises(self):
        with patch("triggers.tasks.post_pinned_https") as mock_post:
            mock_post.return_value = _FakeResponse(500, text="boom")
            deliver_webhook(self.emission.id, self.subscription.id)

        delivery = WebhookDelivery.objects.get()
        self.assertFalse(delivery.succeeded)
        self.assertEqual(delivery.response_status, 500)

    def test_success_guard_makes_duplicate_job_call_a_noop(self):
        with patch("triggers.tasks.post_pinned_https") as mock_post:
            mock_post.return_value = _FakeResponse(200)
            deliver_webhook(self.emission.id, self.subscription.id)
            deliver_webhook(self.emission.id, self.subscription.id)

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(
            list(WebhookDelivery.objects.values_list("attempt", flat=True)),
            [1],
        )
