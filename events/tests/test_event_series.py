"""Tests for the EventSeries model and origin invariant on Event.

Issue #564 (renamed from EventGroup in #575).

Covers:
- ``EventSeries`` model: slug auto-derivation, description markdown.
- ``Event.origin`` invariant: github iff source_repo is set.
- ``studio.utils.is_synced`` branching on ``origin``.
- Public ``/events/groups/<slug>`` view (URL kept for back-compat).
- Public events list shows series link when an event belongs to a series.
"""

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
    """Public ``/events/groups/<slug>`` page."""

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
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Series Session 1')

    def test_anonymous_visitor_does_not_see_drafts(self):
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertNotContains(response, 'Series Session 2')

    def test_unknown_slug_returns_404(self):
        response = self.client.get('/events/groups/does-not-exist')
        self.assertEqual(response.status_code, 404)

    def test_staff_sees_drafts(self):
        staff = User.objects.create_user(
            email='staff@test.com', password='pass', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get(f'/events/groups/{self.series.slug}')
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
        response = self.client.get(f'/events/groups/{self.series.slug}')
        self.assertContains(response, 'Monday, Jun 15, 2026 · 18:00 Europe/Berlin')
        # The raw UTC clock time labeled Berlin must NOT appear.
        self.assertNotContains(response, '16:00 Europe/Berlin')

    def test_event_detail_url_still_resolves_after_groups_route(self):
        """The ``/events/groups/<slug>`` route must not swallow event ids.

        Issue #673: event detail is now keyed on id+slug; the assertion
        is that ``Event.get_absolute_url`` resolves to a 200 alongside
        the existing groups route.
        """
        response = self.client.get(self.published_event.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_trailing_slash_301s_to_canonical_no_slash_form(self):
        """Issue #909: ``/events/groups/<slug>/`` (trailing slash) is
        normalised by the site-wide ``RemoveTrailingSlashMiddleware``
        with a 301 to the no-slash form *before* URL routing runs —
        ``events`` is not in ``SKIP_PREFIXES``. This locks in that
        behaviour so the canonical no-slash route is the only reachable
        one (the dedicated trailing-slash url pattern was dead code).
        """
        response = self.client.get(f'/events/groups/{self.series.slug}/')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], f'/events/groups/{self.series.slug}'
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
            f'/events/groups/{self.empty_series.slug}',
        )
        self.assertEqual(response.status_code, 404)

    def test_anonymous_never_sees_no_published_placeholder_or_draft(self):
        response = self.client.get(
            f'/events/groups/{self.empty_series.slug}',
        )
        self.assertNotContains(
            response, 'No published events', status_code=404,
        )
        self.assertNotContains(response, 'Draft', status_code=404)

    def test_staff_previews_empty_series(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/events/groups/{self.empty_series.slug}',
        )
        self.assertEqual(response.status_code, 200)

    def test_publishing_makes_series_reachable(self):
        self.draft_only.status = 'upcoming'
        self.draft_only.save()
        response = self.client.get(
            f'/events/groups/{self.empty_series.slug}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hidden Session')

    def test_is_active_false_404s_even_with_published_events(self):
        self.live_series.is_active = False
        self.live_series.save()
        response = self.client.get(
            f'/events/groups/{self.live_series.slug}',
        )
        self.assertEqual(response.status_code, 404)

    def test_is_active_false_still_renders_for_staff(self):
        self.live_series.is_active = False
        self.live_series.save()
        self.client.force_login(self.staff)
        response = self.client.get(
            f'/events/groups/{self.live_series.slug}',
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
            f'/events/groups/{self.live_series.slug}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Open Session')
        self.assertNotContains(response, draft.title)
        self.assertNotContains(response, 'Draft')


class PublicEventSeriesBannerTest(TestCase):
    """Issue #896: the public series page surfaces ``auto_banner_url``."""

    BANNER = 'https://cdn.example.com/banners/event_series/7-abc.jpg'

    @classmethod
    def setUpTestData(cls):
        cls.with_banner = EventSeries.objects.create(
            name='Banner Series', slug='banner-series',
            start_time=time(18, 0),
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

    def test_header_banner_image_rendered_when_set(self):
        response = self.client.get(f'/events/groups/{self.with_banner.slug}')
        self.assertContains(response, 'data-testid="series-banner"')
        self.assertContains(response, self.BANNER)

    def test_no_header_banner_box_when_unset(self):
        response = self.client.get(f'/events/groups/{self.no_banner.slug}')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="series-banner"')

    def test_og_image_uses_banner_when_set(self):
        response = self.client.get(f'/events/groups/{self.with_banner.slug}')
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
        response = self.client.get(f'/events/groups/{self.with_banner.slug}')
        self.assertContains(
            response,
            '<meta property="og:title" content="Banner Series">',
            html=False,
        )

    def test_og_image_falls_back_to_site_default_when_unset(self):
        response = self.client.get(f'/events/groups/{self.no_banner.slug}')
        self.assertContains(response, 'ai-shipping-labs.jpg')
        self.assertNotContains(response, '/banners/event_series/')


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
        self.assertContains(response, '/events/groups/grouped-series')

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
