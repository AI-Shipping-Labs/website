"""``.ics`` join URL and registration confirmation email URL must
respect the Studio DB override of ``SITE_BASE_URL`` (issue #435).
"""

import email
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventRegistration
from events.services.calendar_invite import generate_ics
from events.services.registration_email import send_registration_confirmation
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from payments.models import Tier


def _set_override(value):
    IntegrationSetting.objects.create(
        key='SITE_BASE_URL', value=value, group='site',
    )
    clear_config_cache()


def _join_url_line(ics_bytes):
    """Return the URL: line from .ics bytes."""
    text = ics_bytes.decode('utf-8')
    for line in text.splitlines():
        if line.startswith('URL:'):
            return line
    raise AssertionError('No URL: line found in .ics output')


@override_settings(SITE_BASE_URL='https://env.example.com')
class IcsJoinUrlOverrideTest(TestCase):
    """Calendar invite join URL tracks the override."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            slug='override-event',
            title='Override Test Event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_ics_join_url_uses_db_override(self):
        _set_override('https://override.example.com')
        ics_bytes = generate_ics(self.event)
        url_line = _join_url_line(ics_bytes)
        self.assertIn(
            'https://override.example.com/events/override-event/join',
            url_line,
        )
        self.assertNotIn('https://env.example.com', url_line)

    def test_ics_join_url_falls_back_to_settings(self):
        # No override row => env value used. Regression guard.
        ics_bytes = generate_ics(self.event)
        url_line = _join_url_line(ics_bytes)
        self.assertIn(
            'https://env.example.com/events/override-event/join',
            url_line,
        )


@override_settings(
    SITE_BASE_URL='https://env.example.com',
    SES_FROM_EMAIL='community@aishippinglabs.com',
    AWS_SES_REGION='us-east-1',
    AWS_ACCESS_KEY_ID='test-key',
    AWS_SECRET_ACCESS_KEY='test-secret',
)
class RegistrationEmailJoinUrlOverrideTest(TestCase):
    """Registration confirmation email join URL tracks the override."""

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='reg-override@example.com',
            password='secure1234',
            first_name='Reg',
        )
        cls.event = Event.objects.create(
            slug='reg-event',
            title='Registration Event',
            description='Test',
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
            status='upcoming',
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _capture_raw_email(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        with patch(
            'events.services.registration_email.boto3'
        ) as mock_boto3:
            mock_client = MagicMock()
            mock_client.send_email.return_value = {'MessageId': 'msg-1'}
            mock_boto3.client.return_value = mock_client
            send_registration_confirmation(registration)
            call_kwargs = mock_client.send_email.call_args[1]
        raw = call_kwargs['Content']['Raw']['Data']
        return email.message_from_string(raw)

    def _get_html_body(self, msg):
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                return part.get_payload(decode=True).decode('utf-8')
        raise AssertionError('No text/html part in registration email')

    def test_registration_email_join_url_uses_db_override(self):
        _set_override('https://override.example.com')
        msg = self._capture_raw_email()
        html = self._get_html_body(msg)
        self.assertIn(
            'https://override.example.com/events/reg-event/join',
            html,
        )
        self.assertNotIn(
            'https://env.example.com/events/reg-event/join',
            html,
        )

    def test_registration_email_join_url_falls_back_to_settings(self):
        msg = self._capture_raw_email()
        html = self._get_html_body(msg)
        self.assertIn(
            'https://env.example.com/events/reg-event/join',
            html,
        )
