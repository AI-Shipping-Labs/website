"""Raw MIME regression tests for event calendar lifecycle emails (#1073)."""

import email as email_lib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from icalendar import Calendar

from email_app.models import EmailLog
from events.models import Event, EventRegistration
from events.services.registration_email import send_registration_confirmation
from events.tasks.notify_cancellation import send_cancellation_notice_one
from events.tasks.notify_reschedule import send_reschedule_notice_one

User = get_user_model()


def _calendar_from_raw(raw):
    msg = email_lib.message_from_string(raw)
    calendar_parts = [
        part for part in msg.walk()
        if part.get_content_type() == 'text/calendar'
    ]
    if len(calendar_parts) != 1:
        raise AssertionError(f'expected one text/calendar part, got {len(calendar_parts)}')
    part = calendar_parts[0]
    payload = part.get_payload(decode=True).decode('utf-8')
    return part, Calendar.from_ical(payload)


def _vevent(cal):
    vevents = [component for component in cal.walk() if component.name == 'VEVENT']
    if len(vevents) != 1:
        raise AssertionError(f'expected one VEVENT, got {len(vevents)}')
    return vevents[0]


def _attendee_text(vevent):
    attendee = vevent.get('attendee')
    if isinstance(attendee, list):
        attendee = attendee[0]
    return str(attendee)


@tag('core')
@override_settings(SES_ENABLED=True, SITE_BASE_URL='https://aishippinglabs.com')
class SingleEventCalendarEmailLifecycleTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='attendee1073@test.com',
            preferred_timezone='UTC',
        )
        self.start = datetime(2026, 7, 10, 16, 0, tzinfo=UTC)
        self.event = Event.objects.create(
            title='Calendar Lifecycle',
            slug='calendar-lifecycle-1073',
            description='Private join details stay gated.',
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=1),
            status='upcoming',
            origin='studio',
            zoom_join_url='https://zoom.us/j/raw-secret',
            ics_sequence=0,
        )
        self.registration = EventRegistration.objects.create(
            event=self.event,
            user=self.user,
        )

    def _last_raw(self, mock_boto3):
        return mock_boto3.client.return_value.send_email.call_args.kwargs[
            'Content'
        ]['Raw']['Data']

    @patch('events.services.registration_email.boto3')
    def test_registration_email_raw_mime_is_request_with_attendee(
        self, mock_boto3,
    ):
        mock_boto3.client.return_value.send_email.return_value = {
            'MessageId': 'ses-registration',
        }

        send_registration_confirmation(self.registration)

        part, cal = _calendar_from_raw(self._last_raw(mock_boto3))
        self.assertEqual(part.get_param('method'), 'REQUEST')
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        vevent = _vevent(cal)
        self.assertEqual(
            str(vevent.get('uid')),
            'event-calendar-lifecycle-1073@aishippinglabs.com',
        )
        self.assertEqual(int(vevent.get('sequence')), 0)
        self.assertIn('mailto:noreply@aishippinglabs.com', str(vevent.get('organizer')))
        self.assertEqual(_attendee_text(vevent), 'mailto:attendee1073@test.com')
        join_url = 'https://aishippinglabs.com/events/calendar-lifecycle-1073/join'
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertIn(f'Join: {join_url}', str(vevent.get('description')))
        self.assertNotIn('zoom.us/j/raw-secret', str(vevent.to_ical()))

    @patch('events.services.registration_email.boto3')
    def test_reschedule_email_raw_mime_updates_same_uid_with_higher_sequence(
        self, mock_boto3,
    ):
        mock_boto3.client.return_value.send_email.return_value = {
            'MessageId': 'ses-update',
        }
        self.event.start_datetime = self.start + timedelta(days=1)
        self.event.end_datetime = self.start + timedelta(days=1, hours=2)
        self.event.ics_sequence = 1
        self.event.save(update_fields=['start_datetime', 'end_datetime', 'ics_sequence'])

        result = send_reschedule_notice_one(
            self.event.pk,
            self.user.pk,
            self.start.isoformat(),
        )

        self.assertEqual(result['status'], 'sent')
        part, cal = _calendar_from_raw(self._last_raw(mock_boto3))
        self.assertEqual(part.get_param('method'), 'REQUEST')
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        vevent = _vevent(cal)
        self.assertEqual(
            str(vevent.get('uid')),
            'event-calendar-lifecycle-1073@aishippinglabs.com',
        )
        self.assertEqual(_attendee_text(vevent), 'mailto:attendee1073@test.com')
        self.assertEqual(int(vevent.get('sequence')), self.event.ics_sequence)
        self.assertGreater(int(vevent.get('sequence')), 0)
        self.assertEqual(
            vevent.decoded('dtstart'),
            self.start + timedelta(days=1),
        )
        self.assertEqual(
            vevent.decoded('dtend'),
            self.start + timedelta(days=1, hours=2),
        )

    @patch('events.services.registration_email.boto3')
    def test_cancellation_email_raw_mime_is_cancel_with_status_cancelled(
        self, mock_boto3,
    ):
        mock_boto3.client.return_value.send_email.return_value = {
            'MessageId': 'ses-cancel',
        }
        self.event.status = 'cancelled'
        self.event.ics_sequence = 2
        self.event.save(update_fields=['status', 'ics_sequence'])

        result = send_cancellation_notice_one(self.event.pk, self.user.pk)

        self.assertEqual(result['status'], 'sent')
        part, cal = _calendar_from_raw(self._last_raw(mock_boto3))
        self.assertEqual(part.get_param('method'), 'CANCEL')
        self.assertEqual(str(cal.get('method')), 'CANCEL')
        vevent = _vevent(cal)
        self.assertEqual(
            str(vevent.get('uid')),
            'event-calendar-lifecycle-1073@aishippinglabs.com',
        )
        self.assertEqual(_attendee_text(vevent), 'mailto:attendee1073@test.com')
        self.assertEqual(int(vevent.get('sequence')), 2)
        self.assertEqual(str(vevent.get('status')), 'CANCELLED')

    @patch('events.services.registration_email.boto3')
    def test_unsubscribed_attendee_still_receives_transactional_update(
        self, mock_boto3,
    ):
        mock_boto3.client.return_value.send_email.return_value = {
            'MessageId': 'ses-unsub',
        }
        self.user.unsubscribed = True
        self.user.save(update_fields=['unsubscribed'])
        self.event.ics_sequence = 1
        self.event.save(update_fields=['ics_sequence'])

        result = send_reschedule_notice_one(
            self.event.pk,
            self.user.pk,
            (timezone.now() + timedelta(days=1)).isoformat(),
        )

        self.assertEqual(result['status'], 'sent')
        self.assertEqual(mock_boto3.client.return_value.send_email.call_count, 1)

    @patch('events.services.registration_email.boto3')
    def test_cancelled_registration_is_skipped_before_cancellation_send(
        self, mock_boto3,
    ):
        self.registration.delete()

        result = send_cancellation_notice_one(self.event.pk, self.user.pk)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'registration_cancelled')
        mock_boto3.client.return_value.send_email.assert_not_called()
        self.assertFalse(
            EmailLog.objects.filter(
                user=self.user,
                email_type='event_cancelled',
            ).exists(),
        )
