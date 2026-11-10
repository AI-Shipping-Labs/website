from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import RequestFactory, TestCase, override_settings

from content.caching import _anonymous_cache_key
from content.marketing_cache import (
    PUBLISHED_MARKETING_PATHS_CACHE_KEY,
    PUBLISHED_MARKETING_PATHS_CACHE_TTL,
    published_marketing_page_paths,
)
from content.models import (
    CuratedLink,
    Download,
    InterviewCategory,
    MarketingPage,
    Project,
    Tutorial,
)
from integrations.shared_cache import reset_local_shared_cache_memo


@override_settings(
    PUBLIC_PAGE_CACHE_ENABLED=True,
    SITE_BASE_URL='https://aishippinglabs.com',
)
class RemainingPublicPageCacheTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Cacheable project',
            slug='cacheable-project',
            date=date(2025, 1, 1),
            content_markdown='A public project.',
            content_html='<p>A public project.</p>',
        )
        cls.member = get_user_model().objects.create_user(
            email='cached-page-member@example.test',
            password='test-password',
        )
        CuratedLink.objects.create(
            item_id='cacheable-resource',
            title='Cacheable resource',
            url='https://example.com/resource',
            category='tools',
        )
        Tutorial.objects.create(
            title='Cacheable tutorial',
            slug='cacheable-tutorial',
            date=date(2025, 1, 1),
        )
        Download.objects.create(
            title='Cacheable download',
            slug='cacheable-download',
            file_url='https://example.com/download.pdf',
        )
        InterviewCategory.objects.create(
            slug='cacheable-interview',
            title='Cacheable interview',
            status='published',
            body_markdown='Questions.',
        )

    def setUp(self):
        caches['default'].clear()
        caches['django_q'].clear()
        reset_local_shared_cache_memo()

    def _assert_repeat_request_is_cached(self, path):
        first = self.client.get(path)
        self.assertEqual(first.status_code, 200)
        with self.assertNumQueries(0):
            second = self.client.get(path)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.content, first.content)

    def test_project_detail_reuses_anonymous_response(self):
        self._assert_repeat_request_is_cached(
            self.project.get_absolute_url(),
        )

    def test_curated_collection_reuses_anonymous_response(self):
        self._assert_repeat_request_is_cached('/resources')

    def test_tutorial_listing_reuses_anonymous_response(self):
        self._assert_repeat_request_is_cached('/tutorials')

    def test_download_listing_reuses_anonymous_response(self):
        self._assert_repeat_request_is_cached('/downloads')

    def test_interview_hub_reuses_anonymous_response(self):
        self._assert_repeat_request_is_cached('/interview')

    def test_sitemap_reuses_rendered_response_for_one_hour(self):
        self._assert_repeat_request_is_cached('/sitemap.xml')

    def test_sitemap_cache_ttl_is_exactly_one_hour(self):
        with patch('content.caching.cache.set') as set_cache:
            response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        set_cache.assert_called_once()
        self.assertEqual(set_cache.call_args.args[2], 3600)

    @override_settings(ALLOWED_HOSTS=[
        'aishippinglabs.com', 'dev.aishippinglabs.com',
    ])
    def test_sitemap_cache_key_tracks_host_and_page_number(self):
        factory = RequestFactory()
        production_page_one = factory.get(
            '/sitemap.xml?p=1', HTTP_HOST='aishippinglabs.com',
        )
        production_page_two = factory.get(
            '/sitemap.xml?p=2', HTTP_HOST='aishippinglabs.com',
        )
        development_page_one = factory.get(
            '/sitemap.xml?p=1', HTTP_HOST='dev.aishippinglabs.com',
        )

        self.assertNotEqual(
            _anonymous_cache_key(production_page_one),
            _anonymous_cache_key(production_page_two),
        )
        self.assertNotEqual(
            _anonymous_cache_key(production_page_one),
            _anonymous_cache_key(development_page_one),
        )

    def test_authenticated_download_listing_does_not_reuse_anonymous_response(self):
        anonymous = self.client.get('/downloads')
        self.assertEqual(anonymous.status_code, 200)
        Download.objects.filter(slug='cacheable-download').update(
            title='Updated member-visible download',
        )
        self.client.force_login(self.member)

        response = self.client.get('/downloads')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Updated member-visible download')
        self.assertNotContains(response, 'Cacheable download')
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_unknown_marketing_path_uses_cached_published_path_set(self):
        first = self.client.get('/unknown-public-marketing-page')
        self.assertEqual(first.status_code, 404)
        with self.assertNumQueries(0):
            second = self.client.get('/unknown-public-marketing-page')
        self.assertEqual(second.status_code, 404)
        self.assertEqual(
            caches['django_q'].get(PUBLISHED_MARKETING_PATHS_CACHE_KEY),
            (),
        )

    def test_marketing_path_set_uses_a_bounded_shared_cache_ttl(self):
        with patch('content.marketing_cache.set_shared_cache') as set_cache:
            self.assertEqual(published_marketing_page_paths(), frozenset())

        set_cache.assert_called_once_with(
            PUBLISHED_MARKETING_PATHS_CACHE_KEY,
            (),
            PUBLISHED_MARKETING_PATHS_CACHE_TTL,
        )

    def test_marketing_page_save_invalidates_cached_path_membership(self):
        self.client.get('/unknown-before-publish')
        MarketingPage.objects.create(
            title='New campaign page',
            public_path='/new-campaign-page',
            content_markdown='New campaign content.',
            status='published',
        )

        response = self.client.get('/new-campaign-page')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'New campaign content.')
