from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from icalendar import Calendar

from email_app.management.commands.prove_relay_calendar import (
    DEFAULT_IDEMPOTENCY_KEY,
    PROOF_EVENT_UID,
    RECIPIENT_EMAIL,
)
from email_app.models import RelayTransactionalDelivery


def _relay_config(key, default=""):
    values = {
        "RELAY_BASE_URL": "https://relay.example.test",
        "RELAY_API_KEY": "relay-proof-secret",
    }
    return values.get(key, default)


def _relay_response():
    response = Mock(status_code=202)
    response.json.return_value = {
        "message": {
            "id": "relay-proof-message-2006",
            "status": "queued",
            "template_key": "event_registration",
            "idempotency_key": DEFAULT_IDEMPOTENCY_KEY,
        },
        "idempotent_replay": False,
    }
    return response


@override_settings(SITE_BASE_URL="https://proof.aishippinglabs.test")
@patch("email_app.relay_transactional.is_enabled", return_value=False)
@patch("email_app.relay_transactional.get_config", side_effect=_relay_config)
class RelayCalendarProofCommandTest(TestCase):
    def assert_proof_payload(self, payload):
        self.assertEqual(payload["email"], RECIPIENT_EMAIL)
        self.assertEqual(payload["template_key"], "event_registration")
        self.assertEqual(payload["idempotency_key"], DEFAULT_IDEMPOTENCY_KEY)
        alternative = payload["calendar_alternative"]
        self.assertEqual(alternative["method"], "REQUEST")
        self.assertEqual(alternative["name"], "event.ics")
        calendar = Calendar.from_ical(alternative["content"])
        event = calendar.walk("VEVENT")[0]
        self.assertEqual(calendar["METHOD"].to_ical(), b"REQUEST")
        self.assertEqual(event["UID"].to_ical(), PROOF_EVENT_UID.encode())
        self.assertEqual(event["SEQUENCE"], 1)
        self.assertEqual(
            event["ATTENDEE"].to_ical(),
            f"mailto:{RECIPIENT_EMAIL}".encode(),
        )
        self.assertEqual(payload["context"]["user_name"], "Alexey")
        self.assertTrue(payload["context"]["is_host_registration"])

    def test_default_is_network_free_and_redacted(self, _config, _enabled):
        output = StringIO()
        with patch("email_app.relay_transactional.requests.post") as post:
            call_command("prove_relay_calendar", stdout=output)

        post.assert_not_called()
        self.assertEqual(RelayTransactionalDelivery.objects.count(), 0)
        rendered = output.getvalue()
        self.assertIn("status=dry_run", rendered)
        self.assertIn("template=event_registration", rendered)
        self.assertRegex(rendered, r"calendar_sha256=[0-9a-f]{64}")
        self.assertNotIn(RECIPIENT_EMAIL, rendered)
        self.assertNotIn("relay-proof-secret", rendered)
        self.assertNotIn("https://", rendered)

    def test_send_posts_fixed_registration_invite_once(self, _config, _enabled):
        first_output = StringIO()
        second_output = StringIO()
        with patch(
            "email_app.relay_transactional.requests.post",
            return_value=_relay_response(),
        ) as post:
            call_command("prove_relay_calendar", "--send", stdout=first_output)
            call_command("prove_relay_calendar", "--send", stdout=second_output)

        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], "https://relay.example.test/api/transactional/send")
        self.assert_proof_payload(post.call_args.kwargs["json"])
        rendered = first_output.getvalue() + second_output.getvalue()
        self.assertIn("status=queued", rendered)
        self.assertIn("status=accepted", rendered)
        self.assertIn("message_id=relay-proof-message-2006", rendered)
        self.assertNotIn(RECIPIENT_EMAIL, rendered)
        self.assertNotIn("relay-proof-secret", rendered)
        self.assertNotIn("https://", rendered)
