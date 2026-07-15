"""Tests for calendar invite generation and registration email sending."""

import datetime
import email
import re
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone
from icalendar import Calendar

from accounts.models import User
from email_app.tests.test_email_service import assert_no_internal_footer_text
from events.models import Event, EventRegistration
from events.services.calendar_invite import (
    AUDIENCE_ATTENDEE,
    AUDIENCE_HOST,
    AUDIENCE_PUBLIC_FEED,
    InvalidCalendarOrganizerError,
    build_vevent,
    generate_ics,
    normalize_calendar_organizer_email,
)
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

    def _vevent(self, ics_bytes):
        cal = self._parse_ics(ics_bytes)
        return [c for c in cal.walk() if c.name == 'VEVENT'][0]

    def test_generate_ics_contains_event_details(self):
        ics_bytes = generate_ics(self.event)
        cal = self._parse_ics(ics_bytes)
        vevents = [c for c in cal.walk() if c.name == 'VEVENT']
        self.assertEqual(len(vevents), 1)

        vevent = vevents[0]
        self.assertEqual(str(vevent.get('summary')), 'AI Agents Workshop')
        join_url = (
            f'https://aishippinglabs.com{self.event.get_join_url()}'
        )
        description = str(vevent.get('description'))
        self.assertIn('Learn about AI agents.', description)
        self.assertIn(f'Join: {join_url}', description)
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)

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
        self.assertEqual(str(organizer), 'mailto:noreply@aishippinglabs.com')
        self.assertEqual(str(organizer.params['CN']), 'AI Shipping Labs')

    @override_settings(
        SES_TRANSACTIONAL_FROM_EMAIL=(
            'AI Shipping Labs <content@aishippinglabs.com>'
        ),
    )
    def test_formatted_sender_uses_bare_mailbox_in_organizer(self):
        organizer = self._vevent(generate_ics(self.event)).get('organizer')

        self.assertEqual(str(organizer), 'mailto:content@aishippinglabs.com')
        self.assertEqual(str(organizer.params['CN']), 'AI Shipping Labs')
        self.assertNotIn('AI Shipping Labs <', str(organizer))

    @override_settings(SES_TRANSACTIONAL_FROM_EMAIL='')
    def test_missing_sender_uses_existing_transactional_default(self):
        organizer = self._vevent(generate_ics(self.event)).get('organizer')

        self.assertEqual(str(organizer), 'mailto:noreply@aishippinglabs.com')

    @override_settings(SES_TRANSACTIONAL_FROM_EMAIL='not-an-email')
    def test_invalid_resolved_sender_prevents_calendar_generation(self):
        with self.assertRaises(InvalidCalendarOrganizerError):
            generate_ics(self.event)

    def test_invalid_or_injected_sender_fails_closed_without_echoing_value(self):
        unsafe_values = [
            '',
            'not-an-email',
            'first@example.com, second@example.com',
            'Sender\r\nBcc: victim@example.com <sender@example.com>',
            '\rleading@example.com',
            'trailing@example.com\r',
            '\nleading@example.com',
            'trailing@example.com\n',
            '\tleading@example.com',
            'trailing@example.com\t',
            '\r\n\tMixed <mixed@example.com>\t\n\r',
        ]

        for value in unsafe_values:
            with self.subTest(value=repr(value)):
                with self.assertRaises(InvalidCalendarOrganizerError) as ctx:
                    normalize_calendar_organizer_email(value)
                if value:
                    self.assertNotIn(value, str(ctx.exception))

    def test_normalizer_preserves_valid_bare_and_formatted_sender_cases(self):
        valid_cases = {
            'content@aishippinglabs.com': 'content@aishippinglabs.com',
            'AI Shipping Labs <content@aishippinglabs.com>': (
                'content@aishippinglabs.com'
            ),
            '  AI Shipping Labs <content@aishippinglabs.com>  ': (
                'content@aishippinglabs.com'
            ),
        }

        for value, expected in valid_cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_calendar_organizer_email(value), expected)

    def test_generate_ics_join_url_not_zoom(self):
        """Attendee community invites point at /join, not raw Zoom."""
        event = Event.objects.create(
            slug='zoom-event',
            title='Zoom Workshop',
            start_datetime=self.start,
            status='upcoming',
            zoom_join_url='https://zoom.us/j/123456',
        )
        ics_bytes = generate_ics(event)
        vevent = self._vevent(ics_bytes)
        join_url = f'https://aishippinglabs.com{event.get_join_url()}'

        self.assertIn(f'Join: {join_url}', str(vevent.get('description')))
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertNotIn('zoom.us', ics_bytes.decode('utf-8'))

    def test_external_attendee_ics_does_not_invent_internal_join_url(self):
        event = Event.objects.create(
            slug='partner-cohort',
            title='Partner Cohort',
            description='Hosted off-platform.',
            start_datetime=self.start,
            status='upcoming',
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/cohort',
        )

        ics_bytes = generate_ics(event)
        vevent = self._vevent(ics_bytes)
        detail_url = f'https://aishippinglabs.com{event.get_absolute_url()}'
        internal_join_url = (
            f'https://aishippinglabs.com{event.get_join_url()}'
        )

        self.assertIn(f'Join: {detail_url}', str(vevent.get('description')))
        self.assertEqual(str(vevent.get('url')), detail_url)
        self.assertEqual(str(vevent.get('location')), 'Maven')
        self.assertNotIn(internal_join_url, str(vevent.get('description')))
        self.assertNotEqual(str(vevent.get('url')), internal_join_url)
        self.assertNotEqual(str(vevent.get('location')), internal_join_url)


@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class MembersOnlySummaryAudienceTest(TestCase):
    """Issue #1072: ``[Members only]`` is scoped to the public feed only.

    The prefix exists for discovery on the anonymous feed at
    ``/events/calendar.ics``; attendee and host invites already know the
    tier, so they must not carry it. The ``[Hosted on X]`` prefix is a
    separate concern that stays on every audience.
    """

    @classmethod
    def setUpTestData(cls):
        cls.start = timezone.now() + timedelta(days=1)
        cls.gated = Event.objects.create(
            slug='gated-evt',
            title='Exploring Vercel',
            description='Gated body text.',
            start_datetime=cls.start,
            status='upcoming',
            required_level=20,
        )
        cls.gated_external = Event.objects.create(
            slug='gated-external-evt',
            title='LLM Cohort',
            description='Gated external body.',
            start_datetime=cls.start,
            status='upcoming',
            required_level=20,
            external_host='Maven',
            zoom_join_url='https://maven.com/aisl/llm',
        )
        cls.open_event = Event.objects.create(
            slug='open-evt',
            title='Open Meetup',
            description='Open to everyone.',
            start_datetime=cls.start,
            status='upcoming',
            required_level=0,
        )

    def test_gated_attendee_summary_has_no_members_only_prefix(self):
        vevent = build_vevent(self.gated, audience=AUDIENCE_ATTENDEE)
        self.assertEqual(str(vevent.get('summary')), 'Exploring Vercel')

    def test_gated_host_summary_has_no_members_only_prefix(self):
        vevent = build_vevent(self.gated, audience=AUDIENCE_HOST)
        self.assertEqual(str(vevent.get('summary')), 'Exploring Vercel')

    def test_gated_public_feed_summary_keeps_members_only_prefix(self):
        vevent = build_vevent(self.gated, audience=AUDIENCE_PUBLIC_FEED)
        self.assertEqual(
            str(vevent.get('summary')), '[Members only] Exploring Vercel',
        )

    def test_gated_external_attendee_keeps_hosted_drops_members_only(self):
        vevent = build_vevent(self.gated_external, audience=AUDIENCE_ATTENDEE)
        self.assertEqual(
            str(vevent.get('summary')), '[Hosted on Maven] LLM Cohort',
        )

    def test_gated_external_public_feed_keeps_both_prefixes(self):
        vevent = build_vevent(
            self.gated_external, audience=AUDIENCE_PUBLIC_FEED,
        )
        self.assertEqual(
            str(vevent.get('summary')),
            '[Members only] [Hosted on Maven] LLM Cohort',
        )

    def test_open_event_summary_is_plain_title_for_every_audience(self):
        for audience in (
            AUDIENCE_ATTENDEE, AUDIENCE_HOST, AUDIENCE_PUBLIC_FEED,
        ):
            with self.subTest(audience=audience):
                vevent = build_vevent(self.open_event, audience=audience)
                self.assertEqual(str(vevent.get('summary')), 'Open Meetup')

    def test_per_event_ics_download_drops_members_only_prefix(self):
        """``generate_ics`` defaults to the attendee audience.

        This is the per-event download at ``/events/<slug>/calendar.ics``
        and the registration-email attachment — the exact surface the
        reporter circled.
        """
        ics_bytes = generate_ics(self.gated)
        cal = Calendar.from_ical(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]
        self.assertEqual(str(vevent.get('summary')), 'Exploring Vercel')
        self.assertNotIn('[Members only]', ics_bytes.decode('utf-8'))

    def test_gated_attendee_description_keeps_full_body(self):
        """Issue #1072 touches only SUMMARY — the attendee body is intact."""
        vevent = build_vevent(self.gated, audience=AUDIENCE_ATTENDEE)
        description = str(vevent.get('description'))
        self.assertIn('Gated body text.', description)
        self.assertNotIn('members-only', description)


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
        cls.gated_event = Event.objects.create(
            slug='gated-test-event',
            title='Exploring Vercel',
            description='A gated test event.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=2),
            status='upcoming',
            required_level=20,
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

    @patch('events.services.registration_email._send_raw_email')
    def test_repeat_processing_same_registration_is_idempotent(self, mock_send):
        mock_send.return_value = 'same-registration-message'
        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )

        first_log = send_registration_confirmation(registration)
        second_log = send_registration_confirmation(registration)

        self.assertEqual(mock_send.call_count, 1)
        self.assertEqual(second_log, first_log)
        self.assertEqual(
            first_log.dedupe_key,
            f'event-registration:{self.event.pk}:{self.user.pk}:'
            f'{registration.pk}',
        )

    @patch('events.services.registration_email._send_raw_email')
    def test_reregister_after_cancellation_sends_fresh_invitation(
        self, mock_send,
    ):
        mock_send.side_effect = [
            'first-registration-message',
            'second-registration-message',
        ]
        first_registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )

        first_log = send_registration_confirmation(first_registration)
        first_registration_pk = first_registration.pk
        first_registration.delete()
        second_registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        second_log = send_registration_confirmation(second_registration)

        self.assertEqual(mock_send.call_count, 2)
        self.assertNotEqual(second_registration.pk, first_registration_pk)
        self.assertNotEqual(second_log, first_log)
        self.assertNotEqual(second_log.dedupe_key, first_log.dedupe_key)
        self.assertEqual(
            first_log.dedupe_key,
            f'event-registration:{self.event.pk}:{self.user.pk}:'
            f'{first_registration_pk}',
        )
        self.assertEqual(
            second_log.dedupe_key,
            f'event-registration:{self.event.pk}:{self.user.pk}:'
            f'{second_registration.pk}',
        )
        for call in mock_send.call_args_list:
            calendar = Calendar.from_ical(call.kwargs['ics_content'])
            self.assertEqual(str(calendar.get('method')), 'REQUEST')

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

    def _get_calendar_part(self, msg):
        for part in msg.walk():
            if part.get_content_type() == 'text/calendar':
                return part.get_payload(decode=True)
        raise AssertionError('No text/calendar part found in email')

    @patch('events.services.registration_email.boto3')
    def test_send_email_calendar_is_multipart_alternative_sibling(self, mock_boto3):
        """Issue #1088: the text/calendar part is delivered as a
        multipart/alternative sibling of the HTML body, NOT as a
        Content-Disposition: attachment, so Gmail merges by UID in place.
        """
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

        # A multipart/alternative container must exist.
        alternative_parts = [
            part for part in msg.walk()
            if part.get_content_type() == 'multipart/alternative'
        ]
        self.assertTrue(
            alternative_parts,
            'No multipart/alternative container found in email',
        )
        alternative = alternative_parts[0]

        # The text/html body and the text/calendar part are SIBLINGS inside
        # the same multipart/alternative container.
        child_types = [
            child.get_content_type()
            for child in alternative.get_payload()
        ]
        self.assertIn('text/html', child_types)
        self.assertIn('text/calendar', child_types)

        cal_part = next(
            child for child in alternative.get_payload()
            if child.get_content_type() == 'text/calendar'
        )

        # method=REQUEST on the calendar part's Content-Type.
        self.assertEqual(cal_part.get_param('method'), 'REQUEST')

        # No Content-Disposition: attachment on the calendar part.
        self.assertIsNone(cal_part.get('Content-Disposition'))
        self.assertNotIn('Content-Disposition: attachment', raw_data)

        # The .ics payload is unchanged and present in the sibling part.
        ics_content = cal_part.get_payload(decode=True).decode('utf-8')
        self.assertIn('VCALENDAR', ics_content)
        self.assertIn('VEVENT', ics_content)
        self.assertIn('UID:event-test-event@aishippinglabs.com', ics_content)
        self.assertIn('METHOD:REQUEST', ics_content)
        self.assertIn('SEQUENCE:', ics_content)
        self.assertIn('ORGANIZER', ics_content)
        self.assertIn('ATTENDEE', ics_content)

    @patch('events.services.registration_email.boto3')
    def test_send_email_ics_attachment_uses_attendee_join_url(self, mock_boto3):
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-join-ics'}
        mock_boto3.client.return_value = mock_client
        self.event.zoom_join_url = 'https://zoom.us/j/raw-secret'
        self.event.save(update_fields=['zoom_join_url'])

        registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        send_registration_confirmation(registration)

        raw_data = mock_client.send_email.call_args.kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)
        ics_bytes = self._get_calendar_part(msg)
        cal = Calendar.from_ical(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]
        join_url = f'https://aishippinglabs.com{self.event.get_join_url()}'

        self.assertIn(f'Join: {join_url}', str(vevent.get('description')))
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertEqual(str(vevent.get('uid')), 'event-test-event@aishippinglabs.com')
        self.assertNotIn('zoom.us', ics_bytes.decode('utf-8'))

    @patch('events.services.registration_email.boto3')
    def test_gated_event_ics_attachment_has_no_members_only_prefix(self, mock_boto3):
        """Issue #1072 regression: the reporter's registered gated event.

        The attendee's confirmation email ``.ics`` SUMMARY must read the
        plain event title, not ``[Members only] Exploring Vercel``.
        """
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-gated'}
        mock_boto3.client.return_value = mock_client

        registration = EventRegistration.objects.create(
            event=self.gated_event, user=self.user,
        )
        send_registration_confirmation(registration)

        raw_data = mock_client.send_email.call_args.kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)
        ics_bytes = self._get_calendar_part(msg)
        cal = Calendar.from_ical(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]
        self.assertEqual(str(vevent.get('summary')), 'Exploring Vercel')
        self.assertNotIn('[Members only]', ics_bytes.decode('utf-8'))

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
        self.assertIn(self.event.get_join_url(), html_body)
        self.assertIn('about 5 minutes before the start time', html_body)
        self.assertNotIn('15 minutes', html_body)

    @patch('events.services.registration_email.boto3')
    def test_send_email_html_body_contains_three_calendar_links(self, mock_boto3):
        """The rendered HTML must carry Google, Outlook.com, and M365 anchors."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-cal-links'}
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

        # Google: anchor to calendar.google.com with the event title encoded
        # in the ``text=`` parameter so we know the URL was built from this
        # event (not just hard-coded in the template).
        google_match = re.search(
            r'href="(https://calendar\.google\.com/calendar/render\?[^"]+)"',
            html,
        )
        self.assertIsNotNone(
            google_match, 'Google Calendar link not found in HTML body',
        )
        self.assertIn('text=Test%20Event', google_match.group(1))

        self.assertRegex(
            html,
            r'href="https://outlook\.live\.com/calendar/[^"]+"',
        )
        self.assertRegex(
            html,
            r'href="https://outlook\.office\.com/calendar/[^"]+"',
        )

    @patch('events.services.registration_email.boto3')
    def test_send_email_html_body_describes_inline_calendar_invitation(
        self, mock_boto3,
    ):
        """Visible copy must match the inline calendar alternative."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-ics-copy'}
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

        self.assertIn('includes a calendar invitation for this event', html)
        self.assertIn('if prompted', html)
        self.assertNotIn('attached', html.lower())
        self.assertNotIn('.ics file', html.lower())

    @patch('events.services.registration_email.boto3')
    def test_send_email_default_end_time_in_google_url(self, mock_boto3):
        """An event without ``end_datetime`` must render dates=start/start+1h."""
        mock_client = MagicMock()
        mock_client.send_email.return_value = {'MessageId': 'msg-default-end'}
        mock_boto3.client.return_value = mock_client

        event_no_end = Event.objects.create(
            slug='no-end-event',
            title='No End Event',
            start_datetime=datetime.datetime(
                2026, 9, 1, 14, 0, tzinfo=datetime.timezone.utc,
            ),
            end_datetime=None,
            status='upcoming',
        )
        registration = EventRegistration.objects.create(
            event=event_no_end, user=self.user,
        )
        send_registration_confirmation(registration)

        call_kwargs = mock_client.send_email.call_args[1]
        raw_data = call_kwargs['Content']['Raw']['Data']
        msg = self._parse_raw_email(raw_data)
        parts = self._get_parts(msg)
        html = parts['text/html']

        google_match = re.search(
            r'href="(https://calendar\.google\.com/calendar/render\?[^"]+)"',
            html,
        )
        self.assertIsNotNone(google_match)
        href = google_match.group(1)
        # Markdown emits ``&amp;`` separators inside href; the ``/`` inside
        # ``dates`` is percent-encoded as ``%2F`` because the URL builder
        # uses ``quote(..., safe='')``.
        self.assertIn(
            'dates=20260901T140000Z%2F20260901T150000Z',
            href,
        )

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
