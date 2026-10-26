"""Capture the real site boundary's welcome envelope at the Relay transport."""

from unittest.mock import patch

from community_base.mail.jobs import deliver
from community_base.mail.models import EmailDelivery
from community_base.mail.relay import RelayMailClient
from community_base.testing.relay import FakeRelay
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from email_app.models import EmailLog
from email_app.package_mail import send_package_mail
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


class WelcomeRelayWireTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = get_user_model().objects.create_user(email="member@example.com")

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        configured = {**settings.COMMUNITY_BASE, "MAIL_BACKEND": "relay"}
        self.settings_override = override_settings(
            COMMUNITY_BASE=configured,
            RELAY_WELCOME_FROM_EMAIL="welcome@aishippinglabs.com",
            RELAY_WELCOME_REPLY_TO_EMAIL="welcome@aishippinglabs.com",
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def test_default_welcome_reply_to_and_hidden_staff_bcc_reach_relay(self):
        delivery = self._assert_welcome_wire("welcome@aishippinglabs.com")
        self._assert_email_log(delivery)

    def test_database_alternate_reply_to_and_hidden_staff_bcc_reach_relay(self):
        IntegrationSetting.objects.create(
            key="RELAY_WELCOME_REPLY_TO_EMAIL",
            value="member-support@example.com",
            group="ses",
        )
        clear_config_cache()
        self._assert_welcome_wire("member-support@example.com")

    def _send_through_worker(self, transport):
        client = RelayMailClient(
            "https://relay.example", "relay-test-key", transport=transport,
        )
        delivery = send_package_mail(
            self.member, "basic_welcome", {"first_name": "Member"},
            bcc=["staff-audit@example.com"], idempotency_key="welcome:wire:member",
        )
        with patch("community_base.mail.backends.relay.configured_client", return_value=client):
            with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
                deliver(None, {"delivery_id": str(delivery.id)})
        delivery.refresh_from_db()
        return delivery

    def _assert_welcome_wire(self, reply_to):
        transport = FakeRelay()
        self._seed_template(transport)
        delivery = self._send_through_worker(transport)
        send_requests = []
        for request in transport.calls:
            if request.url.endswith("/api/transactional/send"):
                send_requests.append(request)
        self.assertEqual(len(send_requests), 1)
        self._assert_send_payload(send_requests[0], reply_to)
        self.assertEqual(delivery.state, EmailDelivery.State.PROVIDER_ACCEPTED)
        self.assertEqual(
            delivery.external_message_id,
            transport.idempotency_keys[delivery.idempotency_key],
        )
        return delivery

    @staticmethod
    def _seed_template(transport):
        transport.templates["basic_welcome"] = {
            "key": "basic_welcome",
            "latest_version": 2,
            "versions": [
                {
                    "template_key": "basic_welcome",
                    "version": 1,
                    "status": "published",
                },
                {
                    "template_key": "basic_welcome",
                    "version": 2,
                    "status": "published",
                },
            ],
        }

    def _assert_send_payload(self, request, reply_to):
        self.assertEqual((request.method, request.url), (
            "POST", "https://relay.example/api/transactional/send",
        ))
        payload = request.kwargs["json"]
        self.assertEqual(payload["email"], self.member.email)
        self.assertEqual(payload["template_key"], "basic_welcome")
        self.assertEqual(payload["template_version"], 2)
        self.assertEqual(payload["from_email"], "welcome")
        self.assertEqual(payload.get("reply_to"), reply_to)
        self.assertEqual(payload.get("bcc"), ["staff-audit@example.com"])
        self.assertNotIn("cc", payload)
        self.assertNotIn("configuration_set", payload)

    def _assert_email_log(self, delivery):
        audit = EmailLog.objects.get(dedupe_key=delivery.idempotency_key)
        self.assertEqual(audit.user, self.member)
        self.assertEqual(audit.email_type, "basic_welcome")
        self.assertEqual(audit.subject, "Hello reader")
