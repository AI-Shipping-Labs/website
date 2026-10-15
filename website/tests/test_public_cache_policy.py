"""Tests for PublicCachePolicyMiddleware — the CDN cache contract.

The CloudFront distribution in front of prod caches ONLY anonymous
public HTML and must never cache (or serve to another viewer) anything
user-specific. These tests pin the response-header side of that
contract; the request-side (cache-key) half lives in the aws-infra
CloudFront cache policy + viewer-request function.
"""

from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings

from analytics.consent import ANALYTICS_CONSENT_COOKIE
from website.middleware import PublicCachePolicyMiddleware

factory = RequestFactory()


def _get(path='/', *, method='get', cookies=None, html='<html></html>', status=200,
         content_type='text/html; charset=utf-8'):
    request = getattr(factory, method)(path)
    for name, value in (cookies or {}).items():
        request.COOKIES[name] = value
    response = HttpResponse(html, status=status, content_type=content_type)
    return PublicCachePolicyMiddleware(
        lambda r: response,
    )(request)


class PublicCachePolicyUnitTest(TestCase):
    def test_anonymous_html_is_public_with_smaxage(self):
        response = _get()
        self.assertEqual(
            response['Cache-Control'], 'public, max-age=0, s-maxage=300',
        )
        self.assertIn('cookie', response['Vary'].lower())
        self.assertEqual(len(response.cookies), 0)

    def test_smaxage_is_tunable_via_settings(self):
        with override_settings(PUBLIC_HTML_SMAXAGE=60):
            response = _get()
        self.assertEqual(
            response['Cache-Control'], 'public, max-age=0, s-maxage=60',
        )

    def test_session_cookie_request_is_no_store(self):
        response = _get(cookies={'sessionid': 'whatever'})
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('cookie', response['Vary'].lower())

    def test_response_with_set_cookie_is_no_store(self):
        def respond(request):
            response = HttpResponse('<html></html>',
                                    content_type='text/html; charset=utf-8')
            response.set_cookie('csrftoken', 'abc')
            return response

        response = PublicCachePolicyMiddleware(respond)(factory.get('/'))
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_response_with_deleted_cookie_is_no_store(self):
        def respond(request):
            response = HttpResponse('<html></html>',
                                    content_type='text/html; charset=utf-8')
            response.delete_cookie('aslab_aid')
            return response

        response = PublicCachePolicyMiddleware(respond)(factory.get('/'))
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_non_200_html_is_left_untouched(self):
        for status in (301, 302, 404, 500):
            response = _get(status=status)
            self.assertNotIn('Cache-Control', response, status)

    def test_non_html_200_is_left_untouched(self):
        response = _get(content_type='application/xml')
        self.assertNotIn('Cache-Control', response)

    def test_post_is_left_untouched(self):
        response = _get(method='post')
        self.assertNotIn('Cache-Control', response)

    def test_head_gets_public_headers(self):
        response = _get(method='head')
        self.assertEqual(
            response['Cache-Control'], 'public, max-age=0, s-maxage=300',
        )

    def test_view_cache_control_is_overwritten_for_public_pages(self):
        request = factory.get('/')
        upstream = HttpResponse('<html></html>',
                                content_type='text/html; charset=utf-8')
        upstream['Cache-Control'] = 'max-age=999'
        response = PublicCachePolicyMiddleware(lambda r: upstream)(request)
        self.assertEqual(
            response['Cache-Control'], 'public, max-age=0, s-maxage=300',
        )


class ProtectedPrefixUnitTest(TestCase):
    def test_protected_prefixes_are_no_store_even_when_anonymous(self):
        for path in (
            '/accounts/login/',
            '/account/email/',
            '/admin/login/',
            '/api/v1/whatever/',
            '/member-api/whatever/',
            '/onboarding/welcome/',
            '/studio/',
        ):
            response = _get(path, status=200)
            self.assertEqual(
                response['Cache-Control'], 'private, no-store', path,
            )
            self.assertIn('cookie', response['Vary'].lower(), path)

    def test_protected_prefix_no_store_applies_to_non_html(self):
        response = _get('/api/v1/thing/', content_type='application/json')
        self.assertEqual(response['Cache-Control'], 'private, no-store')


class PublicCachePolicyIntegrationTest(TestCase):
    """Full-stack checks through the real middleware chain."""

    def test_crawler_request_to_home_is_publicly_cacheable(self):
        # End-to-end crawler story: no consent cookie, bot user-agent
        # (no forced csrftoken from the consent context processor), no
        # analytics cookies -> the response is edge-cacheable and sets
        # nothing.
        response = self.client.get('/', HTTP_USER_AGENT='GPTBot/1.1')
        self.assertTemplateUsed(response, 'home.html')
        self.assertEqual(
            response['Cache-Control'], 'public, max-age=0, s-maxage=300',
        )
        self.assertEqual(len(response.cookies), 0)

    def test_human_first_visit_home_sets_csrf_cookie_and_is_no_store(self):
        # Interactive first visits still get the consent csrftoken; the
        # middleware must then mark the response uncacheable.
        response = self.client.get('/')
        self.assertTemplateUsed(response, 'home.html')
        self.assertIn('csrftoken', response.cookies)
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_login_page_is_no_store(self):
        response = self.client.get('/accounts/login/')
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_robots_txt_is_not_marked_public(self):
        response = self.client.get('/robots.txt')
        cache_control = response.get('Cache-Control', '')
        self.assertNotIn('public', cache_control.lower())


class ConsentCsrfTokenForInteractiveClientsTest(TestCase):
    """Only fetch-only crawlers without a session skip the consent token.

    Regression guard for the 0c7033b5e red main: headless browsers match
    the analytics bot regex (``headlesschrome``) but submit the consent
    banner and other JS forms, so withholding the token 403'd every
    consent POST from Playwright.
    """

    HEADLESS_CHROME_UA = (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) HeadlessChrome/131.0.0.0 Safari/537.36'
    )

    def test_anonymous_headless_chrome_can_post_consent(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get('/', HTTP_USER_AGENT=self.HEADLESS_CHROME_UA)
        token = page.context['analytics_consent_csrf_token']
        self.assertIn('csrftoken', page.cookies)

        response = client.post(
            '/api/analytics/consent',
            data='{"consent": "denied"}',
            content_type='application/json',
            HTTP_USER_AGENT=self.HEADLESS_CHROME_UA,
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.json()['consent'], 'denied')
        self.assertEqual(
            response.cookies[ANALYTICS_CONSENT_COOKIE].value, 'denied',
        )

    def test_crawler_ua_with_session_still_gets_consent_token(self):
        # A session request is never edge-cacheable, so withholding the
        # consent token would only break the banner POST (the Studio
        # helper path in the failing Playwright suites).
        self.client.cookies['sessionid'] = 'not-a-real-session'
        response = self.client.get('/', HTTP_USER_AGENT='GPTBot/1.1')
        self.assertNotEqual(response.context['analytics_consent_csrf_token'], '')
        self.assertIn('csrftoken', response.cookies)
