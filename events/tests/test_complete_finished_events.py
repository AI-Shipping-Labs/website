"""Tests for the `complete_finished_events` periodic task (issue #573).

Verifies the cron job that auto-transitions `Event.status` from
`upcoming` to `completed` once an event's effective end time is in the
past. Effective end is `end_datetime` when set, else
`start_datetime + 1 hour` (mirrors `events/services/calendar_invite.py`).
"""

import logging
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event
from events.tasks.complete_finished_events import complete_finished_events


def _make_event(slug, *, status, start_offset, end_offset=None):
    """Create an event whose start (and optional end) are offsets from now.

    Args:
        slug: unique slug.
        status: initial status (e.g. 'upcoming', 'draft', 'cancelled').
        start_offset: timedelta added to `now` for `start_datetime`.
        end_offset: timedelta added to `now` for `end_datetime`, or None
            to leave `end_datetime` unset.
    """
    now = timezone.now()
    return Event.objects.create(
        slug=slug,
        title=f'Event {slug}',
        start_datetime=now + start_offset,
        end_datetime=(now + end_offset) if end_offset is not None else None,
        status=status,
    )


class CompleteFinishedEventsExplicitEndTest(TestCase):
    """Events with `end_datetime` set."""

    def test_flips_event_with_past_end_datetime(self):
        event = _make_event(
            'past-with-end',
            status='upcoming',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')
        self.assertEqual(flipped, 1)

    def test_does_not_flip_event_with_future_end_datetime(self):
        event = _make_event(
            'future-with-end',
            status='upcoming',
            start_offset=timedelta(hours=-1),
            end_offset=timedelta(hours=1),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(flipped, 0)


class CompleteFinishedEventsImplicitEndTest(TestCase):
    """Events without `end_datetime` use a `start + 1h` default."""

    def test_flips_event_started_over_one_hour_ago(self):
        event = _make_event(
            'flash-qa',
            status='upcoming',
            start_offset=timedelta(minutes=-75),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')
        self.assertEqual(flipped, 1)

    def test_does_not_flip_event_started_less_than_one_hour_ago(self):
        event = _make_event(
            'live-now',
            status='upcoming',
            start_offset=timedelta(minutes=-10),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(flipped, 0)

    def test_does_not_flip_future_event_without_end_datetime(self):
        event = _make_event(
            'next-week',
            status='upcoming',
            start_offset=timedelta(days=7),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'upcoming')
        self.assertEqual(flipped, 0)


class CompleteFinishedEventsStatusFilterTest(TestCase):
    """The job must only touch `upcoming` rows."""

    def test_does_not_touch_draft_event_in_past(self):
        event = _make_event(
            'draft-past',
            status='draft',
            start_offset=timedelta(hours=-5),
            end_offset=timedelta(hours=-3),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'draft')
        self.assertEqual(flipped, 0)

    def test_does_not_touch_completed_event(self):
        event = _make_event(
            'already-done',
            status='completed',
            start_offset=timedelta(hours=-5),
            end_offset=timedelta(hours=-3),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')
        self.assertEqual(flipped, 0)

    def test_does_not_touch_cancelled_event(self):
        event = _make_event(
            'was-cancelled',
            status='cancelled',
            start_offset=timedelta(days=-3),
        )

        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'cancelled')
        self.assertEqual(flipped, 0)


class CompleteFinishedEventsIdempotencyTest(TestCase):
    """Second invocation is a no-op once rows have been flipped."""

    def test_second_run_returns_zero(self):
        _make_event(
            'flip-once',
            status='upcoming',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )

        first = complete_finished_events()
        second = complete_finished_events()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)


class CompleteFinishedEventsLoggingTest(TestCase):
    """Job logs only when count > 0."""

    def test_logs_info_when_rows_flipped(self):
        _make_event(
            'logged-flip',
            status='upcoming',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )

        with self.assertLogs(
            'events.tasks.complete_finished_events', level='INFO',
        ) as cm:
            complete_finished_events()

        self.assertTrue(
            any('Flipped 1' in msg for msg in cm.output),
            f'expected flipped-count log line, got {cm.output}',
        )

    def test_silent_when_nothing_to_flip(self):
        _make_event(
            'future-event',
            status='upcoming',
            start_offset=timedelta(days=7),
        )

        logger = logging.getLogger('events.tasks.complete_finished_events')
        records = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _CaptureHandler(level=logging.INFO)
        logger.addHandler(handler)
        try:
            complete_finished_events()
        finally:
            logger.removeHandler(handler)

        self.assertEqual(records, [])


class CompleteFinishedEventsMixedBatchTest(TestCase):
    """Single invocation processes both explicit-end and implicit-end rows."""

    def test_flips_both_branches_in_one_call(self):
        _make_event(
            'explicit-past',
            status='upcoming',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )
        _make_event(
            'implicit-past',
            status='upcoming',
            start_offset=timedelta(minutes=-75),
        )
        _make_event(
            'explicit-future',
            status='upcoming',
            start_offset=timedelta(hours=-1),
            end_offset=timedelta(hours=1),
        )
        _make_event(
            'implicit-live',
            status='upcoming',
            start_offset=timedelta(minutes=-10),
        )

        flipped = complete_finished_events()

        self.assertEqual(flipped, 2)
        self.assertEqual(
            Event.objects.get(slug='explicit-past').status, 'completed',
        )
        self.assertEqual(
            Event.objects.get(slug='implicit-past').status, 'completed',
        )
        self.assertEqual(
            Event.objects.get(slug='explicit-future').status, 'upcoming',
        )
        self.assertEqual(
            Event.objects.get(slug='implicit-live').status, 'upcoming',
        )
