"""Core Django mail preserves explicit Reply-To on the actual Relay wire."""

from unittest.mock import Mock, patch

from django.core.mail import EmailMessage
from django.test import SimpleTestCase

from email_app.backends.relay import RelayEmailBackend, RelayEmailBackendError


class RelayBackendReplyToTest(SimpleTestCase):
    def setUp(self):
        self.backend = RelayEmailBackend(base_url="https://relay.example.invalid", api_key="synthetic-key")

    def message(self, reply_to):
        return EmailMessage(subject="Synthetic", body="Synthetic", from_email="welcome@example.invalid",
            to=["member@example.invalid"], reply_to=reply_to)

    @patch("email_app.backends.relay.requests.post", return_value=Mock(status_code=202))
    def test_singleton_reply_to_reaches_native_payload(self, request):
        self.assertEqual(self.backend.send_messages([self.message(["reply@example.invalid"])]), 1)
        self.assertEqual(request.call_count, 1)
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["reply_to"], "reply@example.invalid")
        self.assertEqual(payload["email"], "member@example.invalid")
        self.assertEqual(payload["from_email"], "welcome")

    @patch("email_app.backends.relay.requests.post", return_value=Mock(status_code=202))
    def test_empty_reply_to_does_not_add_field(self, request):
        self.backend.send_messages([self.message([])])
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("reply_to", request.call_args.kwargs["json"])

    @patch("email_app.backends.relay.requests.post", return_value=Mock(status_code=202))
    def test_multiple_reply_to_values_fail_safely_without_http(self, request):
        with self.assertRaisesRegex(RelayEmailBackendError, "^unsupported_relay_reply_to_count$"):
            self.backend.send_messages([self.message(["first@example.invalid", "second@example.invalid"])])
        request.assert_not_called()
