from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import TestCase

from email_app.health import get_relay_health_status
from email_app.models import RelayTransactionalDelivery
from email_app.relay_transactional import RelayTransactionalError, send_relay_transactional
from email_app.tests.test_relay_transactional import relay_config, relay_response


def sender_not_configured():
    response = Mock(status_code=400)
    response.json.return_value = {
        "error": {"code": "validation_error", "fields": {"from_email": "not_configured"}},
    }
    return response


@patch("email_app.relay_transactional.is_enabled", return_value=False)
@patch("email_app.relay_transactional.get_config", side_effect=relay_config)
class RelaySenderFallbackTest(TestCase):
    def setUp(self):
        cache.clear()

    def _send(self, **kwargs):
        return send_relay_transactional(
            "member@example.com", "event_registration", {"user_name": "Ada"},
            idempotency_key="registration:1", safe_correlation="event=1 user=2",
            sender_id="alexey", **kwargs,
        )

    def test_unconfigured_sender_falls_back_once_on_same_key(self, _c, _e):
        with patch("email_app.relay_transactional.requests.post",
                   side_effect=[sender_not_configured(), relay_response()]) as post:
            with self.assertLogs("email_app.relay_transactional", "WARNING"):
                result = self._send(fallback_sender_id="content")
        self.assertEqual(result.message_id, "relay-message-1")
        self.assertEqual(post.call_count, 2)
        first, second = (c.kwargs["json"] for c in post.call_args_list)
        self.assertEqual(first["from_email"], "alexey")
        self.assertEqual(second["from_email"], "content")
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        delivery = RelayTransactionalDelivery.objects.get(idempotency_key="registration:1")
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_ACCEPTED)
        health = get_relay_health_status()
        self.assertIn("relay_sender_not_configured: alexey", health["warning"])

    def test_configured_sender_is_used_without_fallback(self, _c, _e):
        with patch("email_app.relay_transactional.requests.post",
                   return_value=relay_response()) as post:
            self._send(fallback_sender_id="content")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["from_email"], "alexey")
        self.assertEqual(get_relay_health_status()["warning"], "")

    def test_no_fallback_without_fallback_sender(self, _c, _e):
        with patch("email_app.relay_transactional.requests.post",
                   return_value=sender_not_configured()) as post:
            with self.assertRaises(RelayTransactionalError):
                self._send()
        self.assertEqual(post.call_count, 1)

    def test_other_400_does_not_fall_back(self, _c, _e):
        other = Mock(status_code=400)
        other.json.return_value = {"error": {"code": "validation_error", "fields": {"context": "must_be_object"}}}
        with patch("email_app.relay_transactional.requests.post", return_value=other) as post:
            with self.assertRaises(RelayTransactionalError):
                self._send(fallback_sender_id="content")
        self.assertEqual(post.call_count, 1)
