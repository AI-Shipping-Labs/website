"""Tests for the time-derived ``Event.is_upcoming`` / ``Event.is_past``
properties introduced in issue #713.

The model previously read ``status`` directly for both flags. Now both
properties derive from ``(status, start_datetime, end_datetime, now)``.
The cron still flips stored ``status`` once a day, but the UI no longer
depends on that flip.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event


def _make_event(slug, *, status='upcoming', start_offset=None, end_offset=None):
    now = timezone.now()
    return Event.objects.create(
        slug=slug,
        title=f'Event {slug}',
        start_datetime=now + (start_offset or timedelta(hours=1)),
        end_datetime=(now + end_offset) if end_offset is not None else None,
        status=status,
    )


class EffectiveEndDatetimeTest(TestCase):
    """``effective_end_datetime`` mirrors the cron + ICS fallback."""

    def test_returns_end_datetime_when_set(self):
        event = _make_event(
            'with-end',
            start_offset=timedelta(hours=1),
            end_offset=timedelta(hours=2),
        )
        expected = event.start_datetime + timedelta(hours=1)
        self.assertEqual(event.effective_end_datetime, expected)
        self.assertEqual(event.effective_end_datetime, event.end_datetime)

    def test_falls_back_to_start_plus_one_hour_when_end_missing(self):
        event = _make_event(
            'no-end',
            start_offset=timedelta(hours=1),
            end_offset=None,
        )
        expected = event.start_datetime + timedelta(hours=1)
        self.assertEqual(event.effective_end_datetime, expected)


class IsUpcomingTruthTableTest(TestCase):
    """6-row truth table covering stored status x time relation."""

    def test_upcoming_status_future_end_is_upcoming(self):
        event = _make_event(
            'upcoming-future',
            status='upcoming',
            start_offset=timedelta(hours=-1),
            end_offset=timedelta(hours=1),
        )
        self.assertTrue(event.is_upcoming)
        self.assertFalse(event.is_past)

    def test_upcoming_status_past_end_is_NOT_upcoming(self):
        """The stale-status-but-time-elapsed case the issue is about."""
        event = _make_event(
            'stale-upcoming',
            status='upcoming',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )
        self.assertFalse(event.is_upcoming)
        self.assertTrue(event.is_past)

    def test_completed_status_future_end_is_upcoming(self):
        """Legacy ``completed`` with a future end: time wins."""
        event = _make_event(
            'legacy-completed-future',
            status='completed',
            start_offset=timedelta(hours=-1),
            end_offset=timedelta(hours=1),
        )
        self.assertTrue(event.is_upcoming)
        self.assertFalse(event.is_past)

    def test_completed_status_past_end_is_past(self):
        event = _make_event(
            'completed-past',
            status='completed',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )
        self.assertFalse(event.is_upcoming)
        self.assertTrue(event.is_past)

    def test_cancelled_status_future_end_is_past(self):
        """Cancelled wins over time."""
        event = _make_event(
            'cancelled-future',
            status='cancelled',
            start_offset=timedelta(hours=2),
            end_offset=timedelta(hours=3),
        )
        self.assertFalse(event.is_upcoming)
        self.assertTrue(event.is_past)

    def test_cancelled_status_past_end_is_past(self):
        event = _make_event(
            'cancelled-past',
            status='cancelled',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )
        self.assertFalse(event.is_upcoming)
        self.assertTrue(event.is_past)


class DraftIsNeitherTest(TestCase):
    """Drafts are not in any visitor-facing state."""

    def test_draft_future_end_is_neither(self):
        event = _make_event(
            'draft-future',
            status='draft',
            start_offset=timedelta(hours=1),
            end_offset=timedelta(hours=2),
        )
        self.assertFalse(event.is_upcoming)
        self.assertFalse(event.is_past)

    def test_draft_past_end_is_neither(self):
        event = _make_event(
            'draft-past',
            status='draft',
            start_offset=timedelta(hours=-3),
            end_offset=timedelta(hours=-1),
        )
        self.assertFalse(event.is_upcoming)
        self.assertFalse(event.is_past)


class ImplicitEndTest(TestCase):
    """Events without ``end_datetime`` use the ``start + 1h`` fallback."""

    def test_implicit_end_in_past_is_past(self):
        event = _make_event(
            'flash-qa-ended',
            status='upcoming',
            start_offset=timedelta(minutes=-75),
            end_offset=None,
        )
        self.assertFalse(event.is_upcoming)
        self.assertTrue(event.is_past)

    def test_implicit_end_in_future_is_upcoming(self):
        event = _make_event(
            'flash-qa-live',
            status='upcoming',
            start_offset=timedelta(minutes=-10),
            end_offset=None,
        )
        self.assertTrue(event.is_upcoming)
        self.assertFalse(event.is_past)
