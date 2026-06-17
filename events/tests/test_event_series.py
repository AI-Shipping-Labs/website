"""Tests for the EventSeries model and origin invariant on Event.

Issue #564 (renamed from EventGroup in #575).

Covers:
- ``EventSeries`` model: slug auto-derivation, description markdown.
- ``Event.origin`` invariant: github iff source_repo is set.
- ``studio.utils.is_synced`` branching on ``origin``.
- Public ``/events/series/<id>/<slug>`` view.
- Public events list shows series link when an event belongs to a series.
"""

import re
import zoneinfo
from datetime import UTC, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import Event, EventSeries
from studio.utils import is_synced

User = get_user_model()


class EventSeriesModelTest(TestCase):
    """EventSeries save behavior and computed properties."""

    def test_slug_auto_derived_from_name(self):
        series = EventSeries.objects.create(
            name='Spring Workshop Series',
            start_time=time(18, 0),
        )
        self.assertEqual(series.slug, 'spring-workshop-series')

    def test_explicit_slug_preserved(self):
        series = EventSeries.objects.create(
            name='Spring Workshop Series',
            slug='custom-slug',
            start_time=time(18, 0),
        )
        self.assertEqual(series.slug, 'custom-slug')

    def test_description_renders_to_html(self):
        series = EventSeries.objects.create(
            name='Markdown Series',
            description='# Heading\n\nA paragraph.',
            start_time=time(18, 0),
        )
        self.assertIn('<h1>Heading</h1>', series.description_html)

    def test_event_count_reflects_member_events(self):
        series = EventSeries.objects.create(
            name='Counted', start_time=time(18, 0),
        )
        Event.objects.create(
            title='Session 1', slug='counted-session-1',
            start_datetime=timezone.now(),
            event_series=series, series_position=1, origin='studio',
        )
        self.assertEqual(series.event_count, 1)

    def test_get_absolute_url_uses_id_and_slug(self):
        series = EventSeries.objects.create(
            name='Canonical Series',
            slug='canonical-series',
            start_time=time(18, 0),
        )
        self.assertEqual(
            series.get_absolute_url(),
            f'/events/series/{series.pk}/canonical-series',
        )


class ScheduleLabelTest(TestCase):
    """Issue #877: cadence label derived from real occurrences.

    ``is_regular_cadence`` decides whether the stored weekly claim is honest;
    ``schedule_label`` renders the header string. Both compute over the
    publicly-visible occurrences (``upcoming`` / ``completed``).
    """

    @staticmethod
    def _series(**kwargs):
        defaults = dict(
            name='Cadence Series',
            day_of_week=2,  # Wednesday
            start_time=time(18, 0),
            timezone='Europe/Berlin',
        )
        defaults.update(kwargs)
        return EventSeries.objects.create(**defaults)

    @staticmethod
    def _occurrence(series, start_local, position, tz='Europe/Berlin',
                    status='upcoming'):
        """Create an occurrence whose local start is ``start_local`` in ``tz``."""
        aware = start_local.replace(tzinfo=zoneinfo.ZoneInfo(tz))
        return Event.objects.create(
            title=f'Session {position}',
            slug=f'{series.slug}-session-{position}',
            start_datetime=aware,
            timezone=tz,
            status=status,
            event_series=series,
            series_position=position,
            origin='studio',
        )

    # --- is_regular_cadence ------------------------------------------------

    def test_regular_weekly_series_is_regular(self):
        series = self._series()
        for i in range(3):
            self._occurrence(
                series,
                datetime(2026, 6, 17, 18, 0) + timedelta(weeks=i),  # Wednesdays
                position=i + 1,
            )
        self.assertTrue(series.is_regular_cadence)

    def test_mixed_weekdays_is_not_regular(self):
        # Reporter's example: occurrences land on mixed weekdays.
        series = self._series()
        schedule = [
            datetime(2026, 6, 15, 18, 0),  # Monday
            datetime(2026, 6, 24, 18, 0),  # Wednesday
            datetime(2026, 6, 29, 18, 0),  # Monday
            datetime(2026, 7, 6, 18, 0),   # Monday
        ]
        for i, dt in enumerate(schedule):
            self._occurrence(series, dt, position=i + 1)
        self.assertFalse(series.is_regular_cadence)

    def test_right_weekday_but_irregular_gaps_is_not_regular(self):
        # Weeks 1, 2, 4 — all Wednesdays at 18:00 but a 14-day gap.
        series = self._series()
        for i, dt in enumerate([
            datetime(2026, 6, 17, 18, 0),
            datetime(2026, 6, 24, 18, 0),
            datetime(2026, 7, 8, 18, 0),  # skips a week -> 14-day gap
        ]):
            self._occurrence(series, dt, position=i + 1)
        self.assertFalse(series.is_regular_cadence)

    def test_wrong_time_is_not_regular(self):
        series = self._series()
        self._occurrence(series, datetime(2026, 6, 17, 18, 0), position=1)
        self._occurrence(series, datetime(2026, 6, 24, 19, 0), position=2)
        self.assertFalse(series.is_regular_cadence)

    def test_zero_occurrences_is_not_regular(self):
        self.assertFalse(self._series().is_regular_cadence)

    def test_single_occurrence_is_not_regular(self):
        series = self._series()
        self._occurrence(series, datetime(2026, 6, 17, 18, 0), position=1)
        self.assertFalse(series.is_regular_cadence)

    def test_six_day_utc_gap_within_tolerance_is_regular(self):
        # A weekly series whose host moved across timezones: every occurrence
        # is the stored weekday at the stored local time, but because one
        # occurrence is stored in a far-eastern tz the UTC-instant gap rounds
        # to 6 days. The +/-1 day tolerance must still treat it as weekly.
        series = self._series()
        self._occurrence(series, datetime(2026, 6, 17, 18, 0), position=1)
        # Wed 18:00 Auckland (UTC+12) is ~10h earlier in UTC than Wed 18:00
        # Berlin, so the instant gap is 6 days 14 hours -> .days == 6.
        # day_of_week (2/Wednesday) and start_time (18:00) still match in NZ.
        self._occurrence(
            series, datetime(2026, 6, 24, 18, 0), position=2,
            tz='Pacific/Auckland',
        )
        self.assertTrue(series.is_regular_cadence)

    def test_draft_and_cancelled_occurrences_ignored(self):
        # A clean weekly series plus a stray draft on a different weekday and a
        # cancelled occurrence at a different time must still read as regular.
        series = self._series()
        for i in range(3):
            self._occurrence(
                series, datetime(2026, 6, 17, 18, 0) + timedelta(weeks=i), position=i + 1,
            )
        self._occurrence(
            series, datetime(2026, 6, 19, 9, 0), position=99,
            status='draft',
        )
        self._occurrence(
            series, datetime(2026, 6, 20, 9, 0), position=98,
            status='cancelled',
        )
        self.assertTrue(series.is_regular_cadence)

    # --- schedule_label ----------------------------------------------------

    def test_label_for_regular_series_matches_legacy_string(self):
        series = self._series()
        for i in range(3):
            self._occurrence(
                series, datetime(2026, 6, 17, 18, 0) + timedelta(weeks=i), position=i + 1,
            )
        self.assertEqual(
            series.schedule_label,
            'Weekly on Wednesday at 18:00 Europe/Berlin',
        )

    def test_label_for_irregular_series_is_neutral_summary(self):
        series = self._series()
        for i, dt in enumerate([
            datetime(2026, 6, 15, 18, 0),  # Monday
            datetime(2026, 6, 24, 18, 0),
            datetime(2026, 6, 29, 18, 0),
            datetime(2026, 7, 6, 18, 0),
            datetime(2026, 7, 21, 18, 0),
            datetime(2026, 8, 3, 18, 0),
        ]):
            self._occurrence(series, dt, position=i + 1)
        label = series.schedule_label
        self.assertEqual(label, '6 sessions · Jun 15, 2026 – Aug 03, 2026')
        self.assertNotIn('Weekly', label)
        self.assertNotIn('Monday', label)

    def test_label_for_single_occurrence_has_no_dash(self):
        series = self._series()
        self._occurrence(series, datetime(2026, 6, 15, 18, 0), position=1)
        label = series.schedule_label
        self.assertEqual(label, '1 session · Jun 15, 2026')
        self.assertNotIn('–', label)
        self.assertNotIn('Weekly', label)

    def test_label_for_zero_occurrences_is_empty(self):
        self.assertEqual(self._series().schedule_label, '')

    def test_label_ignores_draft_and_cancelled_for_count(self):
        series = self._series()
        for i, dt in enumerate([
            datetime(2026, 6, 15, 18, 0),
            datetime(2026, 6, 24, 18, 0),
            datetime(2026, 6, 29, 18, 0),
        ]):
            self._occurrence(series, dt, position=i + 1)
        self._occurrence(
            series, datetime(2026, 7, 1, 18, 0), position=98, status='draft',
        )
        self._occurrence(
            series, datetime(2026, 7, 2, 18, 0), position=99,
            status='cancelled',
        )
        self.assertTrue(series.schedule_label.startswith('3 sessions · '))


class EventOriginInvariantTest(TestCase):
    """``Event.save()`` enforces origin/source_repo consistency."""

    def test_studio_origin_with_source_repo_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            Event.objects.create(
                title='Bad', slug='bad-studio',
                start_datetime=timezone.now(),
                origin='studio',
                source_repo='AI-Shipping-Labs/content',
            )

    def test_github_origin_without_source_repo_raises(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            Event.objects.create(
                title='Bad', slug='bad-github',
                start_datetime=timezone.now(),
                origin='github',
                source_repo='',
            )

    def test_studio_origin_with_empty_source_repo_succeeds(self):
        event = Event.objects.create(
            title='Good', slug='good-studio',
            start_datetime=timezone.now(),
            origin='studio',
        )
        self.assertEqual(event.origin, 'studio')

    def test_github_origin_with_source_repo_succeeds(self):
        event = Event.objects.create(
            title='Good', slug='good-github',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/good.yaml',
        )
        self.assertEqual(event.origin, 'github')


class IsSyncedHelperTest(TestCase):
    """``studio.utils.is_synced`` branches on ``origin`` for events."""

    def test_studio_origin_event_is_not_synced(self):
        event = Event.objects.create(
            title='Studio Event', slug='studio-event',
            start_datetime=timezone.now(),
            origin='studio',
        )
        self.assertFalse(is_synced(event))

    def test_github_origin_event_is_synced(self):
        event = Event.objects.create(
            title='GitHub Event', slug='github-event',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
        )
        self.assertTrue(is_synced(event))

    def test_legacy_object_without_origin_still_works(self):
        """Models without ``origin`` keep the legacy source_repo fallback."""
        # Use a plain object that mimics a non-event model with no origin.
        class Legacy:
            origin = None
            source_repo = 'AI-Shipping-Labs/content'
        self.assertTrue(is_synced(Legacy()))

        class LegacyEmpty:
            source_repo = None
        self.assertFalse(is_synced(LegacyEmpty()))


class PublicEventSeriesViewTest(TestCase):
    """Public ``/events/series/<id>/<slug>`` page."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Spring Series', start_time=time(18, 0),
        )
        cls.published_event = Event.objects.create(
            title='Series Session 1', slug='series-session-1',
            start_datetime=timezone.now(),
            status='upcoming',
            event_series=cls.series, series_position=1, origin='studio',
        )
        cls.draft_event = Event.objects.create(
            title='Series Session 2', slug='series-session-2',
            start_datetime=timezone.now(),
            status='draft',
            event_series=cls.series, series_position=2, origin='studio',
        )

    def test_anonymous_visitor_sees_published_events(self):
        response = self.client.get(self.series.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Series Session 1')

    def test_anonymous_visitor_does_not_see_drafts(self):
        response = self.client.get(self.series.get_absolute_url())
        self.assertNotContains(response, 'Series Session 2')

    def test_unknown_id_returns_404(self):
        response = self.client.get('/events/series/999999/does-not-exist')
        self.assertEqual(response.status_code, 404)

    def test_wrong_slug_redirects_to_current_canonical_url(self):
        response = self.client.get(f'/events/series/{self.series.pk}/stale')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.series.get_absolute_url())

    def test_missing_slug_redirects_to_current_canonical_url(self):
        response = self.client.get(f'/events/series/{self.series.pk}')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.series.get_absolute_url())

    def test_old_groups_url_is_unsupported(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertEqual(response.status_code, 404)

    def test_slug_only_series_url_is_unsupported(self):
        response = self.client.get(f'/events/series/{self.series.slug}')
        self.assertEqual(response.status_code, 404)

    def test_staff_sees_drafts(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='pass', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(self.series.get_absolute_url())
        self.assertContains(response, 'Series Session 2')

    def test_event_time_localized_to_event_timezone(self):
        """Issue #867: a 16:00-UTC event with Europe/Berlin must render as
        18:00 (CEST, +02:00 in summer), not the raw 16:00 UTC clock time.
        """
        Event.objects.create(
            title='Berlin Office Hours', slug='berlin-office-hours',
            start_datetime=datetime(2026, 6, 15, 16, 0, tzinfo=UTC),
            timezone='Europe/Berlin',
            status='upcoming',
            event_series=self.series, series_position=3, origin='studio',
        )
        response = self.client.get(self.series.get_absolute_url())
        self.assertContains(response, 'Monday, Jun 15, 2026 · 18:00 Europe/Berlin')
        # The raw UTC clock time labeled Berlin must NOT appear.
        self.assertNotContains(response, '16:00 Europe/Berlin')

    def test_canonical_and_og_url_use_absolute_series_url(self):
        response = self.client.get(self.series.get_absolute_url())
        absolute_url = (
            f'{response.context["site_url"]}{self.series.get_absolute_url()}'
        )
        self.assertContains(
            response,
            f'<link rel="canonical" href="{absolute_url}">',
            html=False,
        )
        self.assertContains(
            response,
            f'<meta property="og:url" content="{absolute_url}">',
            html=False,
        )

    def test_signed_in_event_time_uses_viewer_preferred_timezone(self):
        user = User.objects.create_user(
            email='ny-series@example.com',
            password='pass',
            preferred_timezone='America/New_York',
        )
        self.client.force_login(user)
        Event.objects.create(
            title='Viewer Local Session', slug='viewer-local-session',
            start_datetime=datetime(2026, 6, 15, 16, 0, tzinfo=UTC),
            timezone='Europe/Berlin',
            status='upcoming',
            event_series=self.series, series_position=3, origin='studio',
        )

        response = self.client.get(self.series.get_absolute_url())

        self.assertContains(
            response,
            'June 15, 2026, 12:00 America/New_York',
        )
        self.assertNotContains(response, 'Monday, Jun 15, 2026 · 18:00 Europe/Berlin')

    def test_event_detail_url_still_resolves_after_series_route(self):
        """The ``/events/series/<id>/<slug>`` route must not swallow event ids.

        Issue #673: event detail is now keyed on id+slug; the assertion
        is that ``Event.get_absolute_url`` resolves to a 200 alongside the
        series route.
        """
        response = self.client.get(self.published_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_trailing_slash_301s_to_canonical_no_slash_form(self):
        """Issue #909: ``/events/series/<id>/<slug>/`` (trailing slash) is
        normalised by the site-wide ``RemoveTrailingSlashMiddleware``
        with a 301 to the no-slash form *before* URL routing runs —
        ``events`` is not in ``SKIP_PREFIXES``. This locks in that
        behaviour so the canonical no-slash route is the only reachable
        one (the dedicated trailing-slash url pattern was dead code).
        """
        response = self.client.get(f'{self.series.get_absolute_url()}/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], self.series.get_absolute_url()
        )


class PublicEventSeriesChronologicalOrderTest(TestCase):
    """Issue #957: the public series page sorts sessions by date, not by the
    stored ``series_position``, so a series rebuilt across multiple batches
    (and never renumbered) still reads in calendar order.
    """

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Office Hours', start_time=time(18, 0),
        )
        now = timezone.now()
        # Deliberately scramble series_position relative to start_datetime:
        # the EARLIEST-dated session carries the HIGHEST position, mimicking a
        # two-batch rebuild where later-created (but earlier-dated) sessions
        # got higher positions.
        cls.s_jun24 = Event.objects.create(
            title='Office Hours — Session 4', slug='oh-session-4',
            start_datetime=now + timedelta(days=10),
            status='upcoming',
            event_series=cls.series, series_position=4, origin='studio',
        )
        cls.s_jul06 = Event.objects.create(
            title='Office Hours — Session 1', slug='oh-session-1',
            start_datetime=now + timedelta(days=22),
            status='upcoming',
            event_series=cls.series, series_position=1, origin='studio',
        )
        cls.s_jul01 = Event.objects.create(
            title='Office Hours — Session 3', slug='oh-session-3',
            start_datetime=now + timedelta(days=17),
            status='upcoming',
            event_series=cls.series, series_position=3, origin='studio',
        )
        cls.s_jun28 = Event.objects.create(
            title='Office Hours — Session 2', slug='oh-session-2',
            start_datetime=now + timedelta(days=14),
            status='upcoming',
            event_series=cls.series, series_position=2, origin='studio',
        )

    def test_sessions_render_in_start_datetime_order(self):
        response = self.client.get(self.series.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        rendered = list(response.context['events'])
        # All four published occurrences present (none dropped by the new sort).
        self.assertEqual(len(rendered), 4)
        # Strictly ascending by start_datetime regardless of series_position.
        starts = [e.start_datetime for e in rendered]
        self.assertEqual(starts, sorted(starts))
        # The earliest-dated session is first even though its position is 4.
        self.assertEqual(rendered[0].pk, self.s_jun24.pk)
        self.assertEqual(rendered[-1].pk, self.s_jul06.pk)

    def test_drafts_and_cancelled_still_hidden_after_ordering_change(self):
        now = timezone.now()
        Event.objects.create(
            title='Office Hours — Draft', slug='oh-draft',
            start_datetime=now + timedelta(days=12),
            status='draft',
            event_series=self.series, series_position=9, origin='studio',
        )
        Event.objects.create(
            title='Office Hours — Cancelled', slug='oh-cancelled',
            start_datetime=now + timedelta(days=13),
            status='cancelled',
            event_series=self.series, series_position=8, origin='studio',
        )
        response = self.client.get(self.series.get_absolute_url())
        rendered = list(response.context['events'])
        # Only the four published occurrences appear; draft + cancelled gone.
        self.assertEqual(len(rendered), 4)
        statuses = {e.status for e in rendered}
        self.assertEqual(statuses, {'upcoming'})
        # Still in start_datetime order.
        starts = [e.start_datetime for e in rendered]
        self.assertEqual(starts, sorted(starts))

    def test_renumber_still_assigns_chronological_position_and_titles(self):
        """Guard: the display sort change must not weaken the position
        numbering / auto-title logic in ``renumber_series_occurrences``.
        """
        from api.views.event_series import renumber_series_occurrences

        # Mark the auto-titled sessions so renumber rewrites their titles.
        Event.objects.filter(event_series=self.series).update(
            title_is_auto=True,
        )
        renumber_series_occurrences(self.series)

        ordered = list(
            Event.objects.filter(event_series=self.series).order_by(
                'start_datetime', 'id',
            )
        )
        for index, event in enumerate(ordered, start=1):
            event.refresh_from_db()
            self.assertEqual(event.series_position, index)
            self.assertEqual(
                event.title, f'{self.series.name} — Session {index}',
            )


class PublicEventSeriesVisibilityTest(TestCase):
    """Issue #858: empty / hidden series 404 for the public, render for staff."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff858@test.com', password='pass', is_staff=True,
        )
        # All-draft series: no published occurrences.
        cls.empty_series = EventSeries.objects.create(
            name='All Draft', slug='all-draft', start_time=time(18, 0),
        )
        cls.draft_only = Event.objects.create(
            title='Hidden Session', slug='all-draft-session-1',
            start_datetime=timezone.now() + timedelta(days=7),
            status='draft',
            event_series=cls.empty_series, series_position=1, origin='studio',
        )
        # Populated, visible series with one published occurrence.
        cls.live_series = EventSeries.objects.create(
            name='Live Series', slug='live-series-858', start_time=time(18, 0),
        )
        cls.published = Event.objects.create(
            title='Open Session', slug='live-series-858-session-1',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            event_series=cls.live_series, series_position=1, origin='studio',
        )

    def test_model_visibility_rule(self):
        self.assertFalse(self.empty_series.is_publicly_visible())
        self.assertTrue(self.live_series.is_publicly_visible())

    def test_anonymous_404s_on_empty_series(self):
        response = self.client.get(
            self.empty_series.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_never_sees_no_published_placeholder_or_draft(self):
        response = self.client.get(
            self.empty_series.get_absolute_url(),
        )
        self.assertNotContains(
            response, 'No published events', status_code=404,
        )
        self.assertNotContains(response, 'Draft', status_code=404)

    def test_staff_previews_empty_series(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.empty_series.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)

    def test_publishing_makes_series_reachable(self):
        self.draft_only.status = 'upcoming'
        self.draft_only.save()
        response = self.client.get(
            self.empty_series.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hidden Session')

    def test_is_active_false_404s_even_with_published_events(self):
        self.live_series.is_active = False
        self.live_series.save()
        response = self.client.get(
            self.live_series.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 404)

    def test_is_active_false_still_renders_for_staff(self):
        self.live_series.is_active = False
        self.live_series.save()
        self.client.force_login(self.staff)
        response = self.client.get(
            self.live_series.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)

    def test_public_series_page_never_shows_draft_word(self):
        # A series with one published and one draft occurrence.
        draft = Event.objects.create(
            title='Second Session', slug='live-series-858-session-2',
            start_datetime=timezone.now() + timedelta(days=14),
            status='draft',
            event_series=self.live_series, series_position=2, origin='studio',
        )
        response = self.client.get(
            self.live_series.get_absolute_url(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Session')
        self.assertNotContains(response, draft.title)
        self.assertNotContains(response, 'Draft')


class PublicEventSeriesBannerTest(TestCase):
    """Public series pages keep banner URLs in metadata, not the body."""

    BANNER = 'https://cdn.example.com/banners/event_series/7-abc.jpg'

    @classmethod
    def setUpTestData(cls):
        cls.with_banner = EventSeries.objects.create(
            name='Banner Series', slug='banner-series',
            start_time=time(18, 0),
            description='Weekly office hours for shipping AI agents.',
            auto_banner_url=cls.BANNER,
        )
        cls.no_banner = EventSeries.objects.create(
            name='Plain Series', slug='plain-series',
            start_time=time(18, 0),
        )
        # Issue #858: each series needs a published occurrence so the public
        # page renders (an empty series 404s for non-staff).
        for idx, series in enumerate((cls.with_banner, cls.no_banner)):
            Event.objects.create(
                title=f'Banner Test Session {idx}',
                slug=f'{series.slug}-session-1',
                start_datetime=timezone.now() + timedelta(days=7),
                status='upcoming',
                event_series=series, series_position=1, origin='studio',
            )

    def test_body_banner_image_not_rendered_when_set(self):
        response = self.client.get(self.with_banner.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="series-banner"')
        self.assertNotRegex(
            response.content.decode(),
            rf'<img[^>]+src="{re.escape(self.BANNER)}"',
        )

    def test_no_header_banner_box_when_unset(self):
        response = self.client.get(self.no_banner.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="series-banner"')

    def test_text_header_registration_and_events_keep_order(self):
        response = self.client.get(self.with_banner.get_absolute_url())
        html = response.content.decode()
        self.assertContains(response, 'data-testid="series-name"')
        self.assertContains(response, 'data-testid="series-cadence"')
        self.assertContains(response, 'Weekly office hours')
        self.assertContains(response, 'data-testid="series-register-panel"')
        self.assertContains(response, 'data-testid="series-events"')
        header_idx = html.index('data-testid="series-name"')
        cadence_idx = html.index('data-testid="series-cadence"')
        description_idx = html.index('Weekly office hours')
        registration_idx = html.index('data-testid="series-register-panel"')
        events_idx = html.index('data-testid="series-events"')
        self.assertLess(header_idx, cadence_idx)
        self.assertLess(cadence_idx, description_idx)
        self.assertLess(description_idx, registration_idx)
        self.assertLess(registration_idx, events_idx)

    def test_og_image_uses_banner_when_set(self):
        response = self.client.get(self.with_banner.get_absolute_url())
        self.assertContains(
            response,
            f'<meta property="og:image" content="{self.BANNER}">',
            html=False,
        )
        self.assertContains(
            response,
            f'<meta name="twitter:image" content="{self.BANNER}">',
            html=False,
        )

    def test_og_title_reflects_series_name(self):
        response = self.client.get(self.with_banner.get_absolute_url())
        self.assertContains(
            response,
            '<meta property="og:title" content="Banner Series">',
            html=False,
        )

    def test_og_image_falls_back_to_site_default_when_unset(self):
        response = self.client.get(self.no_banner.get_absolute_url())
        self.assertContains(response, 'ai-shipping-labs.jpg')
        self.assertNotContains(response, '/banners/event_series/')

    def test_dev_comment_not_leaked_into_head(self):
        # Issue #946: the #896 OG-image note was a multi-line {# #} comment,
        # which Django treats as single-line — leaking lines 2+ as literal
        # text into <head>. It is now a {% comment %} block; the distinctive
        # phrase must not appear in the rendered page.
        response = self.client.get(self.with_banner.get_absolute_url())
        self.assertNotContains(response, 'falling back to the site default')


class UpcomingSeriesCardCadenceTest(TestCase):
    """Issue #947: the /events grouped series card renders the honest
    ``schedule_label`` for its cadence clause, not the old first-occurrence
    weekly claim. A regular series keeps the byte-identical weekly phrasing;
    an irregular series shows the neutral session summary and never the
    literal ``Weekly on``. The ``· N upcoming session(s)`` suffix is preserved.
    """

    @staticmethod
    def _occurrence(series, local_dt, position, tz='Europe/Berlin'):
        aware = local_dt.replace(tzinfo=zoneinfo.ZoneInfo(tz))
        return Event.objects.create(
            title=f'{series.name} Session {position}',
            slug=f'{series.slug}-session-{position}',
            start_datetime=aware,
            timezone=tz,
            status='upcoming',
            event_series=series,
            series_position=position,
            origin='studio',
        )

    @classmethod
    def setUpTestData(cls):
        # All occurrences are anchored to genuinely-future dates relative to the
        # current run so they never go stale: a hardcoded calendar fixture
        # silently flips a session into the past once that date arrives, which
        # drops the "· N upcoming sessions" suffix (issue #947 regression).
        tz = zoneinfo.ZoneInfo('Europe/Berlin')
        today = timezone.now().astimezone(tz).date()

        cls.regular = EventSeries.objects.create(
            name='Regular Office Hours', slug='regular-oh',
            day_of_week=2,  # Wednesday
            start_time=time(18, 0), timezone='Europe/Berlin',
        )
        # First Wednesday strictly in the future, then two more weekly, all at
        # 18:00 so the genuine weekly cadence label stays honest.
        days_until_wed = (2 - today.weekday()) % 7 or 7
        first_wed = today + timedelta(days=days_until_wed)
        for i in range(3):
            wed = first_wed + timedelta(weeks=i)
            cls._occurrence(
                cls.regular,
                datetime(wed.year, wed.month, wed.day, 18, 0),
                position=i + 1,
            )

        cls.irregular = EventSeries.objects.create(
            name='Irregular Workshop', slug='irregular-ws',
            day_of_week=2,  # Wednesday (claimed) — occurrences drift
            start_time=time(18, 0), timezone='Europe/Berlin',
        )
        # Irregular spacing (+3, +12, +17 days) so the series is not a true
        # weekly cadence and the first session is comfortably in the future.
        cls.irregular_dates = [
            today + timedelta(days=offset) for offset in (3, 12, 17)
        ]
        for i, d in enumerate(cls.irregular_dates):
            cls._occurrence(
                cls.irregular,
                datetime(d.year, d.month, d.day, 18, 0),
                position=i + 1,
            )
        # Expected range label, computed from the same dates the model formats.
        cls.irregular_range = (
            f'{cls.irregular_dates[0].strftime("%b %d, %Y")} '
            f'– {cls.irregular_dates[-1].strftime("%b %d, %Y")}'
        )

    def _meta(self, response, series_slug):
        """Return the text inside the series-card-meta paragraph for a card."""
        html = response.content.decode()
        # Isolate the card for this series, then its meta paragraph.
        card_marker = f'data-series-slug="{series_slug}"'
        start = html.index(card_marker)
        segment = html[start:start + 4000]
        match = re.search(
            r'data-testid="series-card-meta"[^>]*>(.*?)</p>',
            segment, re.DOTALL,
        )
        self.assertIsNotNone(match, 'series-card-meta paragraph not found')
        return ' '.join(match.group(1).split())

    def test_regular_series_card_shows_weekly_label_and_suffix(self):
        response = self.client.get('/events')
        meta = self._meta(response, 'regular-oh')
        self.assertEqual(
            meta,
            'Weekly on Wednesday at 18:00 Europe/Berlin '
            '· 3 upcoming sessions',
        )

    def test_irregular_series_card_shows_session_summary_not_weekly(self):
        response = self.client.get('/events')
        meta = self._meta(response, 'irregular-ws')
        self.assertEqual(
            meta,
            f'3 sessions · {self.irregular_range} '
            '· 3 upcoming sessions',
        )
        self.assertNotIn('Weekly on', meta)

    def test_irregular_card_meta_matches_schedule_label(self):
        response = self.client.get('/events')
        meta = self._meta(response, 'irregular-ws')
        self.assertTrue(meta.startswith(self.irregular.schedule_label))


class PublicEventsListSeriesLinkTest(TestCase):
    """Public events listing surfaces a series link for series-linked events."""

    @classmethod
    def setUpTestData(cls):
        cls.series = EventSeries.objects.create(
            name='Grouped Series', slug='grouped-series',
            start_time=time(18, 0),
        )
        cls.grouped = Event.objects.create(
            title='Grouped Event', slug='grouped-event',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            status='upcoming',
            event_series=cls.series, series_position=1, origin='studio',
        )
        cls.standalone = Event.objects.create(
            title='Standalone Event', slug='standalone-event',
            start_datetime=timezone.now() + timezone.timedelta(days=1),
            status='upcoming',
            origin='studio',
        )

    def test_grouped_event_has_series_link(self):
        response = self.client.get('/events?filter=upcoming')
        self.assertContains(response, 'Series: Grouped Series')
        self.assertContains(response, self.series.get_absolute_url())
        self.assertNotContains(response, '/events/groups/grouped-series')

    def test_standalone_event_has_no_series_link(self):
        response = self.client.get('/events?filter=upcoming')
        # The standalone event title is present but no "Series: " label
        # is rendered for it.
        self.assertContains(response, 'Standalone Event')
        # The total "Series: " occurrences must equal the number of
        # series-linked events on the page (1).
        self.assertEqual(
            response.content.decode().count('Series:'), 1,
        )
