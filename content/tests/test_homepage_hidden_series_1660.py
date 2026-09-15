"""Homepage/dashboard call sites inherit the hidden-series exclusion (#1660).

``events.services.time_windows.public_events_queryset`` (and therefore
``upcoming_events_queryset``) already excludes hidden-series occurrences —
covered directly in ``events/tests/test_hidden_series_1660.py``. This file
verifies the three homepage/dashboard call sites named in the spec actually
inherit that fix rather than re-implementing the exclusion.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.views.home import (
    _get_free_unlock_context,
    _get_homepage_public_upcoming_events,
)
from events.models import Event, EventSeries
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_hidden_series(**kwargs):
    kwargs.setdefault('cadence', 'none')
    kwargs.setdefault('day_of_week', None)
    kwargs.setdefault('start_time', None)
    kwargs.setdefault('visibility', 'hidden')
    return EventSeries.objects.create(**kwargs)


class HomepagePublicUpcomingEventsTest(TestCase):
    """``_get_homepage_public_upcoming_events`` (content/views/home.py)."""

    def test_excludes_hidden_series_occurrence(self):
        hidden_series = _make_hidden_series(
            name='Hidden Homepage Series', slug='hidden-homepage-series',
        )
        hidden_event = Event.objects.create(
            title='Hidden Homepage Event',
            slug='hidden-homepage-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            published=True,
            event_series=hidden_series,
        )
        public_event = Event.objects.create(
            title='Public Homepage Event',
            slug='public-homepage-event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
            published=True,
        )

        events = _get_homepage_public_upcoming_events()
        self.assertNotIn(hidden_event, events)
        self.assertIn(public_event, events)

    def test_anonymous_homepage_does_not_render_hidden_series_event(self):
        hidden_series = _make_hidden_series(
            name='Anon Hidden Series', slug='anon-hidden-series',
        )
        Event.objects.create(
            title='Anon Hidden Homepage Event',
            slug='anon-hidden-homepage-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            published=True,
            event_series=hidden_series,
        )

        response = self.client.get('/')
        self.assertNotContains(response, 'Anon Hidden Homepage Event')


class FreeUnlockLockedEventTest(TestCase):
    """``_get_free_unlock_context`` (dashboard free-tier locked-event teaser)."""

    def test_excludes_hidden_series_occurrence(self):
        from content.access import LEVEL_BASIC

        hidden_series = _make_hidden_series(
            name='Hidden Locked Series', slug='hidden-locked-series',
        )
        hidden_event = Event.objects.create(
            title='Hidden Locked Event',
            slug='hidden-locked-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            published=True,
            required_level=LEVEL_BASIC,
            event_series=hidden_series,
        )
        public_event = Event.objects.create(
            title='Public Locked Event',
            slug='public-locked-event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
            published=True,
            required_level=LEVEL_BASIC,
        )

        context = _get_free_unlock_context(enabled=True)
        self.assertNotEqual(context['free_unlock_event'], hidden_event)
        self.assertEqual(context['free_unlock_event'], public_event)


class DashboardFeedCandidateTest(TierSetupMixin, TestCase):
    """The dashboard feed-candidate fallback (``content/views/home.py`` ~1104)."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='feed-candidate@test.com', password='testpass',
        )
        self.client.login(email='feed-candidate@test.com', password='testpass')

    def test_dashboard_never_surfaces_a_hidden_series_occurrence(self):
        hidden_series = _make_hidden_series(
            name='Dashboard Hidden Series', slug='dashboard-hidden-series',
        )
        Event.objects.create(
            title='Dashboard Hidden Feed Event',
            slug='dashboard-hidden-feed-event',
            start_datetime=timezone.now() + timedelta(days=1),
            status='upcoming',
            published=True,
            event_series=hidden_series,
        )

        response = self.client.get('/')
        self.assertNotContains(response, 'Dashboard Hidden Feed Event')
