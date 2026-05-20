"""Cron integration tests for the post-event follow-up (issue #680).

``complete_finished_events`` is extended to enqueue the post-event
follow-up fan-out for each event it just flipped to ``completed``,
provided:

1. The event has a recording URL set (``recording_url`` or
   ``recording_s3_url`` non-empty).
2. No ``EventReminderLog(interval='followup')`` row already exists
   for the event.

These tests verify the cron-level gate (the per-user gate has its
own coverage in ``test_send_post_event_followup``).
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventRegistration
from events.tasks.complete_finished_events import complete_finished_events
from notifications.models import EventReminderLog

User = get_user_model()


def _make_event(slug, **kwargs):
    now = timezone.now()
    defaults = {
        'title': f'Event {slug}',
        'start_datetime': now - timedelta(hours=3),
        'end_datetime': now - timedelta(hours=1),
        'status': 'upcoming',
    }
    defaults.update(kwargs)
    return Event.objects.create(slug=slug, **defaults)


class CronEnqueuesFollowupWhenRecordingSetTest(TestCase):
    """Happy path: a just-flipped event with a recording URL enqueues."""

    @patch(
        'events.tasks.complete_finished_events.enqueue_post_event_followup',
    )
    def test_enqueues_followup_for_event_with_recording(self, mock_enq):
        event = _make_event(
            'flips-with-recording',
            recording_url='https://youtube.com/watch?v=abc',
        )
        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')
        self.assertEqual(flipped, 1)
        mock_enq.assert_called_once_with(event.pk)


class CronSkipsWhenNoRecordingTest(TestCase):
    """An event that flips with no recording URL does not enqueue."""

    @patch(
        'events.tasks.complete_finished_events.enqueue_post_event_followup',
    )
    def test_no_recording_no_enqueue(self, mock_enq):
        event = _make_event('no-recording')
        flipped = complete_finished_events()

        event.refresh_from_db()
        self.assertEqual(event.status, 'completed')
        self.assertEqual(flipped, 1)
        mock_enq.assert_not_called()
        # And no log rows are persisted (the per-user gate is the
        # row writer; the cron does not pre-populate them).
        self.assertFalse(
            EventReminderLog.objects.filter(
                event=event, interval='followup',
            ).exists(),
        )


class CronSkipsWhenAlreadySentTest(TestCase):
    """A second cron tick is a no-op when followup rows already exist."""

    @patch(
        'events.tasks.complete_finished_events.enqueue_post_event_followup',
    )
    def test_existing_followup_row_blocks_enqueue(self, mock_enq):
        event = _make_event(
            'already-sent',
            recording_url='https://youtube.com/watch?v=already',
        )
        user = User.objects.create_user(email='already@test.com')
        EventRegistration.objects.create(event=event, user=user)
        # Simulate a previous fan-out write.
        EventReminderLog.objects.create(
            event=event, user=user, interval='followup',
        )

        complete_finished_events()
        mock_enq.assert_not_called()


class CronRerunIsIdempotentTest(TestCase):
    """A second cron tick after the first does not double-enqueue.

    The cron only inspects rows whose status is ``upcoming``; once a
    row is ``completed`` the second pass filters it out before the
    enqueue step. The dedup row would also gate, but the upstream
    filter is the primary protection.
    """

    @patch(
        'events.tasks.complete_finished_events.enqueue_post_event_followup',
    )
    def test_second_tick_no_op(self, mock_enq):
        _make_event(
            'tick-once',
            recording_url='https://youtube.com/watch?v=tick-once',
        )

        complete_finished_events()
        complete_finished_events()

        self.assertEqual(mock_enq.call_count, 1)


class CronMixedBatchTest(TestCase):
    """The cron picks only the followup-eligible subset from a mixed batch."""

    @patch(
        'events.tasks.complete_finished_events.enqueue_post_event_followup',
    )
    def test_only_recording_events_get_enqueued(self, mock_enq):
        with_recording = _make_event(
            'mixed-with-rec',
            recording_url='https://youtube.com/watch?v=mixed-rec',
        )
        _make_event('mixed-no-rec')  # No recording — should not enqueue.
        # An event that is not 'upcoming' must not be picked up at all.
        _make_event(
            'mixed-draft',
            status='draft',
            recording_url='https://youtube.com/watch?v=mixed-draft',
        )

        complete_finished_events()

        mock_enq.assert_called_once_with(with_recording.pk)


class CronS3RecordingFieldTest(TestCase):
    """``recording_s3_url`` alone (without ``recording_url``) is enough."""

    @patch(
        'events.tasks.complete_finished_events.enqueue_post_event_followup',
    )
    def test_s3_only_is_eligible(self, mock_enq):
        event = _make_event(
            's3-only',
            recording_s3_url='https://cdn.example.test/event-recording.mp4',
        )

        complete_finished_events()
        mock_enq.assert_called_once_with(event.pk)
