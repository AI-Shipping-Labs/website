"""Issue #866: series-membership grouping on the public /events listing.

Part A: a series with 2+ upcoming live occurrences renders as ONE grouped
"series card" (badge, name link, cadence + "N upcoming sessions" meta, the
next up to 3 dates, and a "View series" CTA) instead of one card per
occurrence. A one-occurrence series falls back to a normal single card that
keeps the existing "Series: <name>" link. Standalone events are untouched.
Cancelled/draft occurrences never inflate the grouped count or date list, and
the grouped row is positioned chronologically by its earliest occurrence.

Part B coverage (compact series-page rows) lives in
``events.tests.test_cancelled_visibility`` (state chips) and the Playwright
suite (registration interaction); the structural/state-chip rendering of the
compact rows is asserted here at the Django layer.
"""

import zoneinfo
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from events.views.pages import _build_upcoming_rows

User = get_user_model()


def _weekly_occurrence_starts(after, weekday, hour, minute, count,
                              tz='Europe/Berlin'):
    """Return ``count`` tz-aware UTC datetimes, one per week, each landing on
    ``weekday`` at ``hour:minute`` local ``tz``.

    Each occurrence is built from a local wall-clock date 7 days after the
    previous one and then converted to UTC, so every occurrence stays at the
    same local time even across a DST change. Used to build a genuinely-weekly
    fixture whose occurrences land on the series' stored weekday/time, so
    ``EventSeries.is_regular_cadence`` is True and ``schedule_label`` reads
    "Weekly on …". The first occurrence is at least 3 days out so a soonest
    standalone event (``now + 1 day``) stays ahead of the series in the
    chronological ordering assertions.
    """
    zone = zoneinfo.ZoneInfo(tz)
    local = after.astimezone(zone)
    first_date = local.date() + timedelta(days=3)
    while first_date.weekday() != weekday:
        first_date += timedelta(days=1)
    starts = []
    for i in range(count):
        day = first_date + timedelta(days=7 * i)
        wall = timezone.datetime(
            day.year, day.month, day.day, hour, minute, tzinfo=zone,
        )
        starts.append(wall.astimezone(zoneinfo.ZoneInfo('UTC')))
    return starts


class UpcomingRowsBuilderTest(TestCase):
    """Unit coverage for the view helper that builds the grouped rows."""

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.series = EventSeries.objects.create(
            name='Office Hours', slug='office-hours', start_time=time(18, 0),
        )

    def _make(self, slug, days, series=None, position=0, status='upcoming'):
        return Event.objects.create(
            title=slug.replace('-', ' ').title(), slug=slug,
            start_datetime=self.now + timedelta(days=days),
            status=status, origin='studio',
            event_series=series, series_position=position,
        )

    def test_series_with_two_plus_occurrences_groups_into_one_row(self):
        self._make('oh-1', 2, self.series, 1)
        self._make('oh-2', 9, self.series, 2)
        self._make('oh-3', 16, self.series, 3)
        upcoming = Event.objects.filter(
            event_series=self.series,
        ).order_by('start_datetime')

        rows = _build_upcoming_rows(upcoming)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['kind'], 'series')
        self.assertEqual(row['series'], self.series)
        self.assertEqual(row['count'], 3)
        self.assertEqual(len(row['preview']), 3)
        self.assertEqual(row['extra'], 0)

    def test_preview_caps_at_three_with_extra_count(self):
        for i in range(5):
            self._make(f'oh-{i}', 2 + i * 7, self.series, i + 1)
        upcoming = Event.objects.filter(
            event_series=self.series,
        ).order_by('start_datetime')

        rows = _build_upcoming_rows(upcoming)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['count'], 5)
        self.assertEqual(len(rows[0]['preview']), 3)
        self.assertEqual(rows[0]['extra'], 2)

    def test_single_occurrence_series_stays_an_event_row(self):
        self._make('solo-1', 4, self.series, 1)
        upcoming = Event.objects.filter(
            event_series=self.series,
        ).order_by('start_datetime')

        rows = _build_upcoming_rows(upcoming)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['kind'], 'event')
        self.assertEqual(rows[0]['event'].slug, 'solo-1')

    def test_standalone_events_are_event_rows(self):
        self._make('standalone-a', 1)
        self._make('standalone-b', 3)
        upcoming = Event.objects.all().order_by('start_datetime')

        rows = _build_upcoming_rows(upcoming)

        self.assertEqual([r['kind'] for r in rows], ['event', 'event'])

    def test_rows_sorted_chronologically_by_earliest(self):
        # Standalone event sooner than the series' earliest occurrence.
        self._make('standalone-soon', 1)
        self._make('oh-1', 5, self.series, 1)
        self._make('oh-2', 12, self.series, 2)
        # Standalone event after the series starts.
        self._make('standalone-late', 30)
        upcoming = Event.objects.all().order_by('start_datetime')

        rows = _build_upcoming_rows(upcoming)

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['kind'], 'event')
        self.assertEqual(rows[0]['event'].slug, 'standalone-soon')
        self.assertEqual(rows[1]['kind'], 'series')
        self.assertEqual(rows[2]['kind'], 'event')
        self.assertEqual(rows[2]['event'].slug, 'standalone-late')


class EventsListSeriesCardRenderTest(TestCase):
    """The /events listing renders the grouped series card, not N cards."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.series = EventSeries.objects.create(
            name='LLM Zoomcamp office hours', slug='llm-oh',
            cadence='weekly', day_of_week=2, start_time=time(18, 0),
            timezone='Europe/Berlin',
        )
        # 4 upcoming live occurrences on the next four Wednesdays at 18:00
        # Europe/Berlin. Issue #877/#947: the card cadence is now the honest
        # ``schedule_label`` derived from the real occurrences, so a genuinely
        # weekly fixture is required for the card to read "Weekly on Wednesday
        # at 18:00 Europe/Berlin". (The old fixture spaced occurrences 7 days
        # from ``now`` on an arbitrary weekday, which is no longer regular.)
        starts = _weekly_occurrence_starts(
            now, weekday=2, hour=18, minute=0, count=4,
        )
        for i, start in enumerate(starts):
            Event.objects.create(
                title=f'Office Hours Session {i}', slug=f'oh-session-{i}',
                start_datetime=start,
                timezone='Europe/Berlin',
                status='upcoming', origin='studio',
                event_series=cls.series, series_position=i + 1,
            )
        # One unrelated standalone upcoming event, soonest of all.
        cls.standalone = Event.objects.create(
            title='Standalone Kickoff', slug='standalone-kickoff',
            start_datetime=now + timedelta(days=1),
            status='upcoming', origin='studio',
        )

    def test_series_renders_as_single_grouped_card(self):
        response = self.client.get('/events')
        self.assertEqual(response.status_code, 200)
        rows = response.context['upcoming_rows']
        series_rows = [r for r in rows if r['kind'] == 'series']
        self.assertEqual(len(series_rows), 1)
        self.assertEqual(series_rows[0]['count'], 4)

    def test_grouped_card_shows_badge_name_cta_and_dates(self):
        response = self.client.get('/events')
        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, 'data-testid="series-card-badge"')
        # Name links to the series page.
        self.assertContains(
            response,
            '<a href="/events/groups/llm-oh"',
        )
        # Cadence + session-count meta line.
        self.assertContains(response, '4 upcoming sessions')
        self.assertContains(response, 'Weekly on Wednesday at 18:00')
        # "View series" CTA.
        self.assertContains(response, 'data-testid="series-card-cta"')
        self.assertContains(response, 'View series')
        # The next dates list is present.
        self.assertContains(response, 'data-testid="series-card-dates"')

    def test_individual_occurrence_titles_not_repeated_as_cards(self):
        # The grouped card replaces the 4 per-occurrence cards: the
        # occurrence titles do not appear as separate event-card links.
        response = self.client.get('/events')
        self.assertNotContains(response, 'Office Hours Session 0')
        self.assertNotContains(response, 'Office Hours Session 3')

    def test_standalone_event_renders_as_its_own_card(self):
        response = self.client.get('/events')
        self.assertContains(response, 'Standalone Kickoff')
        rows = response.context['upcoming_rows']
        event_rows = [r for r in rows if r['kind'] == 'event']
        self.assertEqual(len(event_rows), 1)
        self.assertEqual(event_rows[0]['event'].slug, 'standalone-kickoff')

    def test_grouped_card_ordered_chronologically(self):
        # Standalone is day+1, series earliest is day+2, so standalone first.
        response = self.client.get('/events')
        rows = response.context['upcoming_rows']
        self.assertEqual(rows[0]['kind'], 'event')
        self.assertEqual(rows[0]['event'].slug, 'standalone-kickoff')
        self.assertEqual(rows[1]['kind'], 'series')

    def test_developer_comments_do_not_leak_into_rendered_page(self):
        # Both upcoming partials (_upcoming_event_card / _upcoming_series_card)
        # open with a multi-line file-header comment. A bare ``{# ... #}`` is
        # single-line only, so a multi-line one leaks lines 2+ as visible text
        # on /events. These must be wrapped in {% comment %}. Assert distinctive
        # phrases from each header comment never reach the response body so the
        # leak can never silently return (QA round 2, issue #866).
        response = self.client.get('/events')
        self.assertEqual(response.status_code, 200)
        # Both partials are exercised: a grouped series card and a standalone
        # event card are both present on this page.
        self.assertContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, 'Standalone Kickoff')
        # Distinctive phrase from _upcoming_event_card.html's header comment.
        self.assertNotContains(
            response, 'the Upcoming loop can render either an event row',
        )
        # Distinctive phrase from _upcoming_series_card.html's header comment.
        self.assertNotContains(
            response, 'Every link routes to the series page',
        )


class GroupedCardExcludesCancelledAndDraftTest(TestCase):
    """Cancelled/draft occurrences never inflate the grouped count or dates."""

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.series = EventSeries.objects.create(
            name='Mixed Status Series', slug='mixed-series',
            start_time=time(18, 0),
        )

    def test_cancelled_occurrence_does_not_inflate_count(self):
        Event.objects.create(
            title='live-a', slug='live-a',
            start_datetime=self.now + timedelta(days=2),
            status='upcoming', origin='studio',
            event_series=self.series, series_position=1,
        )
        Event.objects.create(
            title='live-b', slug='live-b',
            start_datetime=self.now + timedelta(days=9),
            status='upcoming', origin='studio',
            event_series=self.series, series_position=2,
        )
        Event.objects.create(
            title='cancelled-c', slug='cancelled-c',
            start_datetime=self.now + timedelta(days=16),
            status='cancelled', origin='studio',
            event_series=self.series, series_position=3,
        )

        response = self.client.get('/events')
        rows = response.context['upcoming_rows']
        series_rows = [r for r in rows if r['kind'] == 'series']
        self.assertEqual(len(series_rows), 1)
        # 2 live, the cancelled one is excluded.
        self.assertEqual(series_rows[0]['count'], 2)
        self.assertContains(response, '2 upcoming sessions')
        self.assertNotContains(response, 'cancelled-c')

    def test_falls_back_to_single_card_when_only_one_live_remains(self):
        Event.objects.create(
            title='only-live', slug='only-live',
            start_datetime=self.now + timedelta(days=2),
            status='upcoming', origin='studio',
            event_series=self.series, series_position=1,
        )
        Event.objects.create(
            title='cancelled-x', slug='cancelled-x',
            start_datetime=self.now + timedelta(days=9),
            status='cancelled', origin='studio',
            event_series=self.series, series_position=2,
        )
        Event.objects.create(
            title='draft-y', slug='draft-y',
            start_datetime=self.now + timedelta(days=16),
            status='draft', origin='studio',
            event_series=self.series, series_position=3,
        )

        response = self.client.get('/events')
        rows = response.context['upcoming_rows']
        # Only one live occurrence -> single event card, no grouped card.
        self.assertEqual([r['kind'] for r in rows], ['event'])
        self.assertNotContains(response, 'data-testid="event-series-card"')
        # The single card keeps the "Series: <name>" membership link.
        self.assertContains(response, 'data-testid="event-card-series-link"')
        self.assertContains(response, 'Series: Mixed Status Series')


class OneSessionSeriesShowsMembershipTest(TestCase):
    """A one-session series renders as a normal card with the series link."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Solo Series', slug='solo-series', start_time=time(18, 0),
        )
        Event.objects.create(
            title='Solo Occurrence', slug='solo-occurrence',
            start_datetime=timezone.now() + timedelta(days=4),
            status='upcoming', origin='studio',
            event_series=cls.series, series_position=1,
        )

    def test_renders_single_card_with_membership_link(self):
        response = self.client.get('/events')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="event-series-card"')
        self.assertContains(response, 'data-testid="event-card-series-link"')
        self.assertContains(response, 'Series: Solo Series')
        self.assertContains(response, 'Solo Occurrence')


class CompactSeriesPageRowsTest(TestCase):
    """Issue #866 Part B: the series page renders compact divided rows.

    Asserts the structural shape (single bordered divided container, compact
    rows preserving title/date/state-chip data-testids) and the cancelled
    treatment (excluded for the public, shown to staff with a Cancelled chip).
    The per-occurrence registration interaction is covered in Playwright.
    """

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.series = EventSeries.objects.create(
            name='Compact Office Hours', slug='compact-oh',
            start_time=time(18, 0),
        )
        cls.live_open = Event.objects.create(
            title='Open Future Session', slug='open-future-session',
            start_datetime=cls.now + timedelta(days=3),
            status='upcoming', origin='studio',
            event_series=cls.series, series_position=1,
        )
        cls.past = Event.objects.create(
            title='Finished Session', slug='finished-session',
            start_datetime=cls.now - timedelta(days=3),
            status='completed', origin='studio',
            event_series=cls.series, series_position=2,
        )
        cls.cancelled = Event.objects.create(
            title='Scrapped Session', slug='scrapped-session',
            start_datetime=cls.now + timedelta(days=10),
            status='cancelled', origin='studio',
            event_series=cls.series, series_position=3,
        )

    def test_rows_use_single_divided_container_not_per_row_cards(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertEqual(response.status_code, 200)
        # The list wrapper is a single divided bordered container.
        self.assertContains(response, 'divide-y divide-border')
        # Rows preserve their structural data-testids.
        self.assertContains(response, 'data-testid="series-event"')
        self.assertContains(response, 'data-testid="series-event-link"')
        self.assertContains(response, 'data-testid="series-event-date"')

    def test_state_chips_render_per_occurrence_for_anon(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        # Open future session -> Register chip; past session -> Past chip.
        self.assertContains(response, 'data-testid="series-event-state-register"')
        self.assertContains(response, 'data-testid="series-event-state-past"')

    def test_cancelled_excluded_for_public(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertNotContains(response, 'Scrapped Session')
        self.assertNotContains(
            response, 'data-testid="series-event-state-cancelled"',
        )

    def test_staff_sees_cancelled_with_chip(self):
        staff = User.objects.create_user(
            email='staff866@test.com', password='pass', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertContains(response, 'Scrapped Session')
        self.assertContains(
            response, 'data-testid="series-event-state-cancelled"',
        )

    def test_empty_state_renders_when_no_visible_occurrences(self):
        # A series with zero occurrences 404s for the public (issue #858), so
        # the empty-state branch is reachable only by staff previewing it.
        empty_series = EventSeries.objects.create(
            name='Empty Series', slug='empty-series', start_time=time(18, 0),
        )
        staff = User.objects.create_user(
            email='staff866b@test.com', password='pass', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(f'/events/groups/{empty_series.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'No published events in this series yet.')
