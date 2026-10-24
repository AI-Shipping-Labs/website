from unittest.mock import call, patch

import requests
from django.conf import settings
from django.test import SimpleTestCase

from email_app import relay_transactional
from email_app import testing_relay_transactional as relay_testing
from integrations import config as integration_config


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
        original_adapter_get_config = relay_transactional.get_config
        original_transport = relay_testing._transport
        original_post = requests.post
        original_global_get_config = integration_config.get_config
        original_base_url = getattr(settings, "RELAY_BASE_URL", None)
        original_api_key = getattr(settings, "RELAY_API_KEY", None)
        try:
            relay_testing._transport = None
            transport = relay_testing.install_test_relay_transactional_transport()

            self.assertIs(relay_transactional.requests, transport)
            self.assertIs(requests.post, original_post)
            self.assertIs(integration_config.get_config, original_global_get_config)
            self.assertEqual(
                relay_transactional.get_config("RELAY_BASE_URL"),
                "https://relay.test",
            )
            self.assertEqual(
                relay_transactional.get_config("RELAY_API_KEY"),
                "test-relay-key",
            )
            self.assertEqual(
                getattr(settings, "RELAY_BASE_URL", None),
                original_base_url,
            )
            self.assertEqual(
                getattr(settings, "RELAY_API_KEY", None),
                original_api_key,
            )
        finally:
            relay_transactional.requests = original_binding
            relay_transactional.get_config = original_adapter_get_config
            relay_testing._transport = original_transport

    def test_adapter_config_prefers_explicit_values_and_defaults_offline(self):
        configured = {
            "RELAY_BASE_URL": "https://configured-relay.example",
            "RELAY_API_KEY": "configured-relay-key",
        }

        with patch.object(
            integration_config,
            "get_config",
            side_effect=lambda key, default="", **kwargs: configured.get(key, default),
        ) as get_config:
            self.assertEqual(
                relay_testing._test_relay_config("RELAY_BASE_URL"),
                "https://configured-relay.example",
            )
            self.assertEqual(
                relay_testing._test_relay_config("RELAY_API_KEY"),
                "configured-relay-key",
            )

        self.assertEqual(
            get_config.call_args_list,
            [call("RELAY_BASE_URL", ""), call("RELAY_API_KEY", "")],
        )

        with patch.object(integration_config, "get_config", return_value=""):
            self.assertEqual(
                relay_testing._test_relay_config("RELAY_BASE_URL"),
                "https://relay.test",
            )
            self.assertEqual(
                relay_testing._test_relay_config("RELAY_API_KEY"),
                "test-relay-key",
            )
