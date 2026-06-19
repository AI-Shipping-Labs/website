"""Tests for workshop URL canonicalization (#750, #915, #1064).

Covers:

- ``Workshop.url_key`` property (slug-only canonical key).
- ``Workshop.get_absolute_url`` and ``WorkshopPage.get_absolute_url`` use it.
- The internal helpers ``_parse_date_slug`` and ``_resolve_workshop_by_key``
  raise ``Http404`` on malformed input vs. missing workshop.
- Canonical slug-only URLs return 200 for published workshops.
- Valid dated legacy URLs 301 to slug-only URLs with query strings preserved.
- Malformed, unknown, draft, and date-mismatched dated URLs return 404.
- ``reverse('workshop_detail', kwargs={'slug': ...})`` uses slug-only paths.
- Sitemap emits slug-only URLs and omits dated workshop loc entries.
"""

from datetime import date

from django.http import Http404
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from content.models import Workshop, WorkshopPage
from content.views.workshops import (
    _parse_date_slug,
    _resolve_workshop_by_key,
)


class WorkshopUrlKeyPropertyTest(SimpleTestCase):
    """``Workshop.url_key`` returns the canonical slug-only key."""

    def test_url_key_returns_slug(self):
        ws = Workshop(slug='build-it', date=date(2026, 5, 14))
        self.assertEqual(ws.url_key, 'build-it')

    def test_get_absolute_url_uses_url_key(self):
        ws = Workshop(slug='build-it', date=date(2026, 5, 14))
        self.assertEqual(ws.get_absolute_url(), '/workshops/build-it')


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
            '/workshops/pg-ws/tutorial/intro',
        )


class ParseDateSlugTest(SimpleTestCase):
    """``_parse_date_slug`` accepts ``YYYY-MM-DD-<slug>`` and rejects the rest."""

    def test_valid_date_slug_returns_date_and_slug(self):
        parsed_date, slug = _parse_date_slug('2026-05-14-build-it')
        self.assertEqual(parsed_date, date(2026, 5, 14))
        self.assertEqual(slug, 'build-it')

    def test_no_date_prefix_raises_http404(self):
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
    """End-to-end: canonical slug-only URLs return 200 for published rows."""

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
        response = self.client.get('/workshops/build-it')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Build It')

    def test_canonical_video_returns_200(self):
        response = self.client.get('/workshops/build-it/video')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Build It')

    def test_canonical_tutorial_returns_200(self):
        response = self.client.get('/workshops/build-it/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Intro')

    def test_malformed_bad_date_returns_404(self):
        response = self.client.get('/workshops/9999-99-99-bad-date')
        self.assertEqual(response.status_code, 404)

    def test_unknown_slug_returns_404(self):
        response = self.client.get('/workshops/totally-made-up-slug')
        self.assertEqual(response.status_code, 404)

    def test_valid_dated_landing_redirects_to_slug_only(self):
        response = self.client.get('/workshops/2026-05-14-build-it')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/workshops/build-it')

    def test_valid_dated_video_redirects_and_preserves_query(self):
        response = self.client.get('/workshops/2026-05-14-build-it/video?t=300')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/workshops/build-it/video?t=300')

    def test_valid_dated_tutorial_redirects_to_slug_only(self):
        response = self.client.get(
            '/workshops/2026-05-14-build-it/tutorial/intro?utm=x',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/workshops/build-it/tutorial/intro?utm=x',
        )

    def test_date_slug_mismatch_returns_404(self):
        response = self.client.get('/workshops/2026-05-15-build-it')
        self.assertEqual(response.status_code, 404)


class ReverseWorkshopUrlTest(TestCase):
    """``reverse`` succeeds with the slug-only canonical route."""

    def test_reverse_workshop_detail_with_slug(self):
        ws = Workshop.objects.create(
            slug='build',
            title='Build',
            date=date(2026, 5, 14),
            status='published',
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
        )
        url = reverse('workshop_detail', kwargs={'slug': ws.url_key})
        self.assertEqual(url, '/workshops/build')

    def test_reverse_workshop_video_with_slug(self):
        url = reverse(
            'workshop_video',
            kwargs={'slug': 'build'},
        )
        self.assertEqual(url, '/workshops/build/video')

    def test_reverse_workshop_page_detail_with_slug(self):
        url = reverse(
            'workshop_page_detail',
            kwargs={
                'slug': 'build', 'page_slug': 'intro',
            },
        )
        self.assertEqual(url, '/workshops/build/tutorial/intro')


class SitemapEmitsSlugOnlyUrlsTest(TestCase):
    """The sitemap emits the canonical slug-only URL shape only."""

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
        self.assertContains(response, '/workshops/sit-ws')

    def test_sitemap_contains_canonical_tutorial(self):
        response = self.client.get('/sitemap.xml')
        self.assertContains(response, '/workshops/sit-ws/tutorial/intro')

    def test_sitemap_omits_dated_landing(self):
        response = self.client.get('/sitemap.xml')
        self.assertNotContains(
            response,
            '<loc>https://aishippinglabs.com/workshops/2026-05-14-sit-ws</loc>',
        )
