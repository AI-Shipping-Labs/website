"""Root robots.txt response contracts (issue #1380)."""

from pathlib import Path

from django.contrib.sites.models import Site
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings, tag

from analytics.models import CampaignVisit
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from website.robots import robots_txt
from website.search_indexing import DEV_ROBOTS_DIRECTIVE

PRODUCTION_URL = 'https://aishippinglabs.com'
PRODUCTION_BODY = (
    'User-agent: *\n'
    'Allow: /\n'
    '\n'
    'Sitemap: https://aishippinglabs.com/sitemap.xml\n'
)
NON_PRODUCTION_BODY = 'User-agent: *\nAllow: /\n'


class RobotsConfigMixin:
    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()


@tag('core')
@override_settings(
    SITE_BASE_URL=PRODUCTION_URL,
    ALLOWED_HOSTS=['testserver', 'alias.example.test'],
)
class ProductionRobotsResponseTest(RobotsConfigMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        site, _created = Site.objects.get_or_create(pk=1)
        site.domain = 'example.com'
        site.name = 'Stale Site row'
        site.save(update_fields=['domain', 'name'])

    def test_direct_plain_text_contract_and_directive_cardinality(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(response.content.decode(), PRODUCTION_BODY)
        self.assertIs(response.resolver_match.func, robots_txt)
        self.assertEqual(response.content.decode().count('User-agent:'), 1)
        self.assertEqual(response.content.decode().count('Allow:'), 1)
        self.assertEqual(response.content.decode().count('Sitemap:'), 1)
        self.assertNotIn('Disallow:', response.content.decode())
        self.assertNotIn('X-Robots-Tag', response)
        self.assertEqual(response['Cache-Control'], 'no-store, max-age=0')
        self.assertEqual(response['Pragma'], 'no-cache')

    def test_request_host_scheme_and_site_row_cannot_change_sitemap(self):
        response = self.client.get(
            '/robots.txt',
            secure=False,
            HTTP_HOST='alias.example.test',
        )

        self.assertEqual(response.content.decode(), PRODUCTION_BODY)
        self.assertNotContains(response, 'alias.example.test')
        self.assertNotContains(response, 'example.com')

    def test_runtime_canonical_override_is_used_without_double_slash(self):
        first = self.client.get('/robots.txt')
        self.assertEqual(first.content.decode(), PRODUCTION_BODY)

        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://runtime-canonical.test/',
            group='site',
        )
        clear_config_cache()

        second = self.client.get('/robots.txt')
        self.assertEqual(
            second.content.decode(),
            'User-agent: *\nAllow: /\n\n'
            'Sitemap: https://runtime-canonical.test/sitemap.xml\n',
        )
        self.assertNotContains(second, 'runtime-canonical.test//sitemap.xml')
        self.assertEqual(second['Cache-Control'], 'no-store, max-age=0')


@tag('core')
@override_settings(SITE_BASE_URL='https://dev.aishippinglabs.com')
class DevRobotsResponseTest(RobotsConfigMixin, TestCase):
    def test_dev_allows_crawling_omits_sitemap_and_retains_noindex_header(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain; charset=utf-8')
        self.assertEqual(response.content.decode(), NON_PRODUCTION_BODY)
        self.assertNotIn('Disallow:', response.content.decode())
        self.assertNotIn('Sitemap:', response.content.decode())
        self.assertEqual(response['X-Robots-Tag'], DEV_ROBOTS_DIRECTIVE)

    def test_crawl_targets_keep_status_body_and_environment_header(self):
        robots = self.client.get('/robots.txt')
        self.assertEqual(robots.content.decode(), NON_PRODUCTION_BODY)

        responses = (
            self.client.get('/'),
            self.client.get('/ping'),
            self.client.get('/account/'),
            self.client.get('/missing-robots-target-1380'),
        )
        self.assertEqual(
            [response.status_code for response in responses],
            [200, 200, 302, 404],
        )
        self.assertTrue(responses[1].content)
        for response in responses:
            self.assertEqual(response['X-Robots-Tag'], DEV_ROBOTS_DIRECTIVE)


@tag('core')
@override_settings(SITE_BASE_URL='http://127.0.0.1:8000')
class LocalRobotsResponseTest(RobotsConfigMixin, TestCase):
    def test_local_allows_crawling_without_any_deployed_sitemap(self):
        response = self.client.get('/robots.txt')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), NON_PRODUCTION_BODY)
        self.assertNotIn('Sitemap:', response.content.decode())
        self.assertNotContains(response, 'aishippinglabs.com')
        self.assertNotIn('X-Robots-Tag', response)


@override_settings(
    SITE_BASE_URL=PRODUCTION_URL,
    IP_HASH_SALT='',
    Q_CLUSTER={'sync': True, 'orm': 'default', 'name': 'test', 'workers': 1},
)
class RobotsAnalyticsExclusionTest(RobotsConfigMixin, TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_anonymous_crawler_request_does_not_create_campaign_visit(self):
        client = Client(HTTP_USER_AGENT='Mozilla/5.0 (crawler contract)')
        client.cookies['aslab_analytics_consent'] = 'granted'

        response = client.get(
            '/robots.txt?utm_source=search&utm_campaign=robots-audit',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CampaignVisit.objects.count(), 0)
        self.assertFalse(response.cookies)


class RobotsSourceOwnershipTest(SimpleTestCase):
    def test_stale_static_copy_is_removed(self):
        project_root = Path(__file__).resolve().parent.parent
        self.assertFalse((project_root / 'static' / 'robots.txt').exists())
