"""Tests for the Sprint -> EventSeries link (issue #565, renamed from
event-group in #575).

Covers the model-level relationship (FK direction, ``SET_NULL`` semantics,
one-series-many-sprints) and the public sprint detail page's "Meeting
schedule" section (visible, empty state, single extra query). Studio
form / detail surfaces are covered by
``studio.tests.test_sprint_event_series``.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from events.models import Event, EventSeries
from plans.models import Sprint

User = get_user_model()


def _make_event_series(name='Weekly office hours', slug='weekly-oh'):
    return EventSeries.objects.create(
        name=name,
        slug=slug,
        cadence='weekly',
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone='Europe/Berlin',
    )


def _make_event(series, *, position, status='upcoming', location=''):
    base = datetime.datetime(2026, 5, 6, 18, 0, tzinfo=datetime.timezone.utc)
    start = base + datetime.timedelta(days=7 * (position - 1))
    slug = f'{series.slug}-session-{position}'
    return Event.objects.create(
        title=f'{series.name} — Session {position}',
        slug=slug,
        description='',
        kind='standard',
        platform='zoom',
        start_datetime=start,
        timezone='Europe/Berlin',
        status=status,
        origin='studio',
        event_series=series,
        series_position=position,
        location=location,
        published=True,
    )


def _warm_public_shell_caches():
    """Prime site-wide public caches outside strict view query guards."""
    from content.nav_availability import refresh_published_downloads_nav_cache
    from integrations.config import clear_config_cache, get_config, site_base_url
    from integrations.middleware import (
        clear_announcement_banner_cache,
        clear_redirect_cache,
        get_active_redirects,
        get_announcement_banner,
    )

    clear_redirect_cache()
    get_active_redirects()
    clear_config_cache()
    site_base_url()
    get_config('STRIPE_CUSTOMER_PORTAL_URL', '')
    get_config('GOOGLE_ANALYTICS_ID', '')
    clear_announcement_banner_cache()
    get_announcement_banner()
    refresh_published_downloads_nav_cache()


class SprintEventSeriesRelationTest(TestCase):
    """Model-level FK semantics: ``SET_NULL`` + many-sprints-per-series."""

    @classmethod
    def setUpTestData(cls):
        cls.series = _make_event_series()
        cls.s1 = Sprint.objects.create(
            name='May cohort', slug='may-cohort',
            start_date=datetime.date(2026, 5, 1),
            event_series=cls.series,
        )
        cls.s2 = Sprint.objects.create(
            name='June cohort', slug='june-cohort',
            start_date=datetime.date(2026, 6, 1),
            event_series=cls.series,
        )

    def test_one_event_series_can_back_multiple_sprints(self):
        # Both sprints reference the same series; no uniqueness constraint
        # blocks the second assignment.
        self.assertEqual(self.s1.event_series_id, self.series.pk)
        self.assertEqual(self.s2.event_series_id, self.series.pk)
        self.assertEqual(self.series.sprints.count(), 2)

    def test_deleting_event_series_unlinks_sprints_but_keeps_them(self):
        event = _make_event(self.series, position=1)
        # SET_NULL: the sprint and the event should both survive deletion
        # of the series, with the FK cleared. The event has its own SET_NULL
        # back-link to the series (issue #564) so it stays alive too.
        self.series.delete()

        self.s1.refresh_from_db()
        self.s2.refresh_from_db()
        self.assertIsNone(self.s1.event_series)
        self.assertIsNone(self.s2.event_series)
        # The Event row survives -- only its event_series FK is cleared.
        event.refresh_from_db()
        self.assertIsNone(event.event_series)
        # Sprints are NOT cascaded.
        self.assertTrue(Sprint.objects.filter(pk=self.s1.pk).exists())
        self.assertTrue(Sprint.objects.filter(pk=self.s2.pk).exists())

    def test_event_series_unlink_does_not_delete_series(self):
        # Clearing the FK on a sprint must not touch the series or its events.
        event = _make_event(self.series, position=1)
        self.s1.event_series = None
        self.s1.save()

        self.assertTrue(EventSeries.objects.filter(pk=self.series.pk).exists())
        event.refresh_from_db()
        self.assertEqual(event.event_series_id, self.series.pk)
        # The other sprint's link is untouched.
        self.s2.refresh_from_db()
        self.assertEqual(self.s2.event_series_id, self.series.pk)


class PublicSprintDetailMeetingScheduleTest(TestCase):
    """The public ``/sprints/<slug>`` calls section."""

    @classmethod
    def setUpTestData(cls):
        cls.series = _make_event_series(name='Wed OH', slug='wed-oh')
        cls.event_1 = _make_event(cls.series, position=1, location='Zoom')
        cls.event_2 = _make_event(cls.series, position=2, location='Zoom')
        cls.event_3 = _make_event(cls.series, position=3, location='Zoom')
        cls.linked_sprint = Sprint.objects.create(
            name='May 2026 sprint', slug='may-2026-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
            event_series=cls.series,
        )
        cls.unlinked_sprint = Sprint.objects.create(
            name='Solo sprint', slug='solo-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
        )
        cls.empty_series = _make_event_series(
            name='Empty series', slug='empty-series',
        )
        cls.empty_series_sprint = Sprint.objects.create(
            name='Empty sprint', slug='empty-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
            min_tier_level=0,
            event_series=cls.empty_series,
        )

    def test_section_renders_for_linked_sprint_with_events(self):
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-meeting-schedule"')
        # All three occurrences are listed.
        self.assertContains(
            response,
            'data-testid="sprint-call-entry"',
            count=3,
        )
        # Issue #673: each occurrence links to the canonical
        # ``/events/<id>/<slug>`` URL via ``Event.get_absolute_url``.
        self.assertContains(response, self.event_1.get_absolute_url())
        self.assertContains(response, self.event_2.get_absolute_url())
        self.assertContains(response, self.event_3.get_absolute_url())
        # Heading appears.
        self.assertContains(response, 'Sprint calls')

    def test_meeting_time_localized_to_event_timezone(self):
        """Issue #867: meeting times must render in the event's own timezone.

        Fixture events are stored at 18:00 UTC with ``Europe/Berlin``; in May
        that is CEST (+02:00), so the schedule must show 20:00, not the raw
        18:00 UTC clock time labeled Berlin.
        """
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        response = self.client.get(url)
        self.assertContains(response, '20:00 Europe/Berlin')
        self.assertNotContains(response, '18:00 Europe/Berlin')

    def test_empty_state_when_sprint_has_no_event_series(self):
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.unlinked_sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-meeting-schedule"')
        self.assertContains(response, 'data-testid="sprint-calls-empty"')
        self.assertContains(response, 'No calls scheduled yet')

    def test_empty_state_when_linked_series_has_no_events(self):
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.empty_series_sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-meeting-schedule"')
        self.assertContains(response, 'data-testid="sprint-calls-empty"')
        self.assertContains(response, 'No calls scheduled yet')

    def test_occurrences_ordered_by_start_datetime(self):
        # Insert an out-of-order event with an earlier start to verify
        # the prefetch ORDER BY is honoured.
        out_of_order = Event.objects.create(
            title='Earlier',
            slug='wed-oh-earlier',
            description='',
            kind='standard',
            platform='zoom',
            start_datetime=datetime.datetime(
                2026, 4, 1, 18, 0, tzinfo=datetime.timezone.utc,
            ),
            timezone='Europe/Berlin',
            status='upcoming',
            origin='studio',
            event_series=self.series,
            series_position=99,
            published=True,
        )
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        response = self.client.get(url)
        content = response.content.decode()
        # Earliest event slug appears before the May events.
        pos_earliest = content.index(out_of_order.slug)
        pos_session_1 = content.index(self.event_1.slug)
        self.assertLess(pos_earliest, pos_session_1)

    def test_query_count_is_bounded(self):
        # ``select_related('event_series')`` collapses the series lookup
        # into the sprint query; ``prefetch_related`` adds a second
        # query for the events. For an anonymous viewer the only
        # database hits are the sprint+series join and the events
        # prefetch -- exactly two queries. A regression to N+1 (one
        # query per event) would blow past this immediately as more
        # occurrences are added.
        url = reverse(
            'sprint_detail',
            kwargs={'sprint_slug': self.linked_sprint.slug},
        )
        _warm_public_shell_caches()
        with self.assertNumQueries(2):
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Adding more events MUST NOT increase the query count -- the
        # prefetch should still cover the whole set.
        for i in range(4, 10):
            _make_event(self.series, position=i)
        with self.assertNumQueries(2):
            self.client.get(url)


class SprintEventSeriesFKDirectionTest(TestCase):
    """The FK lives on Sprint and is optional on both ends."""

    @classmethod
    def setUpTestData(cls):
        cls.series = _make_event_series()

    def test_sprint_can_be_created_without_event_series(self):
        # blank=True / null=True default behavior: no value required.
        sprint = Sprint.objects.create(
            name='Lone', slug='lone',
            start_date=datetime.date(2026, 5, 1),
        )
        self.assertIsNone(sprint.event_series)

    def test_related_name_sprints_resolves_from_series(self):
        s = Sprint.objects.create(
            name='S', slug='s',
            start_date=datetime.date(2026, 5, 1),
            event_series=self.series,
        )
        self.assertIn(s, list(self.series.sprints.all()))
