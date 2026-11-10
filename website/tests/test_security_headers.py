from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from website.security_headers import (
    CONTENT_SECURITY_POLICY_REPORT_ONLY,
    ContentSecurityPolicyReportOnlyMiddleware,
)


class ContentSecurityPolicyReportOnlyTest(SimpleTestCase):
    def test_policy_middleware_follows_django_security_middleware(self):
        middleware = list(settings.MIDDLEWARE)

        self.assertLess(
            middleware.index('django.middleware.security.SecurityMiddleware'),
            middleware.index(
                'website.security_headers.ContentSecurityPolicyReportOnlyMiddleware',
            ),
        )

    def test_html_gets_report_only_policy_without_enforcing_csp(self):
        middleware = ContentSecurityPolicyReportOnlyMiddleware(
            lambda request: HttpResponse('<h1>ok</h1>', content_type='text/html'),
        )

        response = middleware(RequestFactory().get('/'))

        self.assertEqual(
            response['Content-Security-Policy-Report-Only'],
            CONTENT_SECURITY_POLICY_REPORT_ONLY,
        )
        self.assertNotIn('Content-Security-Policy', response)
        self.assertEqual(response.content, b'<h1>ok</h1>')

    def test_non_html_response_keeps_its_existing_security_headers(self):
        def response_with_policy(request):
            response = HttpResponse('{}', content_type='application/json')
            response['Content-Security-Policy'] = "default-src 'none'"
            return response

        response = ContentSecurityPolicyReportOnlyMiddleware(
            response_with_policy,
        )(RequestFactory().get('/api/'))

        self.assertEqual(response['Content-Security-Policy'], "default-src 'none'")
        self.assertNotIn('Content-Security-Policy-Report-Only', response)
