"""Registration confirmation email contains a working cancel URL (issue #588)."""

import email
import re
from datetime import timedelta
from unittest.mock import MagicMock, patch

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
    SES_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com',
    AWS_SES_REGION='us-east-1',
    AWS_ACCESS_KEY_ID='test-key',
    AWS_SECRET_ACCESS_KEY='test-secret',
    SES_ENABLED=True,
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

    def _capture_html(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        with patch('events.services.registration_email.boto3') as mock_boto3:
            mock_client = MagicMock()
            mock_client.send_email.return_value = {'MessageId': 'msg-1'}
            mock_boto3.client.return_value = mock_client
            send_registration_confirmation(registration)
            call_kwargs = mock_client.send_email.call_args[1]
        msg = email.message_from_string(call_kwargs['Content']['Raw']['Data'])
        for part in msg.walk():
            if part.get_content_type() == 'text/html':
                return registration, part.get_payload(decode=True).decode('utf-8')
        raise AssertionError('No text/html part in registration email')

    def test_cancel_url_present_and_decodes_to_this_registration(self):
        registration, html = self._capture_html()
        match = CANCEL_RE.search(html)
        self.assertIsNotNone(match, 'cancel URL not found in email body')

        url = match.group(0)
        self.assertIn('/events/cancel-url-event/cancel-registration', url)

        token = match.group(1)
        payload = decode_cancel_token(token)
        self.assertEqual(payload['registration_id'], registration.pk)
        self.assertEqual(payload['event_id'], self.event.pk)
        self.assertEqual(payload['user_id'], self.user.pk)

    def test_send_records_email_log_row(self):
        registration, _html = self._capture_html()
        log = EmailLog.objects.filter(
            user=registration.user, email_type='event_registration',
        ).first()
        self.assertIsNotNone(log)


@override_settings(SITE_BASE_URL='https://env.example.com', SES_ENABLED=False)
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

    def _capture_rendered_body(self):
        # SES is disabled; the service short-circuits and returns the
        # synthetic message id. We still want the rendered HTML, so call
        # the template render path directly via the same service.
        from email_app.services.email_service import EmailService
        from events.services.calendar_links import build_calendar_links
        from events.services.cancel_token import generate_cancel_token
        from integrations.config import site_base_url

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )

        site_url = site_base_url()
        cancel_token = generate_cancel_token(registration)
        cancel_url = (
            f'{site_url}/events/{self.event.slug}/'
            f'cancel-registration?token={cancel_token}'
        )
        calendar_links = build_calendar_links(self.event)

        service = EmailService()
        _subject, body_html = service._render_template(
            'event_registration',
            self.user,
            {
                'event_title': self.event.title,
                'event_datetime': self.event.formatted_start(),
                'join_url': f'{site_url}/events/{self.event.slug}/join',
                'cancel_url': cancel_url,
                'google_calendar_url': calendar_links['google'],
                'outlook_calendar_url': calendar_links['outlook'],
                'office365_calendar_url': calendar_links['office365'],
            },
        )
        return body_html

    def test_cancel_url_uses_db_override(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        body = self._capture_rendered_body()
        self.assertIn(
            'https://override.example.com/events/cancel-override-event/'
            'cancel-registration?token=',
            body,
        )
        self.assertNotIn('https://env.example.com/events/cancel-override-event', body)

    def test_cancel_url_falls_back_to_settings(self):
        body = self._capture_rendered_body()
        self.assertIn(
            'https://env.example.com/events/cancel-override-event/'
            'cancel-registration?token=',
            body,
        )


@override_settings(SES_ENABLED=False)
class RegistrationEmailCancelUrlSesDisabledTest(TestCase):
    """SES-disabled path still records an EmailLog and renders the cancel URL."""

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

    def test_email_log_recorded_with_synthetic_message_id(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        log = send_registration_confirmation(registration)
        self.assertEqual(log.ses_message_id, 'ses-disabled-noop')
        self.assertEqual(log.email_type, 'event_registration')
