"""Comprehensive tests for AISHL Relay P1 (AC 1-6)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from community_base.mail.models import EmailDelivery
from community_base.mail.relay import RelayMailError, RelaySendResult
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.core.mail.message import EmailMessage
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.models import User
from email_app.backends.relay import (
    RAW_TRANSACTIONAL_TEMPLATE_KEY,
    RelayEmailBackend,
    RelayEmailBackendError,
)
from email_app.checks import (
    check_email_backend_selection,
    check_relay_configuration_in_production,
)
from email_app.health import get_relay_health_status, validate_relay_config
from email_app.package_mail import send_package_mail
from email_app.template_catalog import get_template_catalog


class TestRelayBackendSelection(SimpleTestCase):
    def test_community_base_mail_backend_defaults_to_relay(self):
        backend = settings.COMMUNITY_BASE.get("MAIL_BACKEND")
        self.assertEqual(backend, "relay")

    def test_relay_email_backend_instantiation(self):
        backend = RelayEmailBackend(
            base_url="https://relay.datatalks.club",
            api_key="test-api-key",
        )
        self.assertEqual(backend.base_url, "https://relay.datatalks.club")
        self.assertEqual(backend.api_key, "test-api-key")

    def test_check_email_backend_selection_rejects_smtp(self):
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend", TESTING=False):
            errors = check_email_backend_selection(None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "email_app.E004")

    def test_check_email_backend_selection_rejects_ses(self):
        with override_settings(EMAIL_BACKEND="django_ses.SESBackend", TESTING=False):
            errors = check_email_backend_selection(None)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].id, "email_app.E004")

    def test_check_email_backend_selection_accepts_relay(self):
        with override_settings(EMAIL_BACKEND="email_app.backends.relay.RelayEmailBackend", TESTING=False):
            errors = check_email_backend_selection(None)
            self.assertEqual(errors, [])


class TestRelayEmailBackendDirect(SimpleTestCase):
    def setUp(self):
        self.backend = RelayEmailBackend(
            base_url="https://relay.test.datatalks.club",
            api_key="test-api-key",
            fail_silently=False,
        )

    @patch("email_app.backends.relay.requests.post")
    def test_send_messages_successful_payload_shape(self, mock_post):
        mock_response = MagicMock(status_code=202)
        mock_post.return_value = mock_response

        message = EmailMessage(
            subject="Test Core Subject",
            body="Plain text body here.",
            from_email="AI Shipping Labs <noreply@aishippinglabs.com>",
            to=["recipient@example.com"],
            headers={"Idempotency-Key": "custom-core-key-123"},
        )

        sent = self.backend.send_messages([message])
        self.assertEqual(sent, 1)

        self.assertEqual(mock_post.call_count, 1)
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://relay.test.datatalks.club/api/transactional/send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-api-key")

        payload = kwargs["json"]
        self.assertEqual(payload["email"], "recipient@example.com")
        self.assertEqual(payload["template_key"], RAW_TRANSACTIONAL_TEMPLATE_KEY)
        self.assertEqual(payload["template_version"], 1)
        self.assertEqual(payload["idempotency_key"], "custom-core-key-123")
        self.assertEqual(payload["from_email"], "noreply")
        self.assertEqual(payload["context"]["subject"], "Test Core Subject")
        self.assertEqual(payload["context"]["text_body"], "Plain text body here.")

    @patch("email_app.backends.relay.requests.post")
    def test_send_messages_html_alternative(self, mock_post):
        mock_response = MagicMock(status_code=200)
        mock_post.return_value = mock_response

        msg = EmailMultiAlternatives(
            subject="Multi Subject",
            body="Plain body",
            from_email="welcome@aishippinglabs.com",
            to=["user@example.com"],
        )
        msg.attach_alternative("<h1>HTML Body</h1>", "text/html")

        sent = self.backend.send_messages([msg])
        self.assertEqual(sent, 1)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["from_email"], "welcome")
        self.assertEqual(payload["context"]["html_body"], "<h1>HTML Body</h1>")
        self.assertEqual(payload["context"]["text_body"], "Plain body")
        self.assertTrue(payload["idempotency_key"].startswith("core:"))

    def test_missing_config_raises_improperly_configured(self):
        backend = RelayEmailBackend(base_url="", api_key="")
        message = EmailMessage(subject="S", body="B", to=["u@test.com"])
        with self.assertRaises(ImproperlyConfigured):
            backend.send_messages([message])

    @patch("email_app.backends.relay.requests.post")
    def test_relay_error_fails_loudly_when_not_fail_silently(self, mock_post):
        mock_response = MagicMock(status_code=500, text="Internal Relay Error")
        mock_post.return_value = mock_response

        message = EmailMessage(subject="S", body="B", to=["u@test.com"])
        with self.assertRaises(RelayEmailBackendError):
            self.backend.send_messages([message])

    @patch("email_app.backends.relay.requests.post")
    def test_relay_error_swallowed_when_fail_silently(self, mock_post):
        mock_response = MagicMock(status_code=500, text="Internal Relay Error")
        mock_post.return_value = mock_response

        silent_backend = RelayEmailBackend(
            base_url="https://relay.test.datatalks.club",
            api_key="test-api-key",
            fail_silently=True,
        )
        message = EmailMessage(subject="S", body="B", to=["u@test.com"])
        sent = silent_backend.send_messages([message])
        self.assertEqual(sent, 0)


class TestRelayPackageMailDelivery(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com",
            password="testpassword",
        )

    @patch("community_base.mail.backends.relay.configured_client")
    def test_send_package_mail_success_updates_delivery(self, mock_configured_client):
        mock_client = MagicMock()
        mock_client.send.return_value = RelaySendResult(
            message_id="relay-msg-999",
            status="sent",
            template_version=1,
            idempotent_replay=False,
        )
        mock_configured_client.return_value = mock_client

        delivery = send_package_mail(
            user=self.user,
            template_name="free_welcome",
            context={"first_name": "Test"},
            idempotency_key="welcome:user:999",
        )

        from community_base.mail.backends.relay import deliver

        deliver(delivery, context={"first_name": "Test"})

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, EmailDelivery.State.PROVIDER_ACCEPTED)
        self.assertEqual(delivery.external_message_id, "relay-msg-999")
        self.assertEqual(delivery.template_version, 1)

    @patch("community_base.mail.backends.relay.configured_client")
    def test_send_package_mail_idempotency_preserved(self, mock_configured_client):
        mock_client = MagicMock()
        mock_client.send.return_value = RelaySendResult(
            message_id="relay-msg-idemp",
            status="sent",
            template_version=1,
            idempotent_replay=True,
        )
        mock_configured_client.return_value = mock_client

        delivery = send_package_mail(
            user=self.user,
            template_name="password_reset",
            context={"token": "test-reset-token-123"},
            idempotency_key="pass-reset:user:1",
        )
        self.assertEqual(delivery.idempotency_key, "pass-reset:user:1")

    @patch("community_base.mail.backends.relay.configured_client")
    def test_send_package_mail_permanent_error_marks_delivery_dead(self, mock_configured_client):
        mock_client = MagicMock()
        mock_client.send.side_effect = RelayMailError("template_not_found", retryable=False)
        mock_configured_client.return_value = mock_client

        delivery = send_package_mail(
            user=self.user,
            template_name="welcome",
            context={},
            idempotency_key="welcome:error:1",
        )

        from community_base.jobs.runner import PermanentJobError
        from community_base.mail.backends.relay import deliver

        with self.assertRaises(PermanentJobError):
            deliver(delivery, context={})

        delivery.refresh_from_db()
        self.assertEqual(delivery.state, EmailDelivery.State.DEAD)
        self.assertEqual(delivery.reason_code, "template_not_found")


class TestRelayHealthAndSystemChecks(SimpleTestCase):
    def test_validate_relay_config(self):
        with patch("email_app.health.get_config", side_effect=lambda k, d="": "https://relay.datatalks.club" if k == "RELAY_BASE_URL" else ("secret" if k == "RELAY_API_KEY" else d)):
            valid, error = validate_relay_config()
            self.assertTrue(valid)
            self.assertEqual(error, "")

    def test_validate_relay_config_missing_key(self):
        with patch("email_app.health.get_config", side_effect=lambda k, d="": "https://relay.datatalks.club" if k == "RELAY_BASE_URL" else d):
            valid, error = validate_relay_config()
            self.assertFalse(valid)
            self.assertEqual(error, "relay_config_missing")

    def test_dry_run_prod_fails_health(self):
        with patch("email_app.health.get_config", side_effect=lambda k, d="": "https://relay.datatalks.club" if k == "RELAY_BASE_URL" else ("secret" if k == "RELAY_API_KEY" else (True if k == "RELAY_TRANSACTIONAL_DRY_RUN" else d))), \
             patch("email_app.health.is_production", return_value=True):
            status = get_relay_health_status()
            self.assertEqual(status["status"], "FAIL")
            self.assertTrue(status["degraded"])
            self.assertEqual(status["error"], "dry_run_on_prod")

    def test_check_relay_configuration_in_production(self):
        with override_settings(
            COMMUNITY_BASE={"MAIL_BACKEND": "relay"},
            DEBUG=False,
            TESTING=False,
        ), patch("email_app.checks.get_config", return_value=""):
            errors = check_relay_configuration_in_production(None)
            self.assertTrue(any(e.id == "email_app.E002" for e in errors))


class TestTemplateCatalogIntegrity(SimpleTestCase):
    def test_template_catalog_has_all_local_templates(self):
        catalog = get_template_catalog()
        self.assertGreaterEqual(len(catalog), 61)
        self.assertIn("welcome", catalog)
        self.assertIn("maven_welcome", catalog)
        self.assertIn("password_reset", catalog)
        self.assertIn("raw_transactional", catalog)

        for key, meta in catalog.items():
            self.assertTrue(meta.key)
            self.assertIn(meta.default_sender_id, {"noreply", "welcome", "content"})
            self.assertGreaterEqual(meta.version, 1)
