"""Tests for the reschedule-notice background task (one Relay campaign submit)."""

from datetime import UTC, datetime, time
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from icalendar import Calendar

from email_app.models import EmailLog
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from events.services.calendar_invite import generate_ics
from events.tasks.notify_reschedule import (
    enqueue_reschedule_notice,
    send_reschedule_notice_fanout,
)

User = get_user_model()


def _parse_ics(ics_bytes):
    return Calendar.from_ical(ics_bytes)


class EnqueueRescheduleNoticeTest(TestCase):
    """The Studio-facing enqueue helper defers via async_task."""

    @patch('jobs.tasks.helpers.q_async_task')
    def test_enqueue_calls_async_task_with_dotted_path(self, mock_q):
        mock_q.return_value = 'task-id'
        enqueue_reschedule_notice(42, '2026-06-08T16:00:00+00:00')

        self.assertEqual(mock_q.call_count, 1)
        args = mock_q.call_args.args
        self.assertEqual(
            args[0],
            'events.tasks.notify_reschedule.send_reschedule_notice_fanout',
        )
        self.assertEqual(args[1], 42)
        self.assertEqual(args[2], '2026-06-08T16:00:00+00:00')


class RescheduleCampaignSubmitTest(TestCase):
    """send_reschedule_notice_fanout submits ONE Relay campaign with per-recipient context + .ics."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Live Q&A',
            slug='live-qa',
            description='An event about live Q&A.',
            start_datetime=datetime(2026, 6, 15, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 15, 17, 0, tzinfo=UTC),
            status='upcoming',
            timezone='Europe/Berlin',
            ics_sequence=1,
        )
        cls.berlin_user = User.objects.create_user(
            email='berlin@test.com',
            preferred_timezone='Europe/Berlin',
        )
        cls.no_tz_user = User.objects.create_user(
            email='no-tz@test.com',
            preferred_timezone='',
        )
        cls.unsub_user = User.objects.create_user(
            email='unsub@test.com',
            preferred_timezone='Europe/Berlin',
            unsubscribed=True,
        )

    def setUp(self):
        EventRegistration.objects.create(event=self.event, user=self.berlin_user)
        EventRegistration.objects.create(event=self.event, user=self.no_tz_user)
        EventRegistration.objects.create(event=self.event, user=self.unsub_user)

    @patch('events.tasks.notify_reschedule.submit_event_notice_campaign', return_value={'created': True})
    def test_submits_one_campaign_with_per_recipient_timezone_and_ics(self, mock_submit):
        result = send_reschedule_notice_fanout(
            self.event.pk, '2026-06-08T16:00:00+00:00',
        )

        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['count'], 3)
        self.assertEqual(mock_submit.call_count, 1)
        kwargs = mock_submit.call_args.kwargs
        self.assertEqual(kwargs['external_key'], f'event-rescheduled-{self.event.pk}-seq-1')
        self.assertEqual(kwargs['template_key'], 'event_rescheduled')

        by_email = {
            r['email']: r['metadata']['relay_campaign_context']
            for r in kwargs['recipients']
        }
        berlin_ctx = by_email['berlin@test.com']
        self.assertIn('June 08, 2026, 18:00 Europe/Berlin', berlin_ctx['old_event_datetime'])
        self.assertIn('June 15, 2026, 18:00 Europe/Berlin', berlin_ctx['new_event_datetime'])
        self.assertNotIn('16:00 UTC', str(berlin_ctx))

        utc_ctx = by_email['no-tz@test.com']
        self.assertIn('June 08, 2026, 16:00 UTC', utc_ctx['old_event_datetime'])
        self.assertIn('June 15, 2026, 16:00 UTC', utc_ctx['new_event_datetime'])
        self.assertNotIn('Europe/Berlin', str(utc_ctx))

        self.assertIn('unsub@test.com', by_email)
        cal_alt = berlin_ctx['calendar_alternative']
        self.assertEqual(cal_alt['method'], 'REQUEST')
        cal = _parse_ics(cal_alt['content'])
        self.assertEqual(str(cal.get('method')), 'REQUEST')
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]
        self.assertEqual(int(vevent.get('sequence')), 1)
        join_url = f'https://aishippinglabs.com{self.event.get_join_url()}'
        self.assertEqual(str(vevent.get('url')), join_url)

        self.assertEqual(
            EmailLog.objects.filter(event=self.event, email_type='event_rescheduled').count(),
            3,
        )

    @patch('events.tasks.notify_reschedule.submit_event_notice_campaign', return_value={'created': True})
    def test_rerun_is_deduplicated_by_email_log(self, mock_submit):
        send_reschedule_notice_fanout(self.event.pk, '2026-06-08T16:00:00+00:00')
        second = send_reschedule_notice_fanout(self.event.pk, '2026-06-08T16:00:00+00:00')

        self.assertEqual(second['status'], 'skipped')
        self.assertEqual(second['reason'], 'no_recipients')
        self.assertEqual(mock_submit.call_count, 1)

    @patch('events.tasks.notify_reschedule.submit_event_notice_campaign')
    def test_missing_event_returns_skipped(self, mock_submit):
        result = send_reschedule_notice_fanout(999_999, '2026-06-01T16:00:00+00:00')
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_event')
        mock_submit.assert_not_called()

    @patch('events.tasks.notify_reschedule.submit_event_notice_campaign', return_value={'created': True})
    def test_resent_ics_sequence_strictly_greater_than_original(self, mock_submit):
        original_ics = generate_ics(
            Event.objects.create(
                title='Original',
                slug='original-seq',
                start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
                ics_sequence=0,
            ),
        )
        original_seq = int(
            [c for c in _parse_ics(original_ics).walk() if c.name == 'VEVENT'][0].get('sequence')
        )
        send_reschedule_notice_fanout(self.event.pk, '2026-06-08T16:00:00+00:00')
        cal = _parse_ics(
            mock_submit.call_args.kwargs['recipients'][0]['metadata']['relay_campaign_context']['calendar_alternative']['content']
        )
        new_seq = int([c for c in cal.walk() if c.name == 'VEVENT'][0].get('sequence'))
        self.assertGreater(new_seq, original_seq)


class SeriesSubscriberDedupTest(TestCase):
    """Issue #869 de-dup contract: series subscribers are excluded from per-event reschedule notice."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Office Hours',
            slug='office-hours',
            start_time=time(16, 0),
            timezone='UTC',
        )
        cls.event = Event.objects.create(
            title='Office Hours #5',
            slug='office-hours-5',
            start_datetime=datetime(2026, 6, 15, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 15, 17, 0, tzinfo=UTC),
            status='upcoming',
            timezone='UTC',
            event_series=cls.series,
        )
        cls.series_subscriber = User.objects.create_user(
            email='series-sub@test.com',
            preferred_timezone='UTC',
        )
        SeriesRegistration.objects.create(series=cls.series, user=cls.series_subscriber)
        cls.one_off = User.objects.create_user(
            email='one-off@test.com',
            preferred_timezone='UTC',
        )

    def setUp(self):
        EventRegistration.objects.create(event=self.event, user=self.series_subscriber)
        EventRegistration.objects.create(event=self.event, user=self.one_off)

    @patch('events.tasks.notify_reschedule.submit_event_notice_campaign', return_value={'created': True})
    def test_series_subscriber_excluded_and_one_off_notified(self, mock_submit):
        result = send_reschedule_notice_fanout(
            self.event.pk, '2026-06-08T16:00:00+00:00',
        )

        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['count'], 1)
        recipients = mock_submit.call_args.kwargs['recipients']
        self.assertEqual([r['email'] for r in recipients], ['one-off@test.com'])
        self.assertFalse(
            EmailLog.objects.filter(email_type='event_rescheduled', user=self.series_subscriber).exists(),
        )
        self.assertTrue(
            EmailLog.objects.filter(email_type='event_rescheduled', user=self.one_off).exists(),
        )
