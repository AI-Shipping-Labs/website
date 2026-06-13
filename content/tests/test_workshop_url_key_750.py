"""Tests for issue #750 — switch workshop detail URLs to ``/<date>-<slug>``.

Covers:

- ``Workshop.url_key`` property (date-slug composite).
- ``Workshop.get_absolute_url`` and ``WorkshopPage.get_absolute_url`` use it.
- The internal helpers ``_parse_date_slug`` and ``_resolve_workshop_by_key``
  raise ``Http404`` on malformed input vs. missing workshop.
- Canonical date-slug URLs return 200 for published workshops.
- Issue #915: the legacy slug-only routes were removed — a bare slug
  URL (landing, video, tutorial) now returns 404 with no ``Location``
  header instead of 301-redirecting to the canonical URL.
- Malformed URLs (bad date prefix, no date prefix + missing slug) return 404.
- ``reverse('workshop_detail', kwargs={'date_slug': ...})`` succeeds;
  passing ``slug=...`` raises NoReverseMatch.
- Sitemap emits the canonical date-slug URLs only — no slug-only shape.
"""

from datetime import date

from django.http import Http404
from django.test import SimpleTestCase, TestCase
from django.urls import NoReverseMatch, reverse

from content.models import Workshop, WorkshopPage
from content.views.workshops import (
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
        # A bare slug (the shape that lived in old emails) does not match
        # the date-slug regex and must 404. Issue #915 removed the legacy
        # slug-only fallback, so there is nothing else for it to match.
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
        # route doesn't match. Issue #915 removed the legacy slug-only
        # fallback, so the request 404s.
        response = self.client.get('/workshops/totally-made-up-slug')
        self.assertEqual(response.status_code, 404)

    def test_bare_slug_matching_published_workshop_now_404s(self):
        # Issue #915: a bare slug that DOES match a published workshop
        # used to 301 to the canonical date-slug URL. Now it 404s with
        # no ``Location`` header — the legacy redirect was removed.
        for path in (
            '/workshops/build-it',
            '/workshops/build-it/video',
            '/workshops/build-it/tutorial/intro',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response)


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
