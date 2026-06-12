"""Tests for the host calendar-invite email (issue #861).

Covers the service layer (``events.services.host_invite``):

- ``resolve_host_email``: per-event ``host_email`` wins; falls back to the
  ``EVENTS_HOST_INVITE_EMAIL`` config default; empty when neither set.
- ``maybe_send_initial_host_invite``: sends once per event (EmailLog guard),
  never for drafts, never when no host email resolves, and never raises.
- ``send_host_reschedule_invite``: re-issues an invite carrying the event's
  current (bumped) SEQUENCE.
- The host invite ``.ics`` shares the SAME UID + SEQUENCE as the attendee
  ``generate_ics(event)`` for that event (one de-duped calendar entry).
- The host email body carries host-only management links and not the public
  attendee join URL.
"""

import email as email_lib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from icalendar import Calendar

from email_app.models import EmailLog
from events.models import Event
from events.services.calendar_invite import generate_ics
from events.services.host_invite import (
    HOST_INVITE_EMAIL_TYPE,
    maybe_send_initial_host_invite,
    resolve_host_email,
    send_host_reschedule_invite,
)

User = get_user_model()


def _make_event(**kwargs):
    start = timezone.now() + timedelta(days=7)
    defaults = {
        'title': 'AI Shipping Workshop',
        'slug': 'ai-shipping-workshop',
        'start_datetime': start,
        'end_datetime': start + timedelta(hours=1),
        'status': 'upcoming',
        'host_email': 'host@test.com',
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


def _ics_from_raw(raw):
    """Extract and decode the text/calendar attachment from a raw SES message."""
    msg = email_lib.message_from_string(raw)
    for part in msg.walk():
        if part.get_content_type() == 'text/calendar':
            method = part.get_param('method')
            payload = part.get_payload(decode=True).decode('utf-8')
            return payload, method
    raise AssertionError('no text/calendar part in message')


def _html_from_raw(raw):
    msg = email_lib.message_from_string(raw)
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            return part.get_payload(decode=True).decode('utf-8')
    raise AssertionError('no text/html part in message')


@tag('core')
class ResolveHostEmailTest(TestCase):
    def test_explicit_host_email_wins(self):
        event = _make_event(host_email='explicit@test.com')
        with override_settings(EVENTS_HOST_INVITE_EMAIL='default@test.com'):
            self.assertEqual(resolve_host_email(event), 'explicit@test.com')

    @override_settings(EVENTS_HOST_INVITE_EMAIL='fallback@test.com')
    def test_falls_back_to_config_default_when_blank(self):
        event = _make_event(host_email='')
        self.assertEqual(resolve_host_email(event), 'fallback@test.com')

    def test_empty_when_neither_set(self):
        event = _make_event(host_email='')
        with override_settings(EVENTS_HOST_INVITE_EMAIL=''):
            self.assertEqual(resolve_host_email(event), '')


@override_settings(SES_ENABLED=True)
@tag('core')
class SendInitialHostInviteTest(TestCase):
    @patch('events.services.registration_email.boto3')
    def test_sends_invite_logs_emaillog_for_event_and_recipient(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'ses-host-1'}
        event = _make_event(host_email='host@test.com')

        log = maybe_send_initial_host_invite(event)

        self.assertIsNotNone(log)
        self.assertEqual(log.email_type, HOST_INVITE_EMAIL_TYPE)
        self.assertEqual(log.event_id, event.pk)
        self.assertEqual(log.recipient_email, 'host@test.com')
        # The SES Destination is a single To with no CC/BCC.
        dest = client.send_email.call_args.kwargs['Destination']
        self.assertEqual(dest, {'ToAddresses': ['host@test.com']})

    @patch('events.services.registration_email.boto3')
    def test_body_has_host_links_and_not_public_join(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        event = _make_event(host_email='host@test.com')

        maybe_send_initial_host_invite(event)

        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        html = _html_from_raw(raw)
        self.assertIn(f'/studio/events/{event.pk}/edit', html)
        self.assertIn(f'/studio/events/{event.pk}/create-zoom', html)
        # The public attendee join flow must NOT be the CTA in the host copy.
        self.assertNotIn(f'/events/{event.slug}/join', html)

    @patch('events.services.registration_email.boto3')
    def test_only_sent_once_per_event(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        event = _make_event(host_email='host@test.com')

        first = maybe_send_initial_host_invite(event)
        second = maybe_send_initial_host_invite(event)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(
            EmailLog.objects.filter(
                event=event, email_type=HOST_INVITE_EMAIL_TYPE,
            ).count(),
            1,
        )

    @patch('events.services.registration_email.boto3')
    def test_draft_never_invites(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        event = _make_event(status='draft', host_email='host@test.com')

        result = maybe_send_initial_host_invite(event)

        self.assertIsNone(result)
        client.send_email.assert_not_called()
        self.assertFalse(
            EmailLog.objects.filter(
                event=event, email_type=HOST_INVITE_EMAIL_TYPE,
            ).exists(),
        )

    @override_settings(EVENTS_HOST_INVITE_EMAIL='')
    @patch('events.services.registration_email.boto3')
    def test_no_host_email_skips_without_error(self, mock_boto3):
        client = mock_boto3.client.return_value
        event = _make_event(host_email='')

        result = maybe_send_initial_host_invite(event)

        self.assertIsNone(result)
        client.send_email.assert_not_called()

    @patch('events.services.host_invite._send_raw_email')
    def test_ses_failure_does_not_raise(self, mock_send):
        mock_send.side_effect = RuntimeError('SES down')
        event = _make_event(host_email='host@test.com')

        # Must swallow the failure so the Studio save is never broken, and
        # must not write a spurious EmailLog.
        result = maybe_send_initial_host_invite(event)

        self.assertIsNone(result)
        self.assertFalse(
            EmailLog.objects.filter(
                event=event, email_type=HOST_INVITE_EMAIL_TYPE,
            ).exists(),
        )

    @patch('events.services.registration_email.boto3')
    def test_links_to_existing_user_when_host_has_account(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        host_user = User.objects.create_user(
            email='host@test.com', password='x',
        )
        event = _make_event(host_email='host@test.com')

        log = maybe_send_initial_host_invite(event)

        self.assertEqual(log.user_id, host_user.pk)
        self.assertEqual(log.recipient_email, 'host@test.com')


@override_settings(SES_ENABLED=True)
@tag('core')
class HostInviteIcsTest(TestCase):
    @patch('events.services.registration_email.boto3')
    def test_ics_uid_and_sequence_match_attendee_invite(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        event = _make_event(host_email='host@test.com', ics_sequence=2)

        maybe_send_initial_host_invite(event)

        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        host_ics, method = _ics_from_raw(raw)
        self.assertEqual(method, 'REQUEST')

        host_cal = Calendar.from_ical(host_ics)
        host_vevent = next(c for c in host_cal.walk() if c.name == 'VEVENT')

        attendee_cal = Calendar.from_ical(generate_ics(event))
        attendee_vevent = next(
            c for c in attendee_cal.walk() if c.name == 'VEVENT'
        )

        # Same UID + SEQUENCE => one de-duped calendar entry across the host
        # and attendee invites for this event.
        self.assertEqual(
            str(host_vevent.get('uid')),
            str(attendee_vevent.get('uid')),
        )
        self.assertEqual(
            str(host_vevent.get('uid')),
            'event-ai-shipping-workshop@aishippinglabs.com',
        )
        self.assertEqual(
            int(host_vevent.get('sequence')),
            int(attendee_vevent.get('sequence')),
        )
        self.assertEqual(int(host_vevent.get('sequence')), 2)


@override_settings(SES_ENABLED=True)
@tag('core')
class SendHostRescheduleInviteTest(TestCase):
    @patch('events.services.registration_email.boto3')
    def test_reschedule_invite_carries_current_sequence(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        event = _make_event(host_email='host@test.com', ics_sequence=0)

        # Simulate the Studio reschedule path bumping the sequence first.
        event.ics_sequence = 5
        event.save(update_fields=['ics_sequence'])

        log = send_host_reschedule_invite(event)

        self.assertIsNotNone(log)
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        host_ics, _ = _ics_from_raw(raw)
        cal = Calendar.from_ical(host_ics)
        vevent = next(c for c in cal.walk() if c.name == 'VEVENT')
        self.assertEqual(int(vevent.get('sequence')), 5)
