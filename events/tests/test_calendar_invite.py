"""Tests for calendar invite generation and registration email sending."""

import email
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from email_app.tests.test_email_service import assert_no_internal_footer_text
from events.models import Event, EventRegistration
from events.services.calendar_invite import generate_ics
from events.services.registration_email import send_registration_confirmation
from payments.models import Tier


@override_settings(
    SITE_BASE_URL='https://aishippinglabs.com',
    SES_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com',
)
class GenerateIcsTest(TestCase):
    """Tests for .ics calendar file generation."""

    @classmethod
    def setUpTestData(cls):
        cls.start = timezone.now() + timedelta(days=1)
        cls.end = cls.start + timedelta(hours=2)
        cls.event = Event.objects.create(
            slug='ai-workshop',
            title='AI Agents Workshop',
            description='Learn about AI agents.',
            start_datetime=cls.start,
            end_datetime=cls.end,
            status='upcoming',
        )

    def _parse_ics(self, ics_bytes):
        """Parse .ics bytes into an icalendar Calendar object."""
        from icalendar import Calendar
        return Calendar.from_ical(ics_bytes)

    def test_generate_ics_contains_event_details(self):
        ics_bytes = generate_ics(self.event)
        cal = self._parse_ics(ics_bytes)

        vevents = [c for c in cal.walk() if c.name == 'VEVENT']
        self.assertEqual(len(vevents), 1)

        vevent = vevents[0]
        self.assertEqual(str(vevent.get('summary')), 'AI Agents Workshop')
        self.assertEqual(str(vevent.get('description')), 'Learn about AI agents.')
        self.assertIn('/events/ai-workshop/join', str(vevent.get('url')))
        self.assertIn('/events/ai-workshop/join', str(vevent.get('location')))

    def test_generate_ics_has_correct_start_end(self):
        ics_bytes = generate_ics(self.event)
        cal = self._parse_ics(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]

        # .ics format truncates to seconds, so compare without microseconds
        self.assertEqual(
            vevent.get('dtstart').dt.replace(tzinfo=None),
            self.start.replace(microsecond=0, tzinfo=None),
        )
        self.assertEqual(
            vevent.get('dtend').dt.replace(tzinfo=None),
            self.end.replace(microsecond=0, tzinfo=None),
        )

    def test_generate_ics_stable_uid(self):
        ics1 = generate_ics(self.event)
        ics2 = generate_ics(self.event)

        cal1 = self._parse_ics(ics1)
        cal2 = self._parse_ics(ics2)

        uid1 = str([c for c in cal1.walk() if c.name == 'VEVENT'][0].get('uid'))
        uid2 = str([c for c in cal2.walk() if c.name == 'VEVENT'][0].get('uid'))

        self.assertEqual(uid1, uid2)
        self.assertEqual(uid1, 'event-ai-workshop@aishippinglabs.com')

    def test_generate_ics_no_end_datetime_defaults_to_start_plus_one_hour(self):
        event_no_end = Event.objects.create(
            slug='quick-chat',
            title='Quick Chat',
            start_datetime=self.start,
            end_datetime=None,
            status='upcoming',
        )
        ics_bytes = generate_ics(event_no_end)
        cal = self._parse_ics(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]

        self.assertEqual(
            vevent.get('dtstart').dt.replace(tzinfo=None),
            self.start.replace(microsecond=0, tzinfo=None),
        )
        expected_end = (self.start + timedelta(hours=1)).replace(microsecond=0)
        self.assertEqual(
            vevent.get('dtend').dt.replace(tzinfo=None),
            expected_end.replace(tzinfo=None),
        )

    def test_generate_ics_cancel_method(self):
        ics_bytes = generate_ics(self.event, method='CANCEL')
        cal = self._parse_ics(ics_bytes)
        self.assertEqual(str(cal.get('method')), 'CANCEL')

    def test_generate_ics_sequence_matches_event(self):
        self.event.ics_sequence = 3
        self.event.save()
        self.event.refresh_from_db()

        ics_bytes = generate_ics(self.event)
        cal = self._parse_ics(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]

        self.assertEqual(vevent.get('sequence'), 3)

    def test_generate_ics_organizer_email(self):
        ics_bytes = generate_ics(self.event)
        cal = self._parse_ics(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]

        organizer = vevent.get('organizer')
        self.assertIn('noreply@aishippinglabs.com', str(organizer))

    def test_generate_ics_join_url_not_zoom(self):
        """Join URL uses the site join path, not direct Zoom URL."""
        event = Event.objects.create(
            slug='zoom-event',
            title='Zoom Workshop',
            start_datetime=self.start,
            status='upcoming',
            zoom_join_url='https://zoom.us/j/123456',
        )
        ics_bytes = generate_ics(event)
        ics_str = ics_bytes.decode('utf-8')

        self.assertIn('/events/zoom-event/join', ics_str)
        self.assertNotIn('zoom.us', ics_str)


@override_settings(
    SITE_BASE_URL='https://aishippinglabs.com',
    SES_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com',
    AWS_SES_REGION='us-east-1',
    AWS_ACCESS_KEY_ID='test-key',
    AWS_SECRET_ACCESS_KEY='test-secret',
    # Issue #509: this suite asserts on the SES wire-format (boto3 mocked).
    # The SES_ENABLED kill-switch defaults False under TESTING, so we have to
    # opt in here to exercise the send path.
    SES_ENABLED=True,
)
class SendRegistrationConfirmationTest(TestCase):
    """Tests for sending registration confirmation emails."""

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='test@example.com',
            password='testpass123',
            first_name='Test',
        )
        cls.user.tier = cls.tier
        cls.user.save()

        cls.start = timezone.now() + timedelta(days=1)
        cls.event = Event.objects.create(
            slug='test-event',
            title='Test Event',
            description='A test event.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=2),
            status='upcoming',
        )

    @patch('events.services.registration_email.boto3')
    def test_send_calendar_invite_calls_ses(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'test-msg-123'}
        mock_boto3.client.return_value = mock_client

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        result = send_registration_confirmation(registration)

        mock_client.send_email.assert_called_once()
        call_kwargs = mock_client.send_email.call_args[1]

        # Verify Raw format is used
        self.assertIn('Raw', call_kwargs['Content'])

        # Verify the email was sent to the right address
        self.assertEqual(call_kwargs['Destination']['ToAddresses'], ['test@example.com'])
        self.assertEqual(call_kwargs['FromEmailAddress'], 'noreply@aishippinglabs.com')

        # Verify EmailLog was created
        self.assertIsNotNone(result)
        self.assertEqual(result.email_type, 'event_registration')
        self.assertEqual(result.user, self.user)
        self.assertEqual(result.ses_message_id, 'test-msg-123')

    def _parse_raw_email(self, raw_data):
        """Parse raw MIME email string into an email.message.Message."""
        return email.message_from_string(raw_data)

    def _get_parts(self, msg):
        """Extract decoded parts from a multipart MIME message."""
        parts = {}
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if payload:
                parts[ct] = payload.decode('utf-8')
        return parts

    @patch('events.services.registration_email.boto3')
    def test_send_email_contains_ics_attachment(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-456'}
        mock_boto3.client.return_value = mock_client

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        send_registration_confirmation(registration)

        call_kwargs = mock_client.send_email.call_args[1]
        raw_data = call_kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)
        parts = self._get_parts(msg)

        # Check that there is a calendar part with .ics content
        calendar_types = [
            ct for ct in parts if 'calendar' in ct
        ]
        self.assertTrue(calendar_types, 'No text/calendar part found in email')

        ics_content = parts[calendar_types[0]]
        self.assertIn('VCALENDAR', ics_content)
        self.assertIn('VEVENT', ics_content)

        # Check filename in raw headers
        self.assertIn('event.ics', raw_data)

    @patch('events.services.registration_email.boto3')
    def test_send_email_html_footer_has_no_unsubscribe_link(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-clean'}
        mock_boto3.client.return_value = mock_client

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        send_registration_confirmation(registration)

        call_kwargs = mock_client.send_email.call_args[1]
        raw_data = call_kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)
        parts = self._get_parts(msg)
        html = parts['text/html']

        self.assertIn('email-footer', html)
        self.assertNotIn('/api/unsubscribe?token=', html)
        self.assertNotIn('Unsubscribe', html)
        assert_no_internal_footer_text(self, html)

    @patch('events.services.registration_email.boto3')
    def test_send_email_subject_contains_event_title(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-789'}
        mock_boto3.client.return_value = mock_client

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        send_registration_confirmation(registration)

        call_kwargs = mock_client.send_email.call_args[1]
        raw_data = call_kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)

        # Issue #484: confirmation subject was rewritten to confirm
        # registration up-front rather than just naming the event.
        self.assertEqual(msg['Subject'], "You're registered: Test Event")

    @patch('events.services.registration_email.boto3')
    def test_send_email_body_contains_join_link(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-body'}
        mock_boto3.client.return_value = mock_client

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        send_registration_confirmation(registration)

        call_kwargs = mock_client.send_email.call_args[1]
        raw_data = call_kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)
        parts = self._get_parts(msg)

        html_body = parts.get('text/html', '')
        self.assertIn('/events/test-event/join', html_body)

    @patch('events.services.registration_email.boto3')
    def test_send_email_delivers_to_unsubscribed_user(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-unsub'}
        mock_boto3.client.return_value = mock_client
        unsubscribed_user = User.objects.create_user(
            email='unsub@example.com',
            password='testpass123',
            unsubscribed=True,
        )
        registration = EventRegistration.objects.create(
            event=self.event, user=unsubscribed_user,
        )

        result = send_registration_confirmation(registration)
        self.assertIsNotNone(result)
        mock_client.send_email.assert_called_once()
        self.assertEqual(result.email_type, 'event_registration')
        self.assertEqual(result.ses_message_id, 'msg-unsub')


@override_settings(
    SITE_BASE_URL='https://aishippinglabs.com',
    SES_TRANSACTIONAL_FROM_EMAIL='noreply@aishippinglabs.com',
    AWS_SES_REGION='us-east-1',
    AWS_ACCESS_KEY_ID='test-key',
    AWS_SECRET_ACCESS_KEY='test-secret',
    # Issue #509: opt in so the registration API actually exercises the
    # boto3-mocked send path instead of short-circuiting.
    SES_ENABLED=True,
)
class RegistrationApiEmailTest(TestCase):
    """Test that the registration API sends confirmation emails."""

    @classmethod
    def setUpTestData(cls):
        cls.tier = Tier.objects.get_or_create(
            slug='free', defaults={'name': 'Free', 'level': 0},
        )[0]
        cls.user = User.objects.create_user(
            email='api@example.com',
            password='testpass123',
            email_verified=True,
        )
        cls.user.tier = cls.tier
        cls.user.save()

        cls.event = Event.objects.create(
            slug='api-event',
            title='API Event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
        )

    @patch('events.services.registration_email.boto3')
    def test_registration_api_sends_email(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'api-msg'}
        mock_boto3.client.return_value = mock_client

        self.client.login(email='api@example.com', password='testpass123')
        response = self.client.post(f'/api/events/{self.event.slug}/register')

        self.assertEqual(response.status_code, 201)
        mock_client.send_email.assert_called_once()

    @patch('events.services.registration_email.send_registration_confirmation')
    def test_registration_api_returns_201_on_email_failure(self, mock_send):
        mock_send.side_effect = Exception('SES is down')

        self.client.login(email='api@example.com', password='testpass123')
        with self.assertLogs('events.views.api', level='ERROR') as logs:
            response = self.client.post(f'/api/events/{self.event.slug}/register')

        # Registration should still succeed
        self.assertEqual(response.status_code, 201)
        self.assertIn(
            'Failed to send registration email for event "api-event" '
            'to user api@example.com',
            logs.output[0],
        )

        # User should still be registered
        self.assertTrue(
            EventRegistration.objects.filter(
                event=self.event, user=self.user,
            ).exists()
        )
