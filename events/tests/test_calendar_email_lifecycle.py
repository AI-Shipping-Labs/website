"""Raw MIME regression tests for event calendar lifecycle emails (#1073)."""

import email as email_lib
from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from icalendar import Calendar

from email_app.models import EmailLog
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
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
@override_settings(
    SES_ENABLED=True,
    SITE_BASE_URL='https://aishippinglabs.com',
    SES_TRANSACTIONAL_FROM_EMAIL=(
        'AI Shipping Labs <content@aishippinglabs.com>'
    ),
)
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

    def _assert_formatted_from_and_bare_organizer(self, raw, cal):
        msg = email_lib.message_from_string(raw)
        self.assertEqual(
            msg['From'],
            'AI Shipping Labs <content@aishippinglabs.com>',
        )
        self.assertEqual(
            str(_vevent(cal).get('organizer')),
            'mailto:content@aishippinglabs.com',
        )

    @patch('events.services.registration_email.boto3')
    def test_registration_email_raw_mime_is_request_with_attendee(
        self, mock_boto3,
    ):
        mock_boto3.client.return_value.send_email.return_value = {
            'MessageId': 'ses-registration',
        }

        send_registration_confirmation(self.registration)

        raw = self._last_raw(mock_boto3)
        part, cal = _calendar_from_raw(raw)
        self.assertEqual(part.get_param('method'), 'REQUEST')
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        vevent = _vevent(cal)
        self.assertEqual(
            str(vevent.get('uid')),
            'event-calendar-lifecycle-1073@aishippinglabs.com',
        )
        self.assertEqual(int(vevent.get('sequence')), 0)
        self._assert_formatted_from_and_bare_organizer(raw, cal)
        self.assertEqual(_attendee_text(vevent), 'mailto:attendee1073@test.com')
        join_url = f'https://aishippinglabs.com{self.event.get_join_url()}'
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
        raw = self._last_raw(mock_boto3)
        part, cal = _calendar_from_raw(raw)
        self.assertEqual(part.get_param('method'), 'REQUEST')
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        vevent = _vevent(cal)
        self._assert_formatted_from_and_bare_organizer(raw, cal)
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
        raw = self._last_raw(mock_boto3)
        part, cal = _calendar_from_raw(raw)
        self.assertEqual(part.get_param('method'), 'CANCEL')
        self.assertEqual(str(cal.get('method')), 'CANCEL')
        vevent = _vevent(cal)
        self._assert_formatted_from_and_bare_organizer(raw, cal)
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


@tag('core')
class CancellationSeriesSubscriberDedupTest(TestCase):
    """A cancellation has one canonical email path per recipient (#869)."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Cancellation de-dup series',
            slug='cancellation-dedup-series',
            start_time=time(16, 0),
            timezone='UTC',
        )
        cls.event = Event.objects.create(
            title='Cancelled series occurrence',
            slug='cancelled-series-occurrence',
            start_datetime=datetime(2026, 7, 20, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 7, 20, 17, 0, tzinfo=UTC),
            status='cancelled',
            event_series=cls.series,
            ics_sequence=1,
        )
        cls.series_subscriber = User.objects.create_user(
            email='series-cancel@test.com',
            preferred_timezone='UTC',
        )
        cls.one_off_registrant = User.objects.create_user(
            email='one-off-cancel@test.com',
            preferred_timezone='UTC',
        )
        SeriesRegistration.objects.create(
            series=cls.series,
            user=cls.series_subscriber,
        )
        EventRegistration.objects.create(
            event=cls.event,
            user=cls.series_subscriber,
        )
        EventRegistration.objects.create(
            event=cls.event,
            user=cls.one_off_registrant,
        )

    @patch(
        'events.tasks.notify_cancellation._send_raw_email',
        return_value='ses-one-off-cancel',
    )
    def test_series_subscriber_skips_standalone_send_but_one_off_receives_it(
        self, mock_send,
    ):
        subscriber_result = send_cancellation_notice_one(
            self.event.pk,
            self.series_subscriber.pk,
        )
        one_off_result = send_cancellation_notice_one(
            self.event.pk,
            self.one_off_registrant.pk,
        )

        self.assertEqual(subscriber_result['status'], 'skipped')
        self.assertEqual(subscriber_result['reason'], 'series_subscriber')
        self.assertEqual(one_off_result['status'], 'sent')
        mock_send.assert_called_once()
        self.assertEqual(
            mock_send.call_args.kwargs['to_email'],
            self.one_off_registrant.email,
        )

        cancellation_logs = EmailLog.objects.filter(
            email_type='event_cancelled',
        )
        self.assertEqual(cancellation_logs.count(), 1)
        cancellation_log = cancellation_logs.get()
        self.assertEqual(cancellation_log.user, self.one_off_registrant)
        self.assertEqual(cancellation_log.ses_message_id, 'ses-one-off-cancel')
        self.assertFalse(
            cancellation_logs.filter(user=self.series_subscriber).exists(),
        )
