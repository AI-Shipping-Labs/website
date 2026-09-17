"""Nav availability, sitemap exclusion, and source seeding for topics (#1688).

Member topics are a gated surface: the Topics nav entry exists only while
published pages do, and /topics/ must never appear in sitemap.xml.
"""

from io import StringIO

from community_base.content_sync.models import (
    ContentSource as PackageContentSource,
)
from django.core.management import call_command
from django.test import TestCase

from topics.models import STATUS_DRAFT, TopicPage


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


class TopicsSitemapExclusionTest(TestCase):
    """Member topics stay out of sitemap.xml (gated, no SEO surface)."""

    @classmethod
    def setUpTestData(cls):
        TopicPage.objects.create(slug='rag', title='RAG', body='The pipeline.')
        TopicPage.objects.create(
            slug='index',
            title='AISL Wiki',
            body='Hub body.',
        )

    def test_no_topics_urls_in_sitemap(self):
        response = self.client.get('/sitemap.xml')
        # assertNotContains pins the 200 alongside the contract itself.
        self.assertNotContains(response, '/topics/')

    def test_content_sitemaps_dict_has_no_topics_section(self):
        from content.sitemaps import sitemaps

        self.assertNotIn('topics', sitemaps)
        self.assertNotIn('wiki_topics', sitemaps)


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
