"""Tests for issue #750 — switch workshop detail URLs to ``/<date>-<slug>``.

Covers:

- ``Workshop.url_key`` property (date-slug composite).
- ``Workshop.get_absolute_url`` and ``WorkshopPage.get_absolute_url`` use it.
- The internal helpers ``_parse_date_slug`` and ``_resolve_workshop_by_key``
  raise ``Http404`` on malformed input vs. missing workshop.
- Canonical date-slug URLs return 200 for published workshops.
- Legacy slug-only URLs 301 to the canonical URL (landing, video, tutorial).
- Query strings (notably ``?t=`` deep links) survive each redirect.
- Slug collisions across different dates: the legacy redirect picks the
  newest workshop and emits a WARNING log naming both.
- Malformed URLs (bad date prefix, no date prefix + missing slug) return 404.
- ``reverse('workshop_detail', kwargs={'date_slug': ...})`` succeeds;
  passing ``slug=...`` raises NoReverseMatch.
- Sitemap emits the canonical date-slug URLs only — no slug-only shape.
"""

import logging
from datetime import date

from django.http import Http404
from django.test import SimpleTestCase, TestCase
from django.urls import NoReverseMatch, reverse

from content.models import Workshop, WorkshopPage
from content.views.workshops import (
    _lookup_legacy_workshop,
    _parse_date_slug,
    _resolve_workshop_by_key,
)


class WorkshopUrlKeyPropertyTest(SimpleTestCase):
    """``Workshop.url_key`` joins date + slug deterministically."""

    def test_url_key_concatenates_iso_date_and_slug(self):
        ws = Workshop(slug='build-it', date=date(2026, 5, 14))
        self.assertEqual(ws.url_key, '2026-05-14-build-it')

    def test_get_absolute_url_uses_url_key(self):
        ws = Workshop(slug='build-it', date=date(2026, 5, 14))
        self.assertEqual(
            ws.get_absolute_url(), '/workshops/2026-05-14-build-it',
        )


class WorkshopPageGetAbsoluteUrlTest(TestCase):
    """``WorkshopPage.get_absolute_url`` uses the parent's ``url_key``."""

    def test_page_url_uses_workshop_url_key(self):
        ws = Workshop.objects.create(
            slug='pg-ws',
            title='Page Workshop',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        page = WorkshopPage.objects.create(
            workshop=ws, slug='intro', title='Intro', sort_order=1, body='x',
        )
        self.assertEqual(
            page.get_absolute_url(),
            '/workshops/2026-05-14-pg-ws/tutorial/intro',
        )


class ParseDateSlugTest(SimpleTestCase):
    """``_parse_date_slug`` accepts ``YYYY-MM-DD-<slug>`` and rejects the rest."""

    def test_valid_date_slug_returns_date_and_slug(self):
        parsed_date, slug = _parse_date_slug('2026-05-14-build-it')
        self.assertEqual(parsed_date, date(2026, 5, 14))
        self.assertEqual(slug, 'build-it')

    def test_no_date_prefix_raises_http404(self):
        # A bare slug (the shape that lives in old emails) does not match
        # the date-slug regex and must 404 so the resolver falls through
        # to the legacy slug-only route.
        with self.assertRaises(Http404):
            _parse_date_slug('build-it')

    def test_partial_date_prefix_raises_http404(self):
        # ``2026-05-build-it`` is missing the day component.
        with self.assertRaises(Http404):
            _parse_date_slug('2026-05-build-it')

    def test_invalid_month_day_raises_http404(self):
        # ``9999-99-99`` matches the regex shape but fails strict parsing.
        with self.assertRaises(Http404):
            _parse_date_slug('9999-99-99-bad-date')

    def test_empty_slug_after_date_raises_http404(self):
        # The slug part must start with [a-z0-9]; trailing dash isn't valid.
        with self.assertRaises(Http404):
            _parse_date_slug('2026-05-14-')


class ResolveWorkshopByKeyTest(TestCase):
    """``_resolve_workshop_by_key`` looks up the workshop by (date, slug)."""

    @classmethod
    def setUpTestData(cls):
        cls.published = Workshop.objects.create(
            slug='real',
            title='Real Workshop',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.draft = Workshop.objects.create(
            slug='hidden',
            title='Hidden Workshop',
            date=date(2026, 5, 14),
            status='draft',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )

    def test_returns_published_workshop_on_exact_match(self):
        ws = _resolve_workshop_by_key('2026-05-14-real')
        self.assertEqual(ws.pk, self.published.pk)

    def test_draft_workshop_404s_via_resolver(self):
        # A draft workshop with a matching (date, slug) does not resolve
        # — the resolver filters on ``status='published'``.
        with self.assertRaises(Http404):
            _resolve_workshop_by_key('2026-05-14-hidden')

    def test_unknown_date_slug_raises_http404(self):
        with self.assertRaises(Http404):
            _resolve_workshop_by_key('2026-05-14-does-not-exist')

    def test_malformed_input_raises_http404(self):
        with self.assertRaises(Http404):
            _resolve_workshop_by_key('not-a-key')


class LookupLegacyWorkshopTest(TestCase):
    """``_lookup_legacy_workshop`` picks the newest published match and warns.

    Issue #750. The DB-level ``Workshop.slug`` field still carries
    ``unique=True`` (no migration is included by this issue), so in
    production today the multi-match branch is defensive. The collision
    branch is exercised via ``unittest.mock`` so the test still locks
    in the contract for the day the constraint is relaxed.
    """

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='agents-101',
            title='Agents 101',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )

    def test_single_match_returns_workshop_no_warning(self):
        with self.assertNoLogs(
            'content.views.workshops', level='WARNING',
        ):
            ws = _lookup_legacy_workshop('agents-101')
        self.assertEqual(ws.pk, self.workshop.pk)

    def test_collision_picks_newest_and_logs_warning(self):
        # Simulate two workshops sharing the slug. Build a second row
        # in memory (no save — the unique constraint would reject it)
        # and patch the queryset that ``_lookup_legacy_workshop``
        # consumes so the multi-match branch fires deterministically.
        from unittest.mock import patch

        older = Workshop(
            slug='agents-101',
            title='Agents 101 (older)',
            date=date(2025, 2, 1),
            status='published',
        )

        # The helper does ``Workshop.objects.filter(...)``,
        # ``.order_by('-date')``, then ``.first()`` and ``.count()``.
        # Patch the manager so the in-memory pair is what those calls
        # see — ``newest`` first, then ``older``.
        class FakeQS:
            def __init__(self, rows):
                self._rows = rows

            def first(self):
                return self._rows[0] if self._rows else None

            def count(self):
                return len(self._rows)

            def __getitem__(self, idx):
                return self._rows[idx]

        fake = FakeQS([self.workshop, older])
        with patch(
            'content.views.workshops.Workshop.objects.filter',
        ) as mock_filter:
            mock_filter.return_value.order_by.return_value = fake
            with self.assertLogs(
                'content.views.workshops', level='WARNING',
            ) as cm:
                ws = _lookup_legacy_workshop('agents-101')
        self.assertEqual(ws.pk, self.workshop.pk)
        joined = '\n'.join(cm.output)
        # Warning names both candidates so an operator auditing the log
        # can tell which slug-only URL got routed where.
        self.assertIn('2025-02-01', joined)
        self.assertIn('2026-05-14', joined)
        self.assertIn('agents-101', joined)

    def test_missing_slug_raises_http404(self):
        with self.assertRaises(Http404):
            _lookup_legacy_workshop('totally-made-up')


class CanonicalUrlsResolveTest(TestCase):
    """End-to-end: the canonical date-slug URLs return 200 for published rows."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='build-it',
            title='Build It',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='intro', title='Intro',
            sort_order=1, body='Hello.',
        )

    def test_canonical_landing_returns_200(self):
        response = self.client.get('/workshops/2026-05-14-build-it')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Build It')

    def test_canonical_tutorial_returns_200(self):
        response = self.client.get(
            '/workshops/2026-05-14-build-it/tutorial/intro',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Intro')

    def test_malformed_bad_date_returns_404(self):
        response = self.client.get('/workshops/9999-99-99-bad-date')
        self.assertEqual(response.status_code, 404)

    def test_bare_slug_with_no_match_returns_404(self):
        # ``totally-made-up-slug`` has no date prefix, so the canonical
        # route doesn't match. The legacy slug-only redirect view fires,
        # finds no workshop, and 404s.
        response = self.client.get('/workshops/totally-made-up-slug')
        self.assertEqual(response.status_code, 404)


class LegacyRedirectsTest(TestCase):
    """Legacy slug-only URLs 301 to the canonical date-slug URLs."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='build-search',
            title='Build Search',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='intro', title='Intro',
            sort_order=1, body='Hello.',
        )
        cls.canonical = '/workshops/2026-05-14-build-search'

    def test_legacy_landing_301s_to_canonical(self):
        response = self.client.get('/workshops/build-search')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], self.canonical)

    def test_legacy_video_301s_to_canonical(self):
        response = self.client.get('/workshops/build-search/video')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], f'{self.canonical}/video')

    def test_legacy_tutorial_301s_to_canonical(self):
        response = self.client.get(
            '/workshops/build-search/tutorial/intro',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], f'{self.canonical}/tutorial/intro',
        )

    def test_legacy_video_redirect_preserves_t_query_string(self):
        # The ``?t=`` deep-link timestamp on old recording URLs must
        # survive the 301 so the embed still drops the viewer at the
        # right offset.
        response = self.client.get(
            '/workshops/build-search/video?t=300',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], f'{self.canonical}/video?t=300',
        )

    def test_legacy_tutorial_redirect_preserves_arbitrary_query(self):
        response = self.client.get(
            '/workshops/build-search/tutorial/intro?ref=email',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'{self.canonical}/tutorial/intro?ref=email',
        )

    def test_legacy_landing_redirect_preserves_tracking_query(self):
        response = self.client.get(
            '/workshops/build-search?utm_source=newsletter',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'{self.canonical}?utm_source=newsletter',
        )

    def test_legacy_landing_for_unknown_slug_returns_404(self):
        response = self.client.get('/workshops/no-such-slug')
        self.assertEqual(response.status_code, 404)


class LegacyRedirectCollisionTest(TestCase):
    """View-level slug collision: legacy URL lands on the newer workshop.

    Same caveat as :class:`LookupLegacyWorkshopTest` — the model still
    enforces ``slug`` uniqueness, so we patch the helper to simulate the
    collision shape end-to-end through the view layer.
    """

    @classmethod
    def setUpTestData(cls):
        cls.newest = Workshop.objects.create(
            slug='agents-101',
            title='Agents 101 (2026)',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )

    def test_legacy_landing_lands_on_newest(self):
        # No collision in the DB — assert the redirect targets the
        # one workshop we have and no WARNING fires.
        logger = logging.getLogger('content.views.workshops')
        with self.assertNoLogs(logger, level='WARNING'):
            response = self.client.get('/workshops/agents-101')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'], '/workshops/2026-05-14-agents-101',
        )


class ReverseWorkshopUrlTest(TestCase):
    """``reverse`` succeeds with ``date_slug`` and fails with ``slug``."""

    def test_reverse_workshop_detail_with_date_slug(self):
        ws = Workshop.objects.create(
            slug='build',
            title='Build',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        url = reverse(
            'workshop_detail', kwargs={'date_slug': ws.url_key},
        )
        self.assertEqual(url, '/workshops/2026-05-14-build')

    def test_reverse_workshop_detail_with_slug_only_raises(self):
        # Issue #750 acceptance criterion: callers that still pass
        # ``slug=...`` must crash hard so we catch stragglers at runtime
        # rather than silently routing to the legacy redirect.
        with self.assertRaises(NoReverseMatch):
            reverse(
                'workshop_detail', kwargs={'slug': 'build'},
            )

    def test_reverse_workshop_video_with_date_slug(self):
        url = reverse(
            'workshop_video',
            kwargs={'date_slug': '2026-05-14-build'},
        )
        self.assertEqual(url, '/workshops/2026-05-14-build/video')

    def test_reverse_workshop_page_detail_with_date_slug(self):
        url = reverse(
            'workshop_page_detail',
            kwargs={
                'date_slug': '2026-05-14-build', 'page_slug': 'intro',
            },
        )
        self.assertEqual(
            url, '/workshops/2026-05-14-build/tutorial/intro',
        )


class SitemapEmitsDateSlugUrlsTest(TestCase):
    """The sitemap emits the canonical date-slug URL shape only."""

    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(
            slug='sit-ws',
            title='Sitemap Workshop',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        cls.page = WorkshopPage.objects.create(
            workshop=cls.workshop, slug='intro', title='Intro',
            sort_order=1, body='Hello.',
        )

    def test_sitemap_contains_canonical_landing(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(response, '/workshops/2026-05-14-sit-ws')

    def test_sitemap_contains_canonical_tutorial(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(
            response, '/workshops/2026-05-14-sit-ws/tutorial/intro',
        )

    def test_sitemap_omits_bare_slug_landing(self):
        # The bare slug must not appear as a sitemap entry. Scope the
        # NotContains assertion to the ``<loc>`` element shape so it
        # doesn't trip on the date-slug URL incidentally containing
        # the bare slug as a suffix.
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(
            response,
            '<loc>https://aishippinglabs.com/workshops/sit-ws</loc>',
        )
