"""Tests for the reschedule-notice background tasks (issue #670).

Coverage:
- ``enqueue_reschedule_notice`` defers via ``jobs.tasks.async_task``.
- ``send_reschedule_notice_fanout`` enqueues one stage-2 task per
  registration (no N+1 over the user FK).
- ``send_reschedule_notice_one`` skips unsubscribed users, writes one
  ``EmailLog`` row per successful send, renders both OLD and NEW times
  in the recipient's timezone (or UTC fallback), and the regenerated
  ``.ics`` carries ``METHOD:REQUEST`` with a higher SEQUENCE than the
  original registration invite.
"""

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
    send_reschedule_notice_one,
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
        # First positional must be the fan-out function's dotted path,
        # so a worker process can resolve it without importing the
        # caller.
        self.assertEqual(
            args[0],
            'events.tasks.notify_reschedule.send_reschedule_notice_fanout',
        )
        self.assertEqual(args[1], 42)
        self.assertEqual(args[2], '2026-06-08T16:00:00+00:00')


class FanoutTest(TestCase):
    """The fan-out enqueues exactly one per-user task per registration."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Reschedule Fan-out',
            slug='reschedule-fanout',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='upcoming',
        )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_fanout_enqueues_one_per_registration(self, mock_q):
        mock_q.return_value = 'task-id'
        for i in range(3):
            user = User.objects.create_user(email=f'fan{i}@test.com')
            EventRegistration.objects.create(event=self.event, user=user)

        send_reschedule_notice_fanout(
            self.event.pk, '2026-06-01T16:00:00+00:00',
        )

        # Exactly 3 stage-2 tasks; one per registration.
        self.assertEqual(mock_q.call_count, 3)
        for call in mock_q.call_args_list:
            args = call.args
            self.assertEqual(
                args[0],
                'events.tasks.notify_reschedule.send_reschedule_notice_one',
            )
            self.assertEqual(args[1], self.event.pk)
            # args[3] is the iso old-start string.
            self.assertEqual(args[3], '2026-06-01T16:00:00+00:00')

    @patch('jobs.tasks.helpers.q_async_task')
    def test_fanout_zero_registrations_enqueues_nothing(self, mock_q):
        mock_q.return_value = 'task-id'
        send_reschedule_notice_fanout(
            self.event.pk, '2026-06-01T16:00:00+00:00',
        )
        self.assertEqual(mock_q.call_count, 0)

    @patch('jobs.tasks.helpers.q_async_task')
    def test_fanout_avoids_n_plus_1_user_query(self, mock_q):
        """Issue #670 AC: large fan-outs must use select_related('user')
        so the per-registration loop does not issue N user lookups."""
        mock_q.return_value = 'task-id'
        users = [
            User.objects.create_user(email=f'bulk{i}@test.com')
            for i in range(10)
        ]
        for user in users:
            EventRegistration.objects.create(event=self.event, user=user)

        # The fan-out should do at most a small constant number of
        # queries (one for the event, one for the registrations with
        # JOIN over user). Bound at 5 to leave headroom for Django
        # session / auth queries that may sneak in.
        with self.assertNumQueries(2):
            send_reschedule_notice_fanout(
                self.event.pk, '2026-06-01T16:00:00+00:00',
            )

    @patch('jobs.tasks.helpers.q_async_task')
    def test_fanout_missing_event_returns_skipped(self, mock_q):
        mock_q.return_value = 'task-id'
        result = send_reschedule_notice_fanout(
            999_999, '2026-06-01T16:00:00+00:00',
        )
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_event')
        self.assertEqual(mock_q.call_count, 0)


class SendRescheduleNoticeOneTest(TestCase):
    """Stage-2 per-user send: template rendering, EmailLog, .ics."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Live Q&A',
            slug='live-qa',
            description='An event about live Q&A.',
            # New start: 2026-06-15 16:00 UTC (CEST is UTC+02 in June).
            start_datetime=datetime(2026, 6, 15, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 15, 17, 0, tzinfo=UTC),
            status='upcoming',
            timezone='Europe/Berlin',
            ics_sequence=1,  # original invite went out at SEQUENCE=0
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

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-1')
    def test_send_renders_both_times_in_berlin(self, mock_send):
        """Berlin user sees BOTH old and new times in Europe/Berlin."""
        result = send_reschedule_notice_one(
            self.event.pk,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )

        self.assertEqual(result['status'], 'sent')
        html = mock_send.call_args.kwargs['html_body']
        # Old time (2026-06-08 16:00 UTC) in Europe/Berlin during CEST
        # = 18:00 local.
        self.assertIn('June 08, 2026, 18:00 Europe/Berlin', html)
        # New time (2026-06-15 16:00 UTC) in CEST = 18:00 local.
        self.assertIn('June 15, 2026, 18:00 Europe/Berlin', html)
        # No raw UTC label should leak when a valid TZ is set.
        self.assertNotIn('16:00 UTC', html)

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-2')
    def test_send_falls_back_to_utc_when_preference_unset(self, mock_send):
        """Unset preferred_timezone renders both times in UTC."""
        send_reschedule_notice_one(
            self.event.pk,
            self.no_tz_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        html = mock_send.call_args.kwargs['html_body']
        self.assertIn('June 08, 2026, 16:00 UTC', html)
        self.assertIn('June 15, 2026, 16:00 UTC', html)
        # Catches the bug where a per-user re-render forgets to rebuild
        # the context and a previous recipient's TZ leaks through.
        self.assertNotIn('Europe/Berlin', html)

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-3')
    def test_send_writes_email_log(self, mock_send):
        before = EmailLog.objects.filter(email_type='event_rescheduled').count()
        send_reschedule_notice_one(
            self.event.pk,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        after = EmailLog.objects.filter(email_type='event_rescheduled').count()
        self.assertEqual(after - before, 1)
        log = EmailLog.objects.filter(
            email_type='event_rescheduled', user=self.berlin_user,
        ).get()
        self.assertEqual(log.ses_message_id, 'ses-3')

    @patch('events.tasks.notify_reschedule._send_raw_email')
    def test_send_skips_unsubscribed_user(self, mock_send):
        result = send_reschedule_notice_one(
            self.event.pk,
            self.unsub_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'unsubscribed')
        mock_send.assert_not_called()
        self.assertFalse(
            EmailLog.objects.filter(
                email_type='event_rescheduled', user=self.unsub_user,
            ).exists(),
        )

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-4')
    def test_send_passes_method_request_to_raw_email(self, mock_send):
        """The per-user send re-issues the .ics as METHOD:REQUEST."""
        send_reschedule_notice_one(
            self.event.pk,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        self.assertEqual(mock_send.call_args.kwargs['method'], 'REQUEST')
        ics_bytes = mock_send.call_args.kwargs['ics_content']
        cal = _parse_ics(ics_bytes)
        self.assertEqual(str(cal.get('method')), 'REQUEST')

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-5')
    def test_resent_ics_sequence_strictly_greater_than_original(self, mock_send):
        """SEQUENCE bump means calendar clients overwrite the original."""
        # Original registration invite would have used SEQUENCE=0 (the
        # default when the event was created). The reschedule sends a
        # new invite with the post-bump value (1 in setUpTestData).
        original_ics = generate_ics(
            Event.objects.create(
                title='Original',
                slug='original-seq',
                start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
                ics_sequence=0,
            ),
        )
        original_seq = int(
            [c for c in _parse_ics(original_ics).walk() if c.name == 'VEVENT'][0]
            .get('sequence')
        )

        send_reschedule_notice_one(
            self.event.pk,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        ics_bytes = mock_send.call_args.kwargs['ics_content']
        cal = _parse_ics(ics_bytes)
        new_seq = int(
            [c for c in cal.walk() if c.name == 'VEVENT'][0].get('sequence')
        )

        self.assertGreater(new_seq, original_seq)

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-6')
    def test_resent_ics_uses_attendee_join_url_with_bumped_sequence(
        self, mock_send,
    ):
        send_reschedule_notice_one(
            self.event.pk,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )

        ics_bytes = mock_send.call_args.kwargs['ics_content']
        cal = _parse_ics(ics_bytes)
        vevent = [c for c in cal.walk() if c.name == 'VEVENT'][0]
        join_url = 'https://aishippinglabs.com/events/live-qa/join'

        self.assertEqual(int(vevent.get('sequence')), 1)
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertIn(f'Join: {join_url}', str(vevent.get('description')))
        self.assertNotIn(self.event.get_absolute_url(), str(vevent.get('url')))

    @patch('events.tasks.notify_reschedule._send_raw_email')
    def test_send_skips_cancelled_registration(self, mock_send):
        """A user who cancelled between fan-out and send is not emailed."""
        EventRegistration.objects.filter(
            event=self.event, user=self.berlin_user,
        ).delete()
        result = send_reschedule_notice_one(
            self.event.pk,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'registration_cancelled')
        mock_send.assert_not_called()

    @patch('events.tasks.notify_reschedule._send_raw_email')
    def test_send_missing_event_returns_skipped(self, mock_send):
        result = send_reschedule_notice_one(
            999_999,
            self.berlin_user.pk,
            '2026-06-08T16:00:00+00:00',
        )
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_event')
        mock_send.assert_not_called()

    @patch('events.tasks.notify_reschedule._send_raw_email')
    def test_send_missing_user_returns_skipped(self, mock_send):
        result = send_reschedule_notice_one(
            self.event.pk,
            999_999,
            '2026-06-08T16:00:00+00:00',
        )
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_user')
        mock_send.assert_not_called()


class SeriesSubscriberDedupTest(TestCase):
    """Issue #869 de-dup contract: a user enrolled in an occurrence BOTH
    via a standing ``SeriesRegistration`` AND an ``EventRegistration`` must
    receive exactly ONE reschedule email — the canonical multi-event series
    update — not a duplicate per-event reschedule notice.

    The per-event notice (``send_reschedule_notice_one``) therefore skips a
    recipient who has a ``SeriesRegistration`` for the occurrence's series,
    and still sends to a one-off registrant who is not a series subscriber.

    If the skip branch were removed, the dual-enrolled user in case (a)
    would receive the per-event notice (status ``sent``, an EmailLog row,
    a raw send) and the assertions below would fail — no false positive.
    """

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
        # (a) Dual-enrolled: standing series subscription AND a concrete
        #     per-occurrence registration.
        cls.series_subscriber = User.objects.create_user(
            email='series-sub@test.com',
            preferred_timezone='UTC',
        )
        SeriesRegistration.objects.create(
            series=cls.series, user=cls.series_subscriber,
        )
        # (b) One-off registrant of the same occurrence with NO series
        #     subscription.
        cls.one_off = User.objects.create_user(
            email='one-off@test.com',
            preferred_timezone='UTC',
        )

    def setUp(self):
        EventRegistration.objects.create(
            event=self.event, user=self.series_subscriber,
        )
        EventRegistration.objects.create(event=self.event, user=self.one_off)

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-x')
    def test_series_subscriber_skipped(self, mock_send):
        """(a) Dual-enrolled subscriber is skipped with reason
        ``series_subscriber`` — no per-event email, no EmailLog row."""
        result = send_reschedule_notice_one(
            self.event.pk,
            self.series_subscriber.pk,
            '2026-06-08T16:00:00+00:00',
        )

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'series_subscriber')
        mock_send.assert_not_called()
        self.assertFalse(
            EmailLog.objects.filter(
                email_type='event_rescheduled', user=self.series_subscriber,
            ).exists(),
        )

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-y')
    def test_one_off_registrant_still_notified(self, mock_send):
        """(b) A one-off registrant without a SeriesRegistration still gets
        the per-event reschedule notice."""
        result = send_reschedule_notice_one(
            self.event.pk,
            self.one_off.pk,
            '2026-06-08T16:00:00+00:00',
        )

        self.assertEqual(result['status'], 'sent')
        mock_send.assert_called_once()
        self.assertEqual(
            mock_send.call_args.kwargs['to_email'], 'one-off@test.com',
        )
        self.assertTrue(
            EmailLog.objects.filter(
                email_type='event_rescheduled', user=self.one_off,
            ).exists(),
        )
