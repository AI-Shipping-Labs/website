import warnings
from datetime import date
from urllib.parse import parse_qs, urlparse

from django.core.cache import CacheKeyWarning, cache
from django.test import RequestFactory, TestCase, override_settings

from content.caching import _anonymous_cache_key
from content.models import Project
from content.tests.test_crawler_filter_links import _anchors


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class ProjectsListCacheTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Cached Project',
            slug='cached-project',
            description='A project shown in the public listing.',
            date=date(2025, 8, 10),
            published=True,
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_anonymous_cache_hit_and_save_delete_invalidation(self):
        initial_response = self.client.get('/projects')

        self.assertContains(initial_response, 'Cached Project')
        with self.assertNumQueries(0):
            cached_response = self.client.get('/projects')
        self.assertContains(cached_response, 'Cached Project')

        self.project.title = 'Updated Project'
        self.project.save()

        updated_response = self.client.get('/projects')
        self.assertContains(updated_response, 'Updated Project')
        self.assertNotContains(updated_response, 'Cached Project')

        self.project.delete()

        deleted_response = self.client.get('/projects')
        self.assertContains(deleted_response, 'No project ideas yet')
        self.assertNotContains(deleted_response, 'Updated Project')


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class CatalogTagBoundsAndCacheStabilityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='Bounded Tag Project',
            slug='bounded-tag-project',
            description='Project with multiple tags.',
            date=date(2025, 9, 1),
            difficulty='intermediate',
            tags=['agents', 'gemini', 'python', 'rag'],
            published=True,
        )

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_excessive_or_malformed_tag_params_rejected_with_zero_queries(self):
        self.client.get('/projects')
        invalid_queries = (
            'difficulty=intermediate&tag=agents&tag=gemini&tag=python',
            '&'.join(f'tag=t{i}' for i in range(15)),
            f'tag={"x" * 101}',
            'tag=valid%00invalid',
            'tag=valid%7Finvalid',
            '&'.join(['tag=agents'] * 11),
        )
        for path in ('/projects', '/blog', '/resources', '/downloads', '/courses', '/events'):
            for query in invalid_queries:
                with self.subTest(path=path, query=query):
                    with self.assertNumQueries(0):
                        response = self.client.get(f'{path}?{query}')
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response['Cache-Control'], 'no-store')
                    self.assertIn('noindex', response['X-Robots-Tag'])
                    self.assertIn('nofollow', response['X-Robots-Tag'])

    def test_projects_list_filter_and_card_links_never_exceed_two_tags(self):
        response = self.client.get(
            '/projects?difficulty=intermediate&tag=agents&tag=gemini',
        )
        self.assertContains(response, 'Bounded Tag Project')
        anchors = _anchors(response.content.decode())
        tag_anchors = [
            a for a in anchors if 'tag=' in a.get('href', '')
        ]
        self.assertGreaterEqual(len(tag_anchors), 3)
        for anchor in tag_anchors:
            parsed = parse_qs(urlparse(anchor['href']).query)
            self.assertLessEqual(len(parsed.get('tag', [])), 2, anchor['href'])

    def test_projects_list_with_difficulty_and_two_tags_uses_single_query(self):
        self.client.get('/projects')
        with self.assertNumQueries(1):
            response = self.client.get(
                '/projects?difficulty=intermediate&tag=agents&tag=gemini',
            )
        self.assertContains(response, 'Bounded Tag Project')

    def test_anonymous_cache_key_bounds_long_query_without_warning(self):
        factory = RequestFactory()
        long_query = '&'.join(f'utm_{i}={"v" * 30}' for i in range(12))
        request = factory.get(f'/projects?{long_query}')
        key = _anonymous_cache_key(request, cache_group='projects-list')
        self.assertLess(len(key), 200)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', CacheKeyWarning)
            cache.get(key)
        self.assertEqual(len(caught), 0)
