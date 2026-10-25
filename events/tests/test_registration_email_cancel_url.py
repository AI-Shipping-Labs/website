"""Registration confirmation email contains a working cancel URL (issue #588)."""

import re
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from events.services.cancel_token import decode_cancel_token
from events.services.registration_email import send_registration_confirmation
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from payments.models import Tier

CANCEL_RE = re.compile(
    r'https?://[^"\s)<>]+/events/[^/]+/cancel-registration\?token=([^"&\s<>)]+)',
)


@override_settings(
    SITE_BASE_URL='https://env.example.com',
    RELAY_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com',
    AWS_ACCESS_KEY_ID='test-key',
    AWS_SECRET_ACCESS_KEY='test-secret',
)
class RegistrationEmailCancelUrlTest(TestCase):
    """The rendered HTML body carries a valid cancel URL bound to this registration."""

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='cancel-url@example.com',
            password='secret1234',
            first_name='Cancel',
        )
        cls.event = Event.objects.create(
            slug='cancel-url-event',
            title='Cancel URL Event',
            description='Test event for cancel URL.',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
            status='upcoming',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _capture_cancel_url(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        with patch(
            'events.services.registration_email._send_calendar_email',
        ) as mock_send:
            mock_send.return_value = 'relay-1'
            send_registration_confirmation(registration)
        return registration, mock_send.call_args.kwargs['context']['cancel_url']

    def test_cancel_url_present_and_decodes_to_this_registration(self):
        registration, url = self._capture_cancel_url()
        match = CANCEL_RE.search(url)
        self.assertIsNotNone(match, 'cancel URL not found in email body')

        self.assertIn('/events/cancel-url-event/cancel-registration', url)

        token = match.group(1)
        payload = decode_cancel_token(token)
        self.assertEqual(payload['registration_id'], registration.pk)
        self.assertEqual(payload['event_id'], self.event.pk)
        self.assertEqual(payload['user_id'], self.user.pk)

    def test_send_records_email_log_row(self):
        registration, _url = self._capture_cancel_url()
        log = EmailLog.objects.filter(
            user=registration.user, email_type='event_registration',
        ).first()
        self.assertIsNotNone(log)


@override_settings(SITE_BASE_URL='https://env.example.com')
class RegistrationEmailCancelUrlOverrideTest(TestCase):
    """The cancel URL host respects the Studio ``SITE_BASE_URL`` override.

    Same parametrization as ``RegistrationEmailJoinUrlOverrideTest`` —
    operators can swap the site URL via Studio without redeploying, and
    the cancel URL must follow.
    """

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='cancel-override@example.com',
            password='secret1234',
        )
        cls.event = Event.objects.create(
            slug='cancel-override-event',
            title='Cancel Override Event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _capture_cancel_url(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        with patch(
            'events.services.registration_email._send_calendar_email',
            return_value='relay-override',
        ) as send:
            send_registration_confirmation(registration)
        return send.call_args.kwargs['context']['cancel_url']

    def test_cancel_url_uses_db_override(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        url = self._capture_cancel_url()
        self.assertTrue(url.startswith(
            'https://override.example.com/events/cancel-override-event/'
            'cancel-registration?token=',
        ))

    def test_cancel_url_falls_back_to_settings(self):
        url = self._capture_cancel_url()
        self.assertTrue(url.startswith(
            'https://env.example.com/events/cancel-override-event/'
            'cancel-registration?token=',
        ))


class RegistrationEmailRelayTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='cancel-noop@example.com',
            password='secret1234',
        )
        cls.event = Event.objects.create(
            slug='cancel-noop-event',
            title='Cancel Noop Event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    @patch(
        'events.services.registration_email._send_calendar_email',
        return_value='relay-registration',
    )
    def test_email_log_records_relay_message_id(self, mock_send):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        log = send_registration_confirmation(registration)
        self.assertEqual(mock_send.call_count, 1)
        call_kwargs = mock_send.call_args.kwargs
        self.assertEqual(call_kwargs['template_key'], 'event_registration')
        self.assertEqual(call_kwargs['to_email'], self.user.email)
        self.assertEqual(call_kwargs['context']['event_title'], self.event.title)
        self.assertEqual(log.ses_message_id, 'relay-registration')
        self.assertEqual(log.email_type, 'event_registration')
