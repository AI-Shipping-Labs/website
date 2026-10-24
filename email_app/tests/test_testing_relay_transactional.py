import requests
from django.conf import settings
from django.test import SimpleTestCase

from email_app import relay_transactional
from email_app import testing_relay_transactional as relay_testing
from integrations.config import reset_local_config_cache


def _payload(key, template_key="event_registration"):
    return {
        "email": "member@example.test",
        "template_key": template_key,
        "idempotency_key": key,
        "context": {"event_title": "Offline event"},
    }


class StubRelayTransactionalTransportTest(SimpleTestCase):
    def test_response_identity_and_replay_follow_idempotency_key(self):
        transport = relay_testing.StubRelayTransactionalTransport()

        first = transport.post(
            "https://relay.test/api/transactional/send",
            json=_payload("event-registration:1"),
            headers={"Authorization": "Bearer test-relay-key"},
            timeout=15,
        )
        replay = transport.post(
            "https://relay.test/api/transactional/send",
            json=_payload("event-registration:1"),
            headers={"Authorization": "Bearer test-relay-key"},
            timeout=15,
        )
        other = transport.post(
            "https://relay.test/api/transactional/send",
            json=_payload("event-registration:2", "event_rescheduled"),
            headers={"Authorization": "Bearer test-relay-key"},
            timeout=15,
        )

        first_document = first.json()
        replay_document = replay.json()
        other_document = other.json()
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first_document["message"]["status"], "queued")
        self.assertEqual(
            first_document["message"]["template_key"],
            "event_registration",
        )
        self.assertEqual(
            first_document["message"]["idempotency_key"],
            "event-registration:1",
        )
        self.assertRegex(first_document["message"]["id"], r"^relay-test-[0-9a-f]{16}$")
        self.assertFalse(first_document["idempotent_replay"])
        self.assertEqual(
            replay_document["message"]["id"],
            first_document["message"]["id"],
        )
        self.assertTrue(replay_document["idempotent_replay"])
        self.assertNotEqual(
            other_document["message"]["id"],
            first_document["message"]["id"],
        )

    def test_install_is_scoped_to_direct_adapter_requests_binding(self):
        original_binding = relay_transactional.requests
        original_transport = relay_testing._transport
        original_post = requests.post
        original_base_url = getattr(settings, "RELAY_BASE_URL", None)
        original_api_key = getattr(settings, "RELAY_API_KEY", None)
        try:
            relay_testing._transport = None
            transport = relay_testing.install_test_relay_transactional_transport()

            self.assertIs(relay_transactional.requests, transport)
            self.assertIs(requests.post, original_post)
            self.assertEqual(settings.RELAY_BASE_URL, "https://relay.test")
            self.assertEqual(settings.RELAY_API_KEY, "test-relay-key")
        finally:
            relay_transactional.requests = original_binding
            relay_testing._transport = original_transport
            settings.RELAY_BASE_URL = original_base_url
            settings.RELAY_API_KEY = original_api_key
            reset_local_config_cache()
