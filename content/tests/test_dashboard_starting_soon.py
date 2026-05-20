"""Tests for the dashboard "Starting soon" card - issue #705.

Covers:
- Window boundaries (11/10/8/5/3/1 min, 0 sec, past)
- Multi-registration selection (soonest wins)
- Not-registered case (no card)
- Excluded statuses (cancelled, completed, draft)
- ``format_user_datetime`` user-TZ suffix (Europe/Berlin vs default UTC)
- Join URL is ``/events/<slug>/join``, not the raw Zoom URL
- Card label flips at the 5-min boundary
- Meta refresh tag present only when the card is showing
- Upcoming Events list still includes the same imminent event
  (intentional double-display)
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from content.views.home import _get_starting_soon_event
from events.models import Event, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_event(slug, start, *, title=None, status='upcoming', zoom_url='https://zoom.us/j/abc'):
    return Event.objects.create(
        slug=slug,
        title=title or slug.replace('-', ' ').title(),
        start_datetime=start,
        status=status,
        zoom_join_url=zoom_url,
    )


class StartingSoonHelperWindowTest(TierSetupMixin, TestCase):
    """Direct tests for ``_get_starting_soon_event`` against the time window.

    The helper takes a user and returns either a dict (card data) or
    ``None``. These tests exercise the window math without going through
    the full dashboard view.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='attendee@example.com', password='testpass',
        )

    def test_returns_none_when_no_registrations(self):
        # No registrations at all -> no card.
        result = _get_starting_soon_event(self.user)
        self.assertIsNone(result)

    def test_event_30_min_away_returns_none(self):
        event = _make_event(
            'far', timezone.now() + timedelta(minutes=30),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))

    def test_event_11_min_away_returns_none(self):
        # Just outside the 10-min window.
        event = _make_event(
            'just-outside', timezone.now() + timedelta(minutes=11),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))

    def test_event_10_min_away_is_inside_window(self):
        # 10 min exactly should still appear (inclusive upper bound).
        event = _make_event(
            'edge-10', timezone.now() + timedelta(minutes=10),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        result = _get_starting_soon_event(self.user)
        self.assertIsNotNone(result)
        self.assertEqual(result['event'].pk, event.pk)
        # 10 min is outside the <=5min branch -> "Open join page".
        self.assertEqual(result['join_label'], 'Open join page')

    def test_event_8_min_away_renders_open_join_page_label(self):
        event = _make_event(
            'eight', timezone.now() + timedelta(minutes=8),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        result = _get_starting_soon_event(self.user)
        self.assertIsNotNone(result)
        self.assertEqual(result['join_label'], 'Open join page')
        # Within 1 sec of 8 minutes.
        self.assertGreaterEqual(result['seconds_until_start'], 8 * 60 - 1)
        self.assertLessEqual(result['seconds_until_start'], 8 * 60)
        self.assertEqual(result['minutes_until_start'], 7)
        # Join URL is the time-gated dashboard endpoint, never raw Zoom.
        self.assertEqual(
            result['join_url'],
            reverse('event_join', kwargs={'slug': event.slug}),
        )
        self.assertNotIn('zoom.us', result['join_url'])

    def test_event_5_min_away_flips_to_join_now(self):
        # delta == 5 min must use the "Join now" label
        # (inclusive on the Join-now side, matching #704).
        event = _make_event(
            'five', timezone.now() + timedelta(minutes=5),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        result = _get_starting_soon_event(self.user)
        self.assertIsNotNone(result)
        self.assertEqual(result['join_label'], 'Join now')

    def test_event_3_min_away_uses_join_now_label(self):
        event = _make_event(
            'three', timezone.now() + timedelta(minutes=3),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        result = _get_starting_soon_event(self.user)
        self.assertIsNotNone(result)
        self.assertEqual(result['join_label'], 'Join now')

    def test_event_starting_in_one_second_still_in_window(self):
        event = _make_event(
            'almost-now', timezone.now() + timedelta(seconds=1),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        result = _get_starting_soon_event(self.user)
        self.assertIsNotNone(result)
        self.assertEqual(result['join_label'], 'Join now')

    def test_event_already_started_returns_none(self):
        # start_datetime in the past -> excluded by the strict ``> now`` filter.
        event = _make_event(
            'started', timezone.now() - timedelta(minutes=1),
        )
        EventRegistration.objects.create(user=self.user, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))


class StartingSoonExcludedStatusesTest(TierSetupMixin, TestCase):
    """Cancelled / completed / draft events never produce a card."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='filter@example.com', password='testpass',
        )

    def test_cancelled_event_in_window_excluded(self):
        event = _make_event(
            'cancelled', timezone.now() + timedelta(minutes=5),
            status='cancelled',
        )
        EventRegistration.objects.create(user=self.user, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))

    def test_completed_event_in_window_excluded(self):
        event = _make_event(
            'completed', timezone.now() + timedelta(minutes=5),
            status='completed',
        )
        EventRegistration.objects.create(user=self.user, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))

    def test_draft_event_in_window_excluded(self):
        event = _make_event(
            'draft', timezone.now() + timedelta(minutes=5),
            status='draft',
        )
        EventRegistration.objects.create(user=self.user, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))


class StartingSoonSoonestWinsTest(TierSetupMixin, TestCase):
    """Two imminent registrations -> only the soonest appears in the card."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='multi@example.com', password='testpass',
        )

    def test_only_soonest_imminent_event_returned(self):
        now = timezone.now()
        later = _make_event('later', now + timedelta(minutes=9), title='Later Event')
        sooner = _make_event('sooner', now + timedelta(minutes=3), title='Sooner Event')
        EventRegistration.objects.create(user=self.user, event=later)
        EventRegistration.objects.create(user=self.user, event=sooner)

        result = _get_starting_soon_event(self.user)
        self.assertIsNotNone(result)
        self.assertEqual(result['event'].pk, sooner.pk)


class StartingSoonNotRegisteredTest(TierSetupMixin, TestCase):
    """Unregistered users see no card even when an imminent event exists."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='nonreg@example.com', password='testpass',
        )

    def test_unregistered_imminent_event_does_not_produce_card(self):
        _make_event('lonely', timezone.now() + timedelta(minutes=5))
        # No EventRegistration row.
        self.assertIsNone(_get_starting_soon_event(self.user))

    def test_other_users_registration_does_not_leak(self):
        other = User.objects.create_user(
            email='other@example.com', password='testpass',
        )
        event = _make_event('theirs', timezone.now() + timedelta(minutes=5))
        EventRegistration.objects.create(user=other, event=event)
        self.assertIsNone(_get_starting_soon_event(self.user))


class StartingSoonTimezoneTest(TierSetupMixin, TestCase):
    """Local-time formatting uses ``format_user_datetime`` with the user TZ."""

    def test_user_with_berlin_tz_sees_berlin_suffix(self):
        user = User.objects.create_user(
            email='berlin@example.com',
            password='testpass',
            preferred_timezone='Europe/Berlin',
        )
        event = _make_event('tz-berlin', timezone.now() + timedelta(minutes=5))
        EventRegistration.objects.create(user=user, event=event)
        result = _get_starting_soon_event(user)
        self.assertIsNotNone(result)
        self.assertTrue(
            result['event_start_local'].endswith('Europe/Berlin'),
            msg=f"Expected Europe/Berlin suffix, got: {result['event_start_local']!r}",
        )

    def test_user_without_tz_falls_back_to_utc(self):
        user = User.objects.create_user(
            email='notz@example.com', password='testpass',
        )
        event = _make_event('tz-utc', timezone.now() + timedelta(minutes=5))
        EventRegistration.objects.create(user=user, event=event)
        result = _get_starting_soon_event(user)
        self.assertIsNotNone(result)
        self.assertTrue(
            result['event_start_local'].endswith('UTC'),
            msg=f"Expected UTC suffix, got: {result['event_start_local']!r}",
        )


# ============================================================
# Dashboard view integration tests
# ============================================================


class StartingSoonDashboardViewTest(TierSetupMixin, TestCase):
    """End-to-end: the dashboard renders the partial and meta refresh."""

    def setUp(self):
        self.user = User.objects.create_user(
            email='dash@example.com', password='testpass',
        )
        self.client.login(email='dash@example.com', password='testpass')

    def test_imminent_event_renders_card_and_meta_refresh(self):
        event = _make_event(
            'cohort-call', timezone.now() + timedelta(minutes=8),
            title='Cohort Office Hours',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # Card wrapper present.
        self.assertContains(
            response, 'data-testid="starting-soon-card"',
        )
        # Countdown span present with seconds attribute.
        self.assertContains(
            response, 'data-testid="starting-soon-countdown-timer"',
        )
        self.assertContains(response, 'data-seconds-remaining=')
        # Title shown.
        self.assertContains(response, 'Cohort Office Hours')
        # Open join page label (> 5 min away).
        self.assertContains(response, 'Open join page')
        # Join URL is /events/<slug>/join, NEVER raw Zoom.
        join_url = reverse('event_join', kwargs={'slug': event.slug})
        self.assertContains(response, f'href="{join_url}"')
        self.assertNotContains(response, 'href="https://zoom.us/j/abc"')
        # Meta refresh present.
        self.assertContains(
            response, '<meta http-equiv="refresh" content="30">',
        )

    def test_event_inside_5_min_window_shows_join_now_label(self):
        event = _make_event(
            'now-call', timezone.now() + timedelta(minutes=3),
            title='Imminent Workshop',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertContains(
            response, 'data-testid="starting-soon-card"',
        )
        self.assertContains(response, 'Join now')
        self.assertNotContains(response, 'Open join page')

    def test_far_event_does_not_render_card_or_meta_refresh(self):
        # An event 2 hours away must not produce a card.
        event = _make_event(
            'far-future', timezone.now() + timedelta(hours=2),
            title='Tomorrow Event',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        self.assertNotContains(
            response, 'data-testid="starting-soon-card"',
        )
        # Meta refresh must NOT leak on the no-card path.
        self.assertNotContains(
            response, '<meta http-equiv="refresh" content="30">',
        )
        # The same event still appears in the Upcoming Events list.
        self.assertContains(response, 'Tomorrow Event')

    def test_imminent_event_still_appears_in_upcoming_events_list(self):
        # Intentional double-display: the card is urgency, the list is context.
        event = _make_event(
            'dual-show', timezone.now() + timedelta(minutes=7),
            title='Both Surfaces',
        )
        EventRegistration.objects.create(user=self.user, event=event)

        response = self.client.get('/')
        # Title rendered AT LEAST twice — once in the card, once in the list.
        # The card has the data-testid; the list has the "View Event" CTA.
        self.assertContains(response, 'Both Surfaces', count=2)
        self.assertContains(response, 'View Event')

    def test_unregistered_user_with_imminent_event_sees_no_card(self):
        # Event exists in the window but the user is not registered.
        _make_event(
            'someone-elses', timezone.now() + timedelta(minutes=5),
            title='Other Org Event',
        )
        response = self.client.get('/')
        self.assertNotContains(
            response, 'data-testid="starting-soon-card"',
        )
        self.assertNotContains(
            response, '<meta http-equiv="refresh" content="30">',
        )

    def test_two_imminent_registrations_renders_only_soonest_card(self):
        now = timezone.now()
        sooner = _make_event('s', now + timedelta(minutes=2), title='Sooner Card Pick')
        later = _make_event('l', now + timedelta(minutes=9), title='Later Card Skip')
        EventRegistration.objects.create(user=self.user, event=sooner)
        EventRegistration.objects.create(user=self.user, event=later)

        response = self.client.get('/')
        content = response.content.decode()
        # The card wrapper appears exactly once.
        self.assertEqual(content.count('data-testid="starting-soon-card"'), 1)
        # Exactly one countdown <span> is rendered. The literal
        # ``starting-soon-countdown-timer`` also appears inside the inline
        # JS that queries the span, so we anchor on the ``data-seconds-remaining``
        # attribute the helper renders on the span itself.
        self.assertEqual(content.count('data-seconds-remaining='), 1)
        # The card displays the SOONER event.
        # Find the card region and assert the sooner title is inside it.
        card_start = content.find('data-testid="starting-soon-card"')
        card_end = content.find('</section>', card_start)
        card_html = content[card_start:card_end]
        self.assertIn('Sooner Card Pick', card_html)
        self.assertNotIn('Later Card Skip', card_html)


class StartingSoonFreezeTimeTest(TierSetupMixin, TestCase):
    """Window edges with frozen time for deterministic boundaries."""

    @freeze_time('2026-06-15T12:00:00Z')
    def test_event_at_t_plus_10_min_card_visible(self):
        user = User.objects.create_user(
            email='ft1@example.com', password='testpass',
        )
        event = _make_event(
            'ten-min-edge',
            timezone.now() + timedelta(minutes=10),
            title='Exactly Ten',
        )
        EventRegistration.objects.create(user=user, event=event)

        self.client.login(email='ft1@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'data-testid="starting-soon-card"')
        self.assertContains(response, 'Exactly Ten')
        self.assertContains(response, 'Open join page')

    @freeze_time('2026-06-15T12:00:00Z')
    def test_event_at_t_plus_11_min_no_card(self):
        user = User.objects.create_user(
            email='ft2@example.com', password='testpass',
        )
        event = _make_event(
            'eleven-min-edge',
            timezone.now() + timedelta(minutes=11),
            title='Eleven Out',
        )
        EventRegistration.objects.create(user=user, event=event)

        self.client.login(email='ft2@example.com', password='testpass')
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="starting-soon-card"')

    @freeze_time('2026-06-15T12:00:00Z')
    def test_event_at_t_plus_5_min_join_now_label(self):
        # Inclusive on the Join-now side, matching #704.
        user = User.objects.create_user(
            email='ft3@example.com', password='testpass',
        )
        event = _make_event(
            'five-min-edge',
            timezone.now() + timedelta(minutes=5),
            title='Five Min',
        )
        EventRegistration.objects.create(user=user, event=event)

        self.client.login(email='ft3@example.com', password='testpass')
        response = self.client.get('/')
        self.assertContains(response, 'Join now')
        self.assertNotContains(response, 'Open join page')

    @freeze_time('2026-06-15T12:00:00Z')
    def test_event_in_past_no_card(self):
        user = User.objects.create_user(
            email='ft4@example.com', password='testpass',
        )
        event = _make_event(
            'one-min-past',
            timezone.now() - timedelta(minutes=1),
            title='Already Past',
        )
        EventRegistration.objects.create(user=user, event=event)

        self.client.login(email='ft4@example.com', password='testpass')
        response = self.client.get('/')
        self.assertNotContains(response, 'data-testid="starting-soon-card"')
