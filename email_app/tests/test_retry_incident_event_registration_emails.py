"""Safety checks for the narrowly scoped registration retry command."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from email_app.models import RelayTransactionalDelivery
from events.models import Event, EventRegistration

COMMAND_MODULE = "email_app.management.commands.retry_incident_event_registration_emails"


class RetryIncidentEventRegistrationEmailsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        start = timezone.now() + timedelta(days=5)
        cls.event = Event.objects.create(
            title="Incident test event",
            slug="incident-test-event",
            description="Test registration retry command.",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="upcoming",
        )
        cls.user = User.objects.create_user(email="retry@example.com")
        cls.registration = EventRegistration.objects.create(event=cls.event, user=cls.user)
        cls.key = f"event-registration:{cls.event.pk}:{cls.user.pk}:{cls.registration.pk}"
        cls.delivery = RelayTransactionalDelivery.objects.create(
            idempotency_key=cls.key,
            template_key="event_registration",
            safe_correlation=f"event={cls.event.pk} user={cls.user.pk}",
            status=RelayTransactionalDelivery.STATUS_FAILED,
            error_code="relay_http_error",
        )

    def _incident_map(self):
        return {self.registration.pk: (self.event.pk, self.user.pk)}

    def test_defaults_to_dry_run_and_shows_the_existing_key(self):
        output = StringIO()
        with (
            patch(f"{COMMAND_MODULE}.INCIDENT_REGISTRATIONS", self._incident_map()),
            patch(f"{COMMAND_MODULE}.send_registration_confirmation") as send,
        ):
            call_command("retry_incident_event_registration_emails", stdout=output)

        send.assert_not_called()
        self.assertIn("registrations=1 retryable=1", output.getvalue())
        self.assertIn(f"key={self.key}", output.getvalue())
        self.assertIn("mode=dry_run", output.getvalue())

    def test_send_requires_the_exact_intake_approval_note(self):
        with (
            patch(f"{COMMAND_MODULE}.INCIDENT_REGISTRATIONS", self._incident_map()),
            patch(f"{COMMAND_MODULE}.send_registration_confirmation") as send,
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "retry_incident_event_registration_emails",
                    registration_ids=[self.registration.pk],
                    send=True,
                    stdout=StringIO(),
                )

        send.assert_not_called()

    def test_approved_send_calls_the_existing_registration_service(self):
        output = StringIO()
        with (
            patch(f"{COMMAND_MODULE}.INCIDENT_REGISTRATIONS", self._incident_map()),
            patch(f"{COMMAND_MODULE}.send_registration_confirmation") as send,
        ):
            call_command(
                "retry_incident_event_registration_emails",
                registration_ids=[self.registration.pk],
                send=True,
                approval_note="resend approved by Alexey",
                stdout=output,
            )

        send.assert_called_once_with(self.registration)
        self.assertIn("sent=1 failed=0", output.getvalue())

    def test_already_accepted_delivery_is_skipped(self):
        self.delivery.status = RelayTransactionalDelivery.STATUS_ACCEPTED
        self.delivery.save(update_fields=["status"])
        output = StringIO()
        with (
            patch(f"{COMMAND_MODULE}.INCIDENT_REGISTRATIONS", self._incident_map()),
            patch(f"{COMMAND_MODULE}.send_registration_confirmation") as send,
        ):
            call_command(
                "retry_incident_event_registration_emails",
                registration_ids=[self.registration.pk],
                stdout=output,
            )

        send.assert_not_called()
        self.assertIn("retryable=0 already_accepted=1", output.getvalue())

    def test_registration_mismatch_fails_before_sending(self):
        wrong_incident = {self.registration.pk: (self.event.pk + 1, self.user.pk)}
        with (
            patch(f"{COMMAND_MODULE}.INCIDENT_REGISTRATIONS", wrong_incident),
            patch(f"{COMMAND_MODULE}.send_registration_confirmation") as send,
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "retry_incident_event_registration_emails",
                    registration_ids=[self.registration.pk],
                    stdout=StringIO(),
                )

        send.assert_not_called()
