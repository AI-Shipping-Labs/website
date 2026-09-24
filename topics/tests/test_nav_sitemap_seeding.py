"""Nav availability, sitemap inclusion, and source seeding for topics.

The topics wiki is an open surface (#1804): the Topics nav entry exists
only while published pages do, and /topics/ plus every published page
must appear in sitemap.xml.
"""

import re
import xml.etree.ElementTree as ET
from io import StringIO

from community_base.content_sync.models import (
    ContentSource as PackageContentSource,
)
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from topics.models import STATUS_DRAFT, TopicPage

SITEMAP_NS = '{http://www.sitemaps.org/schemas/sitemap/0.9}'


class TopicsNavAvailabilityTest(TestCase):
    """The cached Topics nav flag follows published TopicPage rows."""

    def test_flag_defaults_to_false_without_pages(self):
        from content.nav_availability import has_published_topics_for_nav

        self.assertFalse(has_published_topics_for_nav())

    def test_flag_true_only_while_published_pages_exist(self):
        from content.nav_availability import (
            has_published_topics_for_nav,
            refresh_topics_nav_cache,
        )

        TopicPage.objects.create(slug='rag', title='RAG', body='The pipeline.')
        self.assertTrue(refresh_topics_nav_cache())
        self.assertTrue(has_published_topics_for_nav())

        TopicPage.objects.update(status=STATUS_DRAFT)
        self.assertFalse(refresh_topics_nav_cache())
        self.assertFalse(has_published_topics_for_nav())

    def test_flag_ignores_draft_pages(self):
        from content.nav_availability import refresh_topics_nav_cache

        TopicPage.objects.create(
            slug='ghost',
            title='Ghost',
            body='Draft.',
            status=STATUS_DRAFT,
        )
        self.assertFalse(refresh_topics_nav_cache())


class TopicsPrimaryNavTest(TestCase):
    """The header exposes a Topics entry through the availability flag."""

    def test_topics_entry_present_when_available(self):
        from website.context_processors import _build_primary_nav

        nav = _build_primary_nav(
            {}, False, {}, topics_available=True,
        )
        learning = next(
            group for group in nav if group['key'] == 'learning'
        )
        topics_items = [
            item for item in learning['items'] if item['slug'] == 'topics'
        ]
        self.assertEqual(len(topics_items), 1)
        self.assertEqual(topics_items[0]['label'], 'Topics')
        self.assertEqual(topics_items[0]['href'], '/topics/')

    def test_topics_entry_absent_when_unavailable(self):
        from website.context_processors import _build_primary_nav

        nav = _build_primary_nav({}, False, {}, topics_available=False)
        learning = next(
            group for group in nav if group['key'] == 'learning'
        )
        self.assertFalse(
            [item for item in learning['items'] if item['slug'] == 'topics'],
        )


class TopicsSitemapInclusionTest(TestCase):
    """The open topics wiki is a sitemap member (#1804)."""

    @classmethod
    def setUpTestData(cls):
        cls.rag = TopicPage.objects.create(
            slug='rag', title='RAG', body='The pipeline.',
        )
        TopicPage.objects.create(
            slug='index',
            title='AISL Wiki',
            body='Hub body.',
        )
        TopicPage.objects.create(
            slug='evaluation',
            title='Evaluation',
            body='Draft.',
            status=STATUS_DRAFT,
        )

    def _sitemap_entries(self):
        """Parse /sitemap.xml into {path: lastmod-text}."""
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        root = ET.fromstring(response.content)
        entries = {}
        for url in root.findall(f'{SITEMAP_NS}url'):
            loc = url.findtext(f'{SITEMAP_NS}loc') or ''
            path = re.sub(r'^https?://[^/]+', '', loc)
            entries[path] = url.findtext(f'{SITEMAP_NS}lastmod') or ''
        return entries

    def test_sitemap_lists_hub_and_published_pages(self):
        entries = self._sitemap_entries()
        self.assertIn('/topics/', entries)
        self.assertIn('/topics/rag/', entries)

    def test_sitemap_lastmod_comes_from_updated_at(self):
        entries = self._sitemap_entries()
        expected = timezone.localdate(self.rag.updated_at).isoformat()
        self.assertTrue(
            entries['/topics/rag/'].startswith(expected),
            f'lastmod {entries["/topics/rag/"]!r} does not match '
            f'updated_at date {expected!r}',
        )

    def test_sitemap_excludes_draft_pages(self):
        entries = self._sitemap_entries()
        self.assertNotIn('/topics/evaluation/', entries)

    def test_content_sitemaps_dict_has_topics_section(self):
        from content.sitemaps import TopicsSitemap, sitemaps

        self.assertIs(sitemaps['topics'], TopicsSitemap)


class WikiSourceSeedingTest(TestCase):
    """The private wiki repo is a seeded package content source (#1688)."""

    def _seed(self):
        call_command('seed_content_sources', stdout=StringIO())

    def test_wiki_source_seeded_private_with_high_file_limit(self):
        self._seed()
        source = PackageContentSource.objects.get(
            repo_name='AI-Shipping-Labs/wiki',
        )
        self.assertEqual(source.slug, 'wiki')
        self.assertTrue(source.is_private)
        self.assertEqual(source.max_files, 5000)

    def test_wiki_source_created_disabled_without_secret(self):
        self._seed()
        source = PackageContentSource.objects.get(
            repo_name='AI-Shipping-Labs/wiki',
        )
        self.assertEqual(source.webhook_secret, '')
        self.assertFalse(source.is_enabled)

    def test_seeding_is_idempotent(self):
        self._seed()
        self._seed()
        self.assertEqual(
            PackageContentSource.objects.filter(
                repo_name='AI-Shipping-Labs/wiki',
            ).count(),
            1,
        )
