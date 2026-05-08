"""Regression tests for the SES_ENABLED kill-switch (issue #509).

Background
----------

Production AWS SES was being spammed by Playwright runs because the
custom EmailService and the events registration mailer both build a
boto3 SES client directly from AWS credentials and bypass Django's
EMAIL_BACKEND. A test run hitting /api/subscribe, /api/register,
/api/password-reset-request, or event registration would send real
email to ``@example.com`` synthetic addresses. SES accepted those then
bounced them, damaging sender reputation and burning bounce-volume
spend.

What we're guarding
-------------------

This module is the canary that detects any new code path that bypasses
the SES_ENABLED gate. Every test in the suite patches ``boto3.client``
globally and asserts that

1. ``settings.SES_ENABLED`` is ``False`` under ``manage.py test`` (the
   kill-switch lives in ``website/settings.py`` next to the SLACK_ENABLED
   pattern).
2. The four user-facing endpoints that send email — newsletter
   subscribe, account register, password-reset request, and event
   registration — never call ``boto3.client('sesv2', ...)`` and never
   call ``send_email`` on the resulting client.
3. Direct ``EmailService.send`` calls return an ``EmailLog`` row whose
   ``ses_message_id`` is the recognisable noop marker so observability
   is preserved.

If you are adding a new code path that sends mail, add it to this file
so the canary fires when the gate is missed.
"""

from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from email_app.models import EmailLog
from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration
from payments.models import Tier


class SESEnabledFlagTest(TestCase):
    """The kill-switch must be off whenever Django tests are running."""

    def test_ses_enabled_is_false_under_test_runner(self):
        # ``website/settings.py`` forces SES_ENABLED to False when TESTING
        # is true. If this assertion ever fails we are about to ship real
        # mail traffic from CI / local test runs.
        self.assertFalse(
            getattr(settings, "SES_ENABLED", None),
            "SES_ENABLED must be False under manage.py test (issue #509)",
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
        ``boto3.client`` factory. If a future code path slips past the
        gate this assertion fires and points at the bypass.
        """
        # No boto3 SES client should have been constructed at all.
        for call in mock_boto_client.call_args_list:
            service = call.args[0] if call.args else call.kwargs.get("service_name")
            self.assertNotEqual(
                service,
                "sesv2",
                f"boto3.client('sesv2', ...) was called: {call}. "
                f"A code path bypassed the SES_ENABLED gate (issue #509).",
            )
            self.assertNotEqual(
                service,
                "ses",
                f"boto3.client('ses', ...) was called: {call}. "
                f"A code path bypassed the SES_ENABLED gate (issue #509).",
            )
        # And the mock client returned by the factory must never have
        # received a send_email() call. We check the default return-value
        # client; whatever attribute access happens on it is recorded.
        self.assertFalse(
            mock_boto_client.return_value.send_email.called,
            "send_email() was called on a mocked boto3 client; "
            "a code path bypassed the SES_ENABLED gate (issue #509).",
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
        # This is the second-client regression canary for
        # events/services/registration_email.py — that module builds
        # its own boto3 SES client separate from EmailService.
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
    """Direct EmailService.send() returns the noop marker and logs the row."""

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
        # preserved when the kill-switch fires.
        self.assertEqual(EmailLog.objects.count(), baseline + 1)
        self.assertIsNotNone(log)
        self.assertEqual(log.user, self.user)
        self.assertEqual(log.email_type, "welcome")
        self.assertEqual(log.ses_message_id, "ses-disabled-noop")

        # No boto3 SES client should have been constructed.
        for call in mock_boto_client.call_args_list:
            service_name = call.args[0] if call.args else call.kwargs.get("service_name")
            self.assertNotIn(service_name, ("ses", "sesv2"))
        self.assertFalse(mock_boto_client.return_value.send_email.called)
