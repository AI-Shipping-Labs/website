from io import StringIO
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from email_app.health import check_relay_sender_configuration, get_relay_health_status
from email_app.models import RelayTransactionalDelivery
from email_app.relay_transactional import RelayTransactionalError, send_relay_transactional
from email_app.services.email_classification import get_calendar_fallback_sender
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
        with patch(
            "email_app.relay_transactional.requests.post",
            side_effect=[sender_not_configured(), relay_response()],
        ) as post:
            with self.assertLogs("email_app.relay_transactional", "WARNING"):
                result = self._send(fallback_sender_id="content")
        self.assertEqual(result.message_id, "relay-message-1")
        self.assertEqual(post.call_count, 2)
        first, second = (call.kwargs["json"] for call in post.call_args_list)
        self.assertEqual(first["from_email"], "alexey")
        self.assertEqual(second["from_email"], "content")
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        delivery = RelayTransactionalDelivery.objects.get(idempotency_key="registration:1")
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_ACCEPTED)
        health = get_relay_health_status()
        self.assertIn("relay_sender_not_configured: alexey", health["warning"])

    def test_configured_sender_is_used_without_fallback(self, _c, _e):
        with patch(
            "email_app.relay_transactional.requests.post",
            return_value=relay_response(),
        ) as post:
            self._send(fallback_sender_id="content")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["from_email"], "alexey")
        self.assertEqual(get_relay_health_status()["warning"], "")

    def test_no_fallback_without_fallback_sender(self, _c, _e):
        with patch(
            "email_app.relay_transactional.requests.post",
            return_value=sender_not_configured(),
        ) as post:
            with self.assertRaises(RelayTransactionalError):
                self._send()
        self.assertEqual(post.call_count, 1)

    def test_other_400_does_not_fall_back(self, _c, _e):
        other = Mock(status_code=400)
        other.json.return_value = {
            "error": {"code": "validation_error", "fields": {"context": "must_be_object"}},
        }
        with patch("email_app.relay_transactional.requests.post", return_value=other) as post:
            with self.assertRaises(RelayTransactionalError):
                self._send(fallback_sender_id="content")
        self.assertEqual(post.call_count, 1)


class RelaySenderPreflightTest(TestCase):
    def test_calendar_fallback_uses_the_promotional_integration_setting(self):
        with patch(
            "email_app.services.email_classification.get_config",
            return_value="promotions@aishippinglabs.com",
        ):
            sender = get_calendar_fallback_sender()

        self.assertEqual(sender, "promotions@aishippinglabs.com")

    def test_preflight_fails_when_a_sender_id_is_missing_from_relay(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "default_sender_id": "noreply",
            "sender_emails": [
                {"id": "noreply", "email": "noreply@aishippinglabs.com"},
                {"id": "content", "email": "content@aishippinglabs.com"},
            ],
        }
        config = {"RELAY_BASE_URL": "https://relay.test", "RELAY_API_KEY": "test-api-key"}
        with (
            patch("email_app.health.validate_relay_config", return_value=(True, "")),
            patch("email_app.health.required_relay_sender_ids", return_value=["alexey", "content"]),
            patch("email_app.health.get_config", side_effect=lambda key, *_args: config[key]),
            patch("email_app.health.requests.get", return_value=response) as get,
        ):
            is_configured, error = check_relay_sender_configuration()

        self.assertFalse(is_configured)
        self.assertEqual(error, "relay_senders_missing:alexey")
        self.assertEqual(get.call_args.args[0], "https://relay.test/api/client/senders")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer test-api-key")

    def test_sender_health_option_fails_the_command_before_promotion(self):
        output = StringIO()
        with (
            patch(
                "email_app.management.commands.check_relay_health.get_relay_health_status",
                return_value={"status": "ok", "relay_status": "ok", "degraded": False, "error": ""},
            ),
            patch(
                "email_app.management.commands.check_relay_health.check_relay_sender_configuration",
                return_value=(False, "relay_senders_missing:alexey"),
            ),
        ):
            with self.assertRaises(CommandError):
                call_command("check_relay_health", check_senders=True, json=True, stdout=output)

        self.assertIn('"sender_check": "FAIL"', output.getvalue())
        self.assertIn('"error": "relay_senders_missing:alexey"', output.getvalue())
