import warnings
from datetime import date
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import CacheKeyWarning, cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from content.caching import (
    _anonymous_cache_key,
    cache_anonymous_page,
    known_public_tags,
)
from content.models import Project
from content.tests.test_crawler_filter_links import _anchors


class CanonicalListingCacheKeyTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_project_tags_are_sorted_deduplicated_and_unknown_params_ignored(self):
        first = self.factory.get('/projects?tag=b&tag=a&tag=a&zz=1')
        second = self.factory.get('/projects?tag=a&tag=b')

        self.assertEqual(_anonymous_cache_key(first), _anonymous_cache_key(second))

    def test_blog_tracking_values_do_not_create_distinct_cache_keys(self):
        first = self.factory.get('/blog?tag=known&utm_source=random-1')
        second = self.factory.get('/blog?tag=known&utm_source=random-2')

        self.assertEqual(_anonymous_cache_key(first), _anonymous_cache_key(second))

    def test_event_page_number_is_bounded_and_semantic_filters_remain_distinct(self):
        highest = self.factory.get('/events?filter=past&page=1000')
        excessive = self.factory.get('/events?filter=past&page=999999')
        upcoming = self.factory.get('/events?page=1000')

        self.assertEqual(_anonymous_cache_key(highest), _anonymous_cache_key(excessive))
        self.assertNotEqual(_anonymous_cache_key(highest), _anonymous_cache_key(upcoming))

    def test_course_detail_keeps_view_and_cohort_in_its_key(self):
        first = self.factory.get('/courses/intro?view=overview&cohort=alpha&x=1')
        second = self.factory.get('/courses/intro?view=overview&cohort=alpha&x=2')
        other_view = self.factory.get('/courses/intro?view=units&cohort=alpha')

        self.assertEqual(_anonymous_cache_key(first), _anonymous_cache_key(second))
        self.assertNotEqual(_anonymous_cache_key(first), _anonymous_cache_key(other_view))

    @override_settings(ALLOWED_HOSTS=['aishippinglabs.com', 'dev.aishippinglabs.com'])
    def test_cache_keys_separate_dev_and_production_host_responses(self):
        production = self.factory.get(
            '/projects', HTTP_HOST='aishippinglabs.com',
        )
        development = self.factory.get(
            '/projects', HTTP_HOST='dev.aishippinglabs.com',
        )

        self.assertNotEqual(
            _anonymous_cache_key(production),
            _anonymous_cache_key(development),
        )

    @override_settings(
        PUBLIC_PAGE_CACHE_ENABLED=True,
        ALLOWED_HOSTS=['aishippinglabs.com', 'dev.aishippinglabs.com'],
    )
    def test_cached_host_dependent_html_cannot_poison_either_host(self):
        cache.clear()
        self.addCleanup(cache.clear)
        view = cache_anonymous_page()(
            lambda request: HttpResponse(request.get_host()),
        )
        requests = {
            'production': self.factory.get(
                '/projects', HTTP_HOST='aishippinglabs.com',
            ),
            'development': self.factory.get(
                '/projects', HTTP_HOST='dev.aishippinglabs.com',
            ),
        }
        for request in requests.values():
            request.user = AnonymousUser()

        for order in (
            ('production', 'development'),
            ('development', 'production'),
        ):
            cache.clear()
            for host in order:
                response = view(requests[host])
                self.assertEqual(
                    response.content.decode(),
                    requests[host].get_host(),
                )


class ListingFacetRejectionTest(SimpleTestCase):
    @override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
    def test_unknown_tag_is_rejected_before_the_listing_view_runs(self):
        request = RequestFactory().get('/projects?tag=unknown')
        request.user = AnonymousUser()
        view = cache_anonymous_page()(
            lambda _request: self.fail('unknown facet reached the full render'),
        )

        with patch('content.caching.known_facet_values', return_value={'known'}):
            response = view(request)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Cache-Control'], 'no-store')

    @override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
    def test_unknown_topic_and_difficulty_are_rejected_before_render(self):
        view = cache_anonymous_page()(
            lambda _request: self.fail('unknown facet reached the full render'),
        )
        for path in ('/blog?topic=unknown', '/projects?difficulty=unknown'):
            request = RequestFactory().get(path)
            request.user = AnonymousUser()
            with self.subTest(path=path):
                with patch('content.caching.known_facet_values', return_value=set()):
                    response = view(request)
                self.assertEqual(response.status_code, 404)

    @override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
    def test_known_public_tags_use_a_shared_cached_value_set(self):
        with patch('content.cache_facets.cache.get', return_value=None), \
             patch('content.cache_facets.cache.set') as cache_set, \
             patch('content.cache_facets._collect_tags', return_value=('known',)) as load:
            self.assertEqual(known_public_tags(), {'known'})

        self.assertEqual(load.call_count, 1)
        cache_set.assert_called_once_with(
            'anon-page:v1:facets:all-public:tag', ('known',), 300,
        )


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

    def test_project_facet_values_and_listing_render_are_cached(self):
        url = '/projects?difficulty=intermediate&tag=agents&tag=gemini'
        with self.assertNumQueries(3):
            response = self.client.get(url)
        self.assertContains(response, 'Bounded Tag Project')

        with self.assertNumQueries(0):
            cached_response = self.client.get(url)
        self.assertContains(cached_response, 'Bounded Tag Project')

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

    def test_default_locmem_cache_bounds_anonymous_page_entries(self):
        options = settings.CACHES['default'].get('OPTIONS', {})
        self.assertEqual(options.get('MAX_ENTRIES'), 500)
