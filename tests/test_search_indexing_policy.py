"""Deployment-wide search-indexing policy regression tests (issue #1376)."""

import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings, tag

from content.models import Article
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from website.middleware import SearchIndexingPolicyMiddleware
from website.search_indexing import (
    NOINDEX_ROBOTS_DIRECTIVE,
    dev_serving_disabled,
    request_targets_dev_host,
    search_indexing_disabled,
)

User = get_user_model()


def _sitemap_locations(response):
    root = ET.fromstring(response.content)
    return [node.text for node in root.findall('.//{*}loc')]


class SearchIndexingClassificationTest(SimpleTestCase):
    @override_settings(SITE_BASE_URL='https://DEV.aishippinglabs.com:443/')
    def test_exact_dev_hostname_disables_indexing(self):
        self.assertTrue(search_indexing_disabled())

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_production_hostname_keeps_indexing_enabled(self):
        self.assertFalse(search_indexing_disabled())

    @override_settings(SITE_BASE_URL='https://dev.aishippinglabs.com.example.test')
    def test_dev_hostname_as_subdomain_suffix_does_not_match(self):
        self.assertFalse(search_indexing_disabled())


@override_settings(ALLOWED_HOSTS=['testserver', 'dev.aishippinglabs.com', 'dev.aishippinglabs.com:8443'])
class DevHostRequestTargetingTest(SimpleTestCase):
    def _request(self, host):
        return RequestFactory().get('/', HTTP_HOST=host)

    def test_exact_dev_host_matches(self):
        self.assertTrue(request_targets_dev_host(self._request('dev.aishippinglabs.com')))

    def test_match_ignores_port(self):
        request = self._request('dev.aishippinglabs.com:8443')
        self.assertTrue(request_targets_dev_host(request))

    def test_production_and_unknown_hosts_do_not_match(self):
        for host in ('aishippinglabs.com', 'www.aishippinglabs.com', 'testserver'):
            with self.subTest(host=host):
                self.assertFalse(request_targets_dev_host(self._request(host)))

    def test_dev_hostname_as_subdomain_suffix_does_not_match(self):
        request = self._request('dev.aishippinglabs.com.example.test')
        self.assertFalse(request_targets_dev_host(request))

    @override_settings(ALLOWED_HOSTS=['testserver'])
    def test_disallowed_host_does_not_match(self):
        request = self._request('dev.aishippinglabs.com')
        self.assertFalse(request_targets_dev_host(request))

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_dev_host_request_is_dev_serving_despite_prod_config(self):
        self.assertTrue(dev_serving_disabled(self._request('dev.aishippinglabs.com')))
        self.assertFalse(dev_serving_disabled(self._request('testserver')))


@override_settings(
    SITE_BASE_URL='https://aishippinglabs.com',
    ALLOWED_HOSTS=['testserver', 'dev.aishippinglabs.com'],
)
class DevHostRequestPolicyTest(TestCase):
    """A dev-host request gets dev treatment with prod config (#1994).

    The shared Contabo container serves prod and dev hostnames with one
    configured production origin. Dev-host requests must still be
    noindexed, canonical-free, and header-tagged even though the
    process-level ``SITE_BASE_URL`` says production.
    """

    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_dev_host_homepage_has_noindex_meta_and_no_canonical(self):
        response = self.client.get('/', HTTP_HOST='dev.aishippinglabs.com')

        self.assertEqual(response['X-Robots-Tag'], NOINDEX_ROBOTS_DIRECTIVE)
        html = response.content.decode()
        self.assertIn('name="robots" content="noindex,nofollow,noarchive"', html)
        self.assertNotIn('<link rel="canonical"', html)

    def test_prod_host_homepage_keeps_indexable_contract(self):
        response = self.client.get('/', HTTP_HOST='testserver')

        self.assertNotIn('X-Robots-Tag', response)
        html = response.content.decode()
        self.assertIn('name="robots" content="max-snippet:-1, max-image-preview:large, max-video-preview:-1"', html)
        self.assertIn('<link rel="canonical" href="https://aishippinglabs.com">', html)

    def test_dev_host_ping_and_404_carry_the_dev_header(self):
        ping_response = self.client.get('/ping', HTTP_HOST='dev.aishippinglabs.com')
        missing_response = self.client.get('/missing-dev-host-1994', HTTP_HOST='dev.aishippinglabs.com')

        self.assertTrue(ping_response.content)
        self.assertEqual(missing_response.status_code, 404)
        self.assertEqual(ping_response['X-Robots-Tag'], NOINDEX_ROBOTS_DIRECTIVE)
        self.assertEqual(missing_response['X-Robots-Tag'], NOINDEX_ROBOTS_DIRECTIVE)


@override_settings(SITE_BASE_URL='https://dev.aishippinglabs.com')
class DevSearchExclusionResponseTest(TestCase):
    """The dev directive applies independently of response type or route."""

    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def assertDevHeader(self, response):
        self.assertEqual(response['X-Robots-Tag'], NOINDEX_ROBOTS_DIRECTIVE)

    @tag('core')
    def test_public_dev_html_has_one_robots_meta_no_canonical_and_dev_og_url(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertDevHeader(response)
        html = response.content.decode()
        self.assertEqual(html.count('name="robots"'), 1)
        self.assertIn('name="robots" content="noindex,nofollow,noarchive"', html)
        self.assertNotIn('<link rel="canonical"', html)
        self.assertIn('property="og:url" content="https://dev.aishippinglabs.com"', html)

    def test_public_collection_metadata_keeps_dev_og_but_omits_canonical(self):
        response = self.client.get('/about')

        self.assertEqual(response.status_code, 200)
        self.assertDevHeader(response)
        html = response.content.decode()
        self.assertNotIn('<link rel="canonical"', html)
        self.assertIn(
            'property="og:url" content="https://dev.aishippinglabs.com/about"',
            html,
        )

    def test_dev_redirect_health_xml_and_404_all_receive_header(self):
        redirect_response = self.client.get('/account/')
        ping_response = self.client.get('/ping')
        sitemap_response = self.client.get('/sitemap.xml')
        missing_response = self.client.get('/missing-search-policy-1376')

        self.assertEqual(redirect_response.status_code, 302)
        self.assertEqual(ping_response.status_code, 200)
        self.assertEqual(ping_response['Content-Type'], 'text/plain')
        self.assertEqual(sitemap_response.status_code, 200)
        self.assertEqual(sitemap_response['Content-Type'], 'application/xml')
        self.assertEqual(missing_response.status_code, 404)
        for response in (
            redirect_response,
            ping_response,
            sitemap_response,
            missing_response,
        ):
            self.assertDevHeader(response)

        locations = _sitemap_locations(sitemap_response)
        self.assertTrue(locations)
        self.assertTrue(
            all(urlsplit(location).netloc == 'dev.aishippinglabs.com' for location in locations)
        )

    def test_authenticated_account_and_studio_html_keep_dev_exclusion(self):
        user = User.objects.create_user(
            email='search-policy-1376@test.com',
            password='testpass',
            is_staff=True,
            email_verified=True,
        )
        self.client.force_login(user)

        for path in ('/account/', '/studio/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertDevHeader(response)
                html = response.content.decode()
                self.assertEqual(html.count('name="robots"'), 1)
                self.assertIn(
                    'name="robots" content="noindex,nofollow,noarchive"',
                    html,
                )
                self.assertNotIn('<link rel="canonical"', html)


@override_settings(SITE_BASE_URL='https://dev.aishippinglabs.com')
class DevSearchExclusionMiddlewareResponseTypeTest(SimpleTestCase):
    """JSON, errors, and redirects are covered without template coupling."""

    def test_non_html_response_matrix_receives_header(self):
        request = RequestFactory().get('/response-matrix')
        responses = (
            JsonResponse({'ok': True}),
            HttpResponse('unavailable', status=503, content_type='text/plain'),
            HttpResponseRedirect('/next'),
        )

        for expected in responses:
            with self.subTest(status=expected.status_code):
                middleware = SearchIndexingPolicyMiddleware(lambda _request: expected)
                response = middleware(request)
                self.assertEqual(response['X-Robots-Tag'], NOINDEX_ROBOTS_DIRECTIVE)


@tag('core')
@override_settings(SITE_BASE_URL='https://aishippinglabs.com')
class ProductionSearchIndexingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Production search policy article',
            slug='production-search-policy-article',
            content_markdown='# Production search policy article',
            date=date(2026, 8, 9),
            published=True,
            required_level=0,
        )

    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_production_home_and_detail_remain_indexable_with_canonicals(self):
        expectations = (
            ('/', 'https://aishippinglabs.com'),
            (
                self.article.get_absolute_url(),
                f'https://aishippinglabs.com{self.article.get_absolute_url()}',
            ),
        )

        for path, canonical in expectations:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('X-Robots-Tag', response)
                html = response.content.decode()
                self.assertEqual(html.count('name="robots"'), 1)
                self.assertIn(
                    'name="robots" content="max-snippet:-1, '
                    'max-image-preview:large, max-video-preview:-1"',
                    html,
                )
                self.assertIn(f'<link rel="canonical" href="{canonical}">', html)

    def test_production_sitemap_uses_only_the_canonical_origin(self):
        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Robots-Tag'], 'noindex, noodp, noarchive')
        locations = _sitemap_locations(response)
        self.assertTrue(locations)
        self.assertTrue(
            all(
                urlsplit(location)[:2] == ('https', 'aishippinglabs.com')
                for location in locations
            )
        )
        self.assertNotContains(response, 'dev.aishippinglabs.com')
        retired_host = 'prod' + '.aishippinglabs.com'
        self.assertNotContains(response, retired_host)
