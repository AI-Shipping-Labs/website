"""Crawler controls for faceted listings and social-login entry points."""

from html.parser import HTMLParser
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sessions.models import Session
from django.contrib.sites.models import Site
from django.db import connection
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings, tag
from django.test.utils import CaptureQueriesContext

from integrations.config import clear_config_cache
from website.middleware import (
    CrawlerOAuthGuardMiddleware,
    is_web_crawler_user_agent,
)

PRODUCTION_URL = 'https://aishippinglabs.com'


class _HeadElementParser(HTMLParser):
    """Collect parsed head elements without making assertions on raw HTML."""

    def __init__(self):
        super().__init__()
        self.in_head = False
        self.elements = []
        self.headings = []
        self._heading_text = None

    def handle_starttag(self, tag, attrs):
        if tag == 'head':
            self.in_head = True
        elif self.in_head:
            self.elements.append((tag, dict(attrs)))
        if tag == 'h1':
            self._heading_text = []

    def handle_data(self, data):
        if self._heading_text is not None:
            self._heading_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'head':
            self.in_head = False
        elif tag == 'h1' and self._heading_text is not None:
            self.headings.append(' '.join(''.join(self._heading_text).split()))
            self._heading_text = None

    def select(self, tag, **attrs):
        return [
            element_attrs
            for element_tag, element_attrs in self.elements
            if element_tag == tag
            and all(element_attrs.get(key) == value for key, value in attrs.items())
        ]


@tag('core')
@override_settings(SITE_BASE_URL=PRODUCTION_URL)
class FacetedListingCrawlerPolicyTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _head(self, path):
        response = self.client.get(path)
        parser = _HeadElementParser()
        parser.feed(response.content.decode())
        return response, parser

    def test_filtered_project_is_noindex_nofollow_with_unfiltered_canonical(self):
        response, head = self._head(
            '/projects?difficulty=intermediate&tag=multimodal',
        )

        robots = head.select('meta', name='robots')
        self.assertEqual(len(robots), 1)
        self.assertTrue(
            {'noindex', 'nofollow'}.issubset(
                set(robots[0]['content'].split(',')),
            ),
        )
        self.assertEqual(
            head.select('link', rel='canonical'),
            [{'rel': 'canonical', 'href': f'{PRODUCTION_URL}/projects'}],
        )
        self.assertIn('noindex', response['X-Robots-Tag'])
        self.assertIn('nofollow', response['X-Robots-Tag'])

    def test_unfiltered_and_tracking_only_project_remain_indexable(self):
        for path in ('/projects', '/projects?utm_source=crawler-policy-test'):
            with self.subTest(path=path):
                response, head = self._head(path)
                robots = head.select('meta', name='robots')
                self.assertEqual(len(robots), 1)
                self.assertNotIn('noindex', robots[0]['content'])
                self.assertNotIn('X-Robots-Tag', response)

    def test_each_public_listing_facet_is_excluded(self):
        facet_urls = (
            '/blog?topic=agents',
            '/blog?tag=agents',
            '/projects?difficulty=advanced',
            '/projects?tag=agents',
            '/resources?tag=agents',
            '/downloads?tag=agents',
            '/workshops/catalog?topic=agents',
            '/workshops/catalog?tag=agents',
            '/events?filter=past',
            '/events?filter=upcoming',
            '/events?filter=past&tag=agents',
        )

        for path in facet_urls:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertIn('noindex', response['X-Robots-Tag'])
                self.assertIn('nofollow', response['X-Robots-Tag'])

    def test_filtered_events_canonical_omits_filter_query(self):
        _response, head = self._head('/events?filter=past')

        self.assertEqual(
            head.select('link', rel='canonical'),
            [{'rel': 'canonical', 'href': f'{PRODUCTION_URL}/events'}],
        )


@tag('core')
@override_settings(SITE_BASE_URL=PRODUCTION_URL)
class CrawlerSocialLoginGuardTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        app = SocialApp.objects.create(
            provider='google',
            name='Google',
            client_id='crawler-policy-client-id',
            secret='crawler-policy-secret',
        )
        app.sites.add(Site.objects.get_current())

    def test_known_crawlers_are_blocked_before_queries_or_session_state(self):
        crawler_agents = (
            'Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)',
            'Mozilla/5.0 (compatible; Amazonbot/0.1; +https://amazon.com/bot)',
            'Googlebot/2.1 (+http://www.google.com/bot.html)',
            'ExampleCrawler/1.0',
            'ExampleSpider/1.0',
        )

        for user_agent in crawler_agents:
            for provider in ('google', 'github', 'slack'):
                with self.subTest(user_agent=user_agent, provider=provider):
                    client = Client(HTTP_USER_AGENT=user_agent)
                    sessions_before = Session.objects.count()
                    with CaptureQueriesContext(connection) as queries:
                        response = client.get(
                            f'/accounts/{provider}/login/',
                            {
                                'next': (
                                    '/projects?difficulty=intermediate'
                                    '&tag=agents'
                                ),
                            },
                        )

                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(len(queries), 0)
                    self.assertNotIn(
                        settings.SESSION_COOKIE_NAME,
                        response.cookies,
                    )
                    self.assertNotIn(
                        settings.SESSION_COOKIE_NAME,
                        client.cookies,
                    )
                    self.assertEqual(Session.objects.count(), sessions_before)

    def test_crawler_head_is_blocked_before_queries_or_session_state(self):
        client = Client(HTTP_USER_AGENT='Amazonbot/0.1')
        with CaptureQueriesContext(connection) as queries:
            response = client.head('/accounts/slack/login/')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(queries), 0)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, client.cookies)

    def test_provider_shaped_path_outside_allowlist_reaches_downstream(self):
        downstream = []

        def get_response(request):
            downstream.append(request.path_info)
            return HttpResponse(status=204)

        request = RequestFactory().get(
            '/accounts/example/login/',
            HTTP_USER_AGENT='GPTBot/1.2',
        )
        response = CrawlerOAuthGuardMiddleware(get_response)(request)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(downstream, ['/accounts/example/login/'])

    def test_browser_and_headless_browser_continue_normal_oauth_start(self):
        browser_agents = (
            (
                'standard',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 Chrome/129.0.0.0 Safari/537.36',
            ),
            (
                'headless',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                'HeadlessChrome/129.0.0.0 Safari/537.36',
            ),
            ('missing', None),
            ('empty', ''),
        )

        for label, user_agent in browser_agents:
            with self.subTest(label=label):
                client_kwargs = (
                    {} if user_agent is None
                    else {'HTTP_USER_AGENT': user_agent}
                )
                client = Client(**client_kwargs)
                with patch(
                    'requests.sessions.Session.request',
                    side_effect=AssertionError('OAuth start made a network call'),
                ):
                    response = client.get('/accounts/google/login/')

                self.assertEqual(response.status_code, 302)
                location = urlsplit(response['Location'])
                self.assertEqual(location.hostname, 'accounts.google.com')
                self.assertTrue(parse_qs(location.query).get('state'))
                self.assertIn(settings.SESSION_COOKIE_NAME, response.cookies)

    def test_crawler_can_fetch_an_ordinary_public_listing(self):
        client = Client(HTTP_USER_AGENT='GPTBot/1.2')

        response = client.get('/projects')

        self.assertTemplateUsed(response, 'content/projects_list.html')
        page = _HeadElementParser()
        page.feed(response.content.decode())
        self.assertEqual(page.headings, ['Pet & Portfolio Project Ideas'])
        self.assertEqual(
            page.select('link', rel='canonical'),
            [{'rel': 'canonical', 'href': f'{PRODUCTION_URL}/projects'}],
        )
        self.assertNotIn('X-Robots-Tag', response)

    def test_slashless_crawler_login_redirect_and_guard_are_query_free(self):
        client = Client(HTTP_USER_AGENT='GPTBot/1.2')
        with CaptureQueriesContext(connection) as queries:
            response = client.get('/accounts/google/login', follow=True)

        self.assertEqual(
            response.redirect_chain,
            [('/accounts/google/login/', 301)],
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(len(queries), 0)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, client.cookies)

    def test_crawler_matcher_excludes_browser_automation_tokens(self):
        self.assertTrue(is_web_crawler_user_agent('GPTBot/1.2'))
        self.assertTrue(is_web_crawler_user_agent('Amazonbot/0.1'))
        self.assertTrue(is_web_crawler_user_agent('Googlebot/2.1'))
        self.assertTrue(is_web_crawler_user_agent('ExampleSpider/3.0'))
        self.assertFalse(is_web_crawler_user_agent('HeadlessChrome/129.0.0.0'))
        self.assertFalse(is_web_crawler_user_agent('Playwright/1.48'))
