"""Rendered sends fail visibly when Relay is unavailable or unconfigured."""

from unittest.mock import MagicMock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from email_app.services.email_errors import EmailTransportOutcomeUnknown, RelayCampaignError
from email_app.services.relay_campaigns import (
    RelayCampaignDeliveryAmbiguous,
    RelayCampaignDeliveryRejected,
)
from email_app.services.relay_transport import send_rendered_relay

TRANSPORT = "email_app.services.relay_transport"


class RelayTransportTest(SimpleTestCase):
    def _send(self, **kwargs):
        return send_rendered_relay(
            "recipient@example.com", "Subject", "<p>Body</p>", text_body="Body",
            idempotency_key="stable-delivery-key", **kwargs,
        )

    @override_settings(TESTING=False)
    def test_missing_configuration_raises_outside_offline_tests(self):
        for base_url, api_key in (("", "key"), ("https://relay.example", "")):
            with (
                self.subTest(base_url=base_url, api_key=bool(api_key)),
                patch(f"{TRANSPORT}.get_relay_base_url", return_value=base_url),
                patch(f"{TRANSPORT}.get_relay_api_key", return_value=api_key),
                patch(f"{TRANSPORT}.send_campaign_delivery_to_relay") as send,
            ):
                with self.assertRaises(ImproperlyConfigured):
                    self._send()
                send.assert_not_called()

    @override_settings(TESTING=True)
    def test_offline_test_configuration_never_contacts_relay(self):
        with (
            patch(f"{TRANSPORT}.get_relay_base_url", return_value=""),
            patch(f"{TRANSPORT}.send_campaign_delivery_to_relay") as send,
        ):
            self.assertEqual(self._send(), "relay-not-configured-noop")
            send.assert_not_called()

    def test_relay_message_id_and_stable_key_are_preserved(self):
        with (
            patch(f"{TRANSPORT}.get_relay_base_url", return_value="https://relay.example"),
            patch(f"{TRANSPORT}.get_relay_api_key", return_value="test-key"),
            patch(f"{TRANSPORT}.send_campaign_delivery_to_relay", return_value="relay-id") as send,
        ):
            self.assertEqual(self._send(), "relay-id")
            self.assertEqual(send.call_args.kwargs["idempotency_key"], "stable-delivery-key")
            self.assertEqual(send.call_count, 1)

    def test_cc_and_bcc_reach_relay_payload(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {"message": {"id": "relay-id"}}
        with (
            patch(f"{TRANSPORT}.get_relay_base_url", return_value="https://relay.example"),
            patch(f"{TRANSPORT}.get_relay_api_key", return_value="test-key"),
            patch("email_app.services.relay_campaigns.get_relay_base_url", return_value="https://relay.example"),
            patch("email_app.services.relay_campaigns.get_relay_api_key", return_value="test-key"),
            patch("email_app.services.relay_campaigns.get_relay_from_email", return_value="content"),
            patch("email_app.services.relay_campaigns.requests.Session.request", return_value=response) as request,
        ):
            self._send(cc=" copy@example.com ", bcc=("audit@example.com", ""))
        payload = request.call_args.kwargs["json"]
        self.assertEqual(payload["cc"], ["copy@example.com"])
        self.assertEqual(payload["bcc"], ["audit@example.com"])

    def test_definitive_and_unknown_failures_remain_distinguishable(self):
        failures = (
            (RelayCampaignDeliveryRejected("rejected"), RelayCampaignError),
            (RelayCampaignDeliveryAmbiguous("timeout"), EmailTransportOutcomeUnknown),
        )
        for failure, expected in failures:
            with (
                self.subTest(expected=expected),
                patch(f"{TRANSPORT}.get_relay_base_url", return_value="https://relay.example"),
                patch(f"{TRANSPORT}.get_relay_api_key", return_value="test-key"),
                patch(f"{TRANSPORT}.send_campaign_delivery_to_relay", side_effect=failure),
            ):
                with self.assertRaises(expected):
                    self._send()
