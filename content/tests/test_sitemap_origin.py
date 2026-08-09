"""Regression coverage for the canonical sitemap origin (issue #1377)."""

import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import urlsplit

from django.contrib.sites.models import Site
from django.test import TestCase, override_settings, tag

from content.models import Article
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


def _sitemap_urls(response):
    root = ET.fromstring(response.content)
    return root.findall('.//{*}url')


def _locations(response):
    return [
        url.find('{*}loc').text
        for url in _sitemap_urls(response)
    ]


@tag('core')
@override_settings(
    SITE_BASE_URL='https://env.example.test',
    ALLOWED_HOSTS=['alias.example.test', 'testserver'],
)
class SitemapCanonicalOriginTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Article.objects.create(
            title='Canonical sitemap article',
            slug='canonical-sitemap-article',
            content_markdown='# Canonical sitemap article',
            date=date(2026, 8, 9),
            published=True,
            required_level=0,
        )
        site, _ = Site.objects.get_or_create(pk=1)
        site.domain = 'example.com'
        site.name = 'Stale test site'
        site.save(update_fields=['domain', 'name'])

    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_response_contract_and_settings_fallback(self):
        response = self.client.get(
            '/sitemap.xml',
            secure=False,
            HTTP_HOST='alias.example.test',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/xml')
        self.assertEqual(
            response['X-Robots-Tag'],
            'noindex, noodp, noarchive',
        )
        locations = _locations(response)
        self.assertTrue(locations)
        self.assertTrue(
            all(
                urlsplit(location)[:2] == ('https', 'env.example.test')
                for location in locations
            ),
        )
        self.assertNotContains(response, 'example.com')
        self.assertNotContains(response, 'alias.example.test')

    def test_db_override_is_used_after_config_cache_invalidation(self):
        before = self.client.get('/sitemap.xml')
        self.assertTrue(
            all(
                urlsplit(location)[:2] == ('https', 'env.example.test')
                for location in _locations(before)
            ),
        )

        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='http://studio.example.test:8042/',
            group='site',
        )
        clear_config_cache()

        after = self.client.get('/sitemap.xml')
        self.assertTrue(
            all(
                urlsplit(location)[:2]
                == ('http', 'studio.example.test:8042')
                for location in _locations(after)
            ),
        )
        self.assertNotContains(after, 'env.example.test')
        self.assertNotContains(after, 'example.com')

    @override_settings(SITE_BASE_URL='http://127.0.0.1:8765/')
    def test_trailing_slash_keeps_paths_and_root_shape(self):
        clear_config_cache()
        response = self.client.get('/sitemap.xml')
        locations = _locations(response)

        self.assertIn('http://127.0.0.1:8765/', locations)
        self.assertIn(
            'http://127.0.0.1:8765/blog/canonical-sitemap-article',
            locations,
        )
        self.assertFalse(
            any('127.0.0.1:8765//' in location for location in locations),
        )

    def test_origin_change_preserves_membership_paths_and_metadata(self):
        first = self.client.get('/sitemap.xml')

        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://dev.aishippinglabs.com/',
            group='site',
        )
        clear_config_cache()
        second = self.client.get('/sitemap.xml')

        def normalized_entries(response):
            entries = {}
            for url in _sitemap_urls(response):
                location = url.find('{*}loc').text
                path = urlsplit(location).path
                metadata = tuple(
                    (child.tag.rsplit('}', 1)[-1], child.text)
                    for child in url
                    if not child.tag.endswith('loc')
                )
                entries[path] = metadata
            return entries

        first_entries = normalized_entries(first)
        second_entries = normalized_entries(second)
        self.assertEqual(first_entries, second_entries)
        self.assertIn('/blog/canonical-sitemap-article', first_entries)
        self.assertIn(
            ('changefreq', 'weekly'),
            first_entries['/blog/canonical-sitemap-article'],
        )
        self.assertIn(
            ('priority', '0.8'),
            first_entries['/blog/canonical-sitemap-article'],
        )
