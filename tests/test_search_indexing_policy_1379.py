"""Production route/state search-indexing policy regression tests (#1379)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings, tag
from django.urls import resolve

from content.models import Article
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from website.middleware import SearchIndexingPolicyMiddleware
from website.search_indexing import (
    NOINDEX_ROBOTS_DIRECTIVE,
    production_request_indexing_disabled,
)

User = get_user_model()
PRODUCTION_SITE_URL = 'https://aishippinglabs.com'


def _resolved_request(path, user=None):
    request = RequestFactory().get(path)
    request.resolver_match = resolve(request.path_info)
    request.user = user or AnonymousUser()
    return request


class ProductionRouteClassifierTest(SimpleTestCase):
    def test_named_private_workspace_routes_are_excluded(self):
        paths = (
            '/notifications',
            '/request-a-call',
            '/vote',
            '/vote/00000000-0000-4000-8000-000000000001',
            '/courses/example/submit',
            '/courses/example/reviews',
            '/courses/example/reviews/1',
            '/events/1/example/join',
            '/events/example/cancel-registration',
            '/sprints/example/board',
            '/sprints/example/feedback/1',
            '/sprints/example/plans/1',
            '/sprints/example/plan/1',
            '/member-api/docs',
        )

        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(
                    production_request_indexing_disabled(
                        _resolved_request(path),
                    ),
                )

    def test_public_content_routes_remain_indexable_for_authenticated_users(self):
        authenticated_user = type(
            'AuthenticatedUser',
            (),
            {'is_authenticated': True},
        )()
        paths = (
            '/blog/example',
            '/courses/example',
            '/courses/example/module',
            '/courses/example/reviews/unit',
            '/workshops/example',
            '/events/1/example',
            '/events/series/1/example',
            '/books/example',
            '/books/example/chapters/1',
            '/books/example/readers/1',
            '/sprints',
            '/sprints/example',
        )

        for path in paths:
            with self.subTest(path=path):
                self.assertFalse(
                    production_request_indexing_disabled(
                        _resolved_request(path, authenticated_user),
                    ),
                )

    def test_private_prefixes_cover_nested_unknown_and_redirect_responses(self):
        middleware = SearchIndexingPolicyMiddleware(
            lambda _request: HttpResponse(status=302),
        )
        for path in (
            '/accounts/',
            '/accounts/future-result',
            '/account/',
            '/onboarding/future-step',
            '/studio/future-fragment',
            '/admin/future-export',
        ):
            with self.subTest(path=path):
                response = middleware(RequestFactory().get(path))
                self.assertEqual(
                    response['X-Robots-Tag'],
                    NOINDEX_ROBOTS_DIRECTIVE,
                )


@override_settings(SITE_BASE_URL=PRODUCTION_SITE_URL)
class ProductionResponsePolicyTest(TestCase):
    def setUp(self):
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def assertNoindexHeader(self, response):
        self.assertEqual(
            response['X-Robots-Tag'],
            NOINDEX_ROBOTS_DIRECTIVE,
        )

    def assertNoindexHtml(self, response, *, canonical=True):
        self.assertNoindexHeader(response)
        html = response.content.decode()
        self.assertEqual(html.count('name="robots"'), 1)
        self.assertIn(
            'name="robots" content="noindex,nofollow,noarchive"',
            html,
        )
        self.assertNotIn('max-snippet', html)
        if canonical:
            self.assertIn('<link rel="canonical"', html)
        else:
            self.assertNotIn('<link rel="canonical"', html)

    def assertIndexableHtml(self, response):
        self.assertNotIn('X-Robots-Tag', response)
        html = response.content.decode()
        self.assertEqual(html.count('name="robots"'), 1)
        self.assertIn('max-snippet:-1', html)
        self.assertNotIn('content="noindex,nofollow,noarchive"', html)

    def test_account_operational_and_token_result_response_matrix(self):
        html_paths = (
            '/accounts/login/',
            '/accounts/register/',
            '/accounts/password-reset-request',
            '/api/password-reset?token=invalid',
            '/api/verify-email?token=invalid',
            '/api/unsubscribe?token=invalid',
            '/api/maven-email-opt-out?token=invalid',
        )
        for path in html_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNoindexHtml(response)

        redirect_paths = (
            '/account/',
            '/onboarding/',
            '/studio/',
            '/admin/',
            '/notifications',
            '/member-api/docs',
        )
        for path in redirect_paths:
            with self.subTest(path=path):
                self.assertNoindexHeader(self.client.get(path))

    def test_private_named_route_slash_redirects_keep_noindex_header(self):
        paths = (
            '/notifications/',
            '/request-a-call/',
            '/vote/',
            '/member-api/docs/',
            '/courses/example/submit/',
            '/events/1/example/join/',
            '/sprints/example/board/',
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], path.rstrip('/'))
                self.assertEqual(response.content, b'')
                self.assertNoindexHeader(response)

        response = self.client.get('/notifications/?page=2')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/notifications?page=2')
        self.assertEqual(response.content, b'')
        self.assertNoindexHeader(response)

    def test_public_and_unknown_slash_redirects_remain_indexable(self):
        paths = (
            '/courses/example/',
            '/events/1/example/',
            '/sprints/example/',
            '/notifications/future/',
            '/unknown-indexing-route-1379/',
        )

        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], path.rstrip('/'))
                self.assertEqual(response.content, b'')
                self.assertNotIn('X-Robots-Tag', response)

    @tag('core')
    def test_authenticated_dashboard_account_and_public_article_boundary(self):
        article = Article.objects.create(
            title='Public indexing boundary article',
            slug='public-indexing-boundary-1379',
            description='Public article metadata.',
            content_markdown='Public article body.',
            date=date(2026, 8, 9),
            published=True,
        )
        user = User.objects.create_user(
            email='indexing-policy-1379@test.com',
            password='testpass',
            email_verified=True,
            account_activated=True,
        )

        self.assertIndexableHtml(self.client.get('/'))
        self.client.force_login(user)
        self.assertNoindexHtml(self.client.get('/'))
        self.assertNoindexHtml(self.client.get('/account/'))
        self.assertNoindexHtml(self.client.get('/notifications'))
        self.assertIndexableHtml(self.client.get(article.get_absolute_url()))

    def test_membership_only_excludes_recognized_result_states(self):
        self.assertIndexableHtml(self.client.get('/membership'))
        self.assertIndexableHtml(
            self.client.get('/membership?checkout_error=unknown'),
        )
        self.assertIndexableHtml(
            self.client.get('/membership?checkout=not-cancelled'),
        )

        excluded_queries = (
            'checkout=cancelled',
            'checkout_error=temporarily_unavailable',
            'checkout_error=invalid_interval',
            'checkout_error=tier_unavailable',
        )
        for query in excluded_queries:
            with self.subTest(query=query):
                self.assertNoindexHtml(self.client.get(f'/membership?{query}'))

    def test_sitemap_keeps_public_content_and_omits_private_families(self):
        article = Article.objects.create(
            title='Sitemap boundary article',
            slug='sitemap-boundary-1379',
            content_markdown='Sitemap body.',
            date=date(2026, 8, 9),
            published=True,
        )

        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, article.get_absolute_url())
        for private_path in (
            '/account/',
            '/accounts/',
            '/onboarding/',
            '/studio/',
            '/admin/',
            '/notifications',
        ):
            self.assertNotContains(response, private_path)

    @override_settings(SITE_BASE_URL='https://dev.aishippinglabs.com')
    def test_dev_policy_wins_without_duplicate_robots_meta(self):
        response = self.client.get('/membership')
        self.assertNoindexHtml(response, canonical=False)

    def test_existing_draft_preview_policy_remains_restrictive(self):
        draft = Article.objects.create(
            title='Private draft boundary',
            slug='private-draft-boundary-1379',
            content_markdown='Private draft body.',
            date=date(2026, 8, 9),
            published=False,
        )

        response = self.client.get(draft.get_preview_url())
        html = response.content.decode()
        self.assertNoindexHeader(response)
        self.assertEqual(html.count('name="robots"'), 1)
        self.assertIn(
            'name="robots" content="noindex,nofollow,noarchive"',
            html,
        )
        self.assertNotIn('<link rel="canonical"', html)
