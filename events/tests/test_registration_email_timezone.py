"""End-to-end tests for issue #666: registration emails render times in
the recipient's preferred timezone (with IANA label), and fall back to
literal UTC when no preference is set.

The send path goes through ``send_registration_confirmation`` with SES
disabled so no network call is attempted; we capture the rendered HTML
body off the EmailService directly to assert against. This exercises
the production caller (not just the helper) so a regression at the
caller site — e.g. someone reintroducing ``event.formatted_start()`` —
is caught here.
"""

import email
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventRegistration
from events.services.registration_email import send_registration_confirmation
from integrations.config import clear_config_cache
from payments.models import Tier


@override_settings(
    SITE_BASE_URL='https://env.example.com',
    SES_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com',
    AWS_SES_REGION='us-east-1',
    AWS_ACCESS_KEY_ID='test-key',
    AWS_SECRET_ACCESS_KEY='test-secret',
    SES_ENABLED=True,
)
class RegistrationEmailTimezoneTest(TestCase):
    """Issue #666: ``event_datetime`` in the rendered body is the user's
    local time + IANA label, NEVER the legacy ``%H:%M UTC`` string."""

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        # 2026-06-01 16:00 UTC is the spec's acceptance pin. June is in
        # CEST so this also exercises the DST branch end-to-end.
        cls.event = Event.objects.create(
            slug='tz-event',
            title='Timezone Event',
            description='Event used in TZ tests.',
            start_datetime=datetime(2026, 6, 1, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 1, 17, 0, tzinfo=UTC),
            status='upcoming',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _capture_html(self, user):
        """Send the registration email and return the rendered HTML body."""
        registration = EventRegistration.objects.create(
            event=self.event, user=user,
        )
        with patch('events.services.registration_email.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_client.send_email.return_value = {'MessageId': 'tz-msg-1'}
            mock_boto3.client.return_value = mock_client
            send_registration_confirmation(registration)
            raw = mock_client.send_email.call_args[1]['Content']['Raw']['Data']
        msg = email.message_from_string(raw)
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                return part.get_payload(decode=True).decode('utf-8')
        raise AssertionError('No text/html part in registration email')

    def test_berlin_user_sees_cest_local_time_in_body(self):
        """Acceptance criterion: user with TZ=Europe/Berlin, event at
        2026-06-01 16:00 UTC sees 18:00 Europe/Berlin (not 16:00 UTC).
        The literal ``UTC`` token must not appear anywhere in the body
        when the recipient has a valid timezone preference.
        """
        user = User.objects.create_user(
            email='berlin@example.com',
            preferred_timezone='Europe/Berlin',
        )

        html = self._capture_html(user)

        self.assertIn('18:00 Europe/Berlin', html)
        # The legacy "%H:%M UTC" string MUST be gone, and ``UTC`` MUST
        # NOT appear anywhere in the email body when the recipient has
        # a valid IANA preference set.
        self.assertNotIn('16:00 UTC', html)
        self.assertNotIn('UTC', html)

    def test_empty_preference_renders_utc_label(self):
        """Acceptance criterion: empty preferred_timezone renders the
        time in UTC with the literal ``UTC`` token appended.
        """
        user = User.objects.create_user(
            email='no-tz@example.com',
            preferred_timezone='',
        )

        html = self._capture_html(user)

        self.assertIn('16:00 UTC', html)
        self.assertNotIn('Europe/Berlin', html)

    def test_invalid_iana_string_falls_back_to_utc(self):
        """Acceptance criterion: bogus stored TZ does NOT crash and
        renders in UTC.
        """
        user = User.objects.create_user(
            email='bogus@example.com',
            preferred_timezone='Not/AZone',
        )

        html = self._capture_html(user)

        self.assertIn('UTC', html)
        # Defensive: no Europe/Berlin should appear (would mean the
        # invalid string was treated as valid somehow).
        self.assertNotIn('Europe/Berlin', html)

    def test_kolkata_half_hour_offset_user(self):
        """Acceptance criterion: Asia/Kolkata (UTC+05:30) renders
        18:00 Asia/Kolkata for a 12:30 UTC event.
        """
        # Override the fixture event with a 12:30 UTC start. Keep all
        # other fields stable so the rest of the wiring is unchanged.
        event = Event.objects.create(
            slug='tz-event-kolkata',
            title='Timezone Event Kolkata',
            description='Event used in TZ tests.',
            start_datetime=datetime(2026, 6, 1, 12, 30, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 1, 13, 30, tzinfo=UTC),
            status='upcoming',
        )
        user = User.objects.create_user(
            email='kolkata@example.com',
            preferred_timezone='Asia/Kolkata',
        )
        registration = EventRegistration.objects.create(event=event, user=user)

        with patch('events.services.registration_email.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_client.send_email.return_value = {'MessageId': 'tz-msg-2'}
            mock_boto3.client.return_value = mock_client
            send_registration_confirmation(registration)
            raw = mock_client.send_email.call_args[1]['Content']['Raw']['Data']
        msg = email.message_from_string(raw)
        html = next(
            part.get_payload(decode=True).decode('utf-8')
            for part in msg.walk()
            if part.get_content_type() == 'text/html'
        )

        self.assertIn('18:00 Asia/Kolkata', html)


@override_settings(SES_ENABLED=False)
class RegistrationEmailDoesNotUseFormattedStartTest(TestCase):
    """Regression guard: ``event.formatted_start()`` (hardcoded UTC)
    must NOT be used to build the email context. The test patches
    ``Event.formatted_start`` to raise; if any future change reintroduces
    that call in the email path the registration send will explode here.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='no-formatted-start@example.com',
            preferred_timezone='Europe/Berlin',
        )
        cls.event = Event.objects.create(
            slug='no-formatted-start-event',
            title='No formatted_start Event',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
            status='upcoming',
        )

    def test_send_registration_confirmation_does_not_call_formatted_start(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )

        # Boom if anything calls formatted_start() during the send.
        with patch(
            'events.models.Event.formatted_start',
            side_effect=AssertionError(
                'formatted_start() must not be called in the email path '
                '(issue #666)',
            ),
        ):
            send_registration_confirmation(registration)
