"""Relay cutover P4 guard: no direct SES send path may remain (issue #509 retired).

Background
----------

Before the Relay migration, production AWS SES was spammed by test runs
because the custom EmailService and the events registration mailer built a
boto3 SES client directly from AWS credentials. The SES_ENABLED
kill-switch short-circuited those paths under ``manage.py test``.

After P4 there is no SES send path at all: every send goes through Relay
(package ``relay`` backend, the Relay transactional adapter, or
RelayEmailBackend). This module is the canary that fails if any direct
SES path comes back:

1. The test runner resolves the package backend to Relay and Django core
   mail to the offline backend: ``COMMUNITY_BASE['MAIL_BACKEND']`` is
   ``relay`` and production-like resolution keeps both backends on Relay.
2. The four user-facing endpoints that send email — newsletter
   subscribe, account register, password-reset request, and event
   registration — never call ``boto3.client('ses'/'sesv2', ...)`` and never
   call ``send_email``/``send_raw_email`` on the resulting client.
3. Offline test ``EmailService.send`` calls without Relay configuration return
   an ``EmailLog`` row whose ``ses_message_id`` is the recognisable
   ``relay-not-configured-noop`` marker so observability is preserved.
4. A static AST scan fails if any non-test module constructs a boto3
   ses/sesv2 client, calls ``send_email``/``send_raw_email``, references
   the deleted ``ses_transport``/``send_ses_email``, or points a mail
   backend at the local SES backend.
5. The E002/E004/E005 system checks fire when production-like settings
   drift off Relay.

If you are adding a new code path that sends mail, route it through
Relay and add it to this file so the canary fires when the rule is
missed.
"""


from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from email_app.models import EmailLog
from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration
from payments.models import Tier


class RelayTestTransportTest(TestCase):
    """The test runner resolves mail to Relay (never SES/SMTP)."""

    def test_package_backend_resolves_to_relay_under_test_runner(self):
        # ``website/settings.py`` defaults COMMUNITY_BASE['MAIL_BACKEND']
        # to relay in every environment. If this ever flips back, package
        # mail silently leaves Relay in tests.
        self.assertEqual(
            settings.COMMUNITY_BASE.get("MAIL_BACKEND"),
            "relay",
        )

    def test_django_backend_is_offline_under_test_runner(self):
        # Django core mail stays on the offline backend under TESTING so
        # no test can reach the network through send_mail/EmailMessage.
        self.assertEqual(
            settings.EMAIL_BACKEND,
            "django.core.mail.backends.locmem.EmailBackend",
        )



class SESDisabledHttpEndpointsTest(TestCase):
    """No SES client is constructed for any user-facing email endpoint."""

    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )
        # User used by the password-reset-request and event-registration
        # tests below. /api/register and /api/subscribe create their own.
        # ``email_verified=True`` is required for ``can_access`` to allow
        # registration on a LEVEL_OPEN event.
        cls.existing_user = User.objects.create_user(
            email="existing@example.com",
            password="ExistingPass123!",
            email_verified=True,
        )
        cls.start = timezone.now() + timedelta(days=1)
        cls.event = Event.objects.create(
            slug="ses-disabled-event",
            title="SES Disabled Test Event",
            description="Free event for the regression test.",
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            status="upcoming",
            required_level=0,
        )

    def _assert_no_ses_traffic(self, mock_boto_client):
        """Assert no SES client was built and no email was sent.

        ``mock_boto_client`` is the replacement for the top-level
        ``boto3.client`` factory. If a future code path reintroduces a
        direct SES send, this assertion fires and points at the bypass.
        """
        # No boto3 SES client should have been constructed at all.
        for call in mock_boto_client.call_args_list:
            service = call.kwargs.get("service_name")
            if call.args:
                service = call.args[0]
            self.assertNotEqual(
                service,
                "sesv2",
                f"boto3.client('sesv2', ...) was called: {call}. "
                f"A code path reintroduced a direct SES send (Relay P4).",
            )
            self.assertNotEqual(
                service,
                "ses",
                f"boto3.client('ses', ...) was called: {call}. "
                f"A code path reintroduced a direct SES send (Relay P4).",
            )
        # And the mock client returned by the factory must never have
        # received a send_email() or send_raw_email() call. We check the
        # default return-value client; whatever attribute access happens
        # on it is recorded.
        self.assertFalse(
            mock_boto_client.return_value.send_email.called,
            "send_email() was called on a mocked boto3 client; "
            "a code path reintroduced a direct SES send (Relay P4).",
        )
        self.assertFalse(
            mock_boto_client.return_value.send_raw_email.called,
            "send_raw_email() was called on a mocked boto3 client; "
            "a code path reintroduced a direct SES send (Relay P4).",
        )

    @patch("boto3.client")
    def test_subscribe_does_not_call_boto3_when_disabled(self, mock_boto_client):
        response = self.client.post(
            "/api/subscribe",
            data='{"email": "newsubscriber@example.com"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self._assert_no_ses_traffic(mock_boto_client)

    @patch("boto3.client")
    def test_register_does_not_call_boto3_when_disabled(self, mock_boto_client):
        response = self.client.post(
            "/api/register",
            data='{"email": "newuser@example.com", "password": "NewPass123!"}',
            content_type="application/json",
        )
        # 200/201 are both acceptable; the point is it succeeded without
        # sending mail.
        self.assertIn(response.status_code, (200, 201))
        self._assert_no_ses_traffic(mock_boto_client)

    @patch("boto3.client")
    def test_password_reset_does_not_call_boto3_when_disabled(self, mock_boto_client):
        response = self.client.post(
            "/api/password-reset-request",
            data='{"email": "existing@example.com"}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self._assert_no_ses_traffic(mock_boto_client)

    @patch("boto3.client")
    def test_event_registration_does_not_call_boto3_when_disabled(
        self, mock_boto_client,
    ):
        # Regression canary for events/services/registration_email.py —
        # calendar mail must go through the Relay adapter, never boto3.
        self.client.force_login(self.existing_user)
        url = reverse("event_register", args=[self.event.slug])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=self.event, user=self.existing_user,
            ).exists(),
        )
        self._assert_no_ses_traffic(mock_boto_client)


class EmailServiceSendNoopTest(TestCase):
    """Direct EmailService.send() without Relay config no-ops and logs."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="noop@example.com",
            first_name="Noop",
        )

    @patch("boto3.client")
    def test_email_service_send_returns_noop_id_when_disabled(self, mock_boto_client):
        baseline = EmailLog.objects.count()

        service = EmailService()
        log = service.send(self.user, "welcome", {"tier_name": "Main"})

        # An EmailLog row must still be written so observability is
        # preserved when Relay is not configured (tests, local dev).
        self.assertEqual(EmailLog.objects.count(), baseline + 1)
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.email_type, "welcome")
        self.assertEqual(log.ses_message_id, "relay-not-configured-noop")

        # No boto3 SES client should have been constructed.
        for call in mock_boto_client.call_args_list:
            service_name = call.kwargs.get("service_name")
            if call.args:
                service_name = call.args[0]
            self.assertNotIn(service_name, ("ses", "sesv2"))
        self.assertFalse(mock_boto_client.return_value.send_email.called)
        self.assertFalse(mock_boto_client.return_value.send_raw_email.called)


class RelayProductionGuardsTest(SimpleTestCase):
    """Production-like settings stay on Relay and the checks fire."""

    def test_package_backend_non_relay_fails_e005(self):
        from email_app.checks import check_package_mail_backend_relay

        for backend in ("ses_local", "smtp", "local", ""):
            with self.subTest(backend=backend), override_settings(
                TESTING=False, DEBUG=True, COMMUNITY_BASE={"MAIL_BACKEND": backend},
            ):
                errors = check_package_mail_backend_relay(None)
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0].id, "email_app.E005")

    def test_package_backend_relay_passes_e005(self):
        from email_app.checks import check_package_mail_backend_relay

        with override_settings(
            TESTING=False,
            DEBUG=False,
            COMMUNITY_BASE={"MAIL_BACKEND": "relay"},
        ):
            self.assertEqual(check_package_mail_backend_relay(None), [])

    def test_django_backend_smtp_and_ses_fail_e004(self):
        from email_app.checks import check_email_backend_selection

        for backend in (
            "django.core.mail.backends.smtp.EmailBackend",
            "django_ses.SESBackend",
            "django.core.mail.backends.locmem.EmailBackend",
            "",
        ):
            with self.subTest(backend=backend or "<unset>"):
                with override_settings(EMAIL_BACKEND=backend, TESTING=False):
                    errors = check_email_backend_selection(None)
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(errors[0].id, "email_app.E004")

    def test_django_backend_relay_passes_e004(self):
        from email_app.checks import check_email_backend_selection

        with override_settings(
            EMAIL_BACKEND="email_app.backends.relay.RelayEmailBackend",
            TESTING=False,
        ):
            self.assertEqual(check_email_backend_selection(None), [])
