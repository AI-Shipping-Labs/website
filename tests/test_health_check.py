from django.test import RequestFactory, SimpleTestCase, override_settings

from website.middleware import HealthCheckMiddleware


class HealthCheckMiddlewareTest(SimpleTestCase):
    """Unit tests for the /ping bypass.

    The end-to-end host-validation interaction is covered in
    ``tests/test_allowed_hosts.py``; these tests just pin the body
    contract: /ping returns ``settings.VERSION`` so the deploy Verify
    step can string-compare against the commit hash.
    """

    def _call_ping(self):
        rf = RequestFactory()

        def fail_get_response(request):
            raise AssertionError(
                "HealthCheckMiddleware must short-circuit /ping; "
                "get_response should never be called."
            )

        middleware = HealthCheckMiddleware(fail_get_response)
        return middleware(rf.get('/ping'))

    @override_settings(VERSION='20260426-130731-b126a1e')
    def test_ping_body_is_version(self):
        response = self._call_ping()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), '20260426-130731-b126a1e')
        self.assertEqual(response['Content-Type'], 'text/plain')

    @override_settings(VERSION='')
    def test_ping_body_falls_back_to_NA_when_version_empty(self):
        # Local dev / unset env: VERSION="" should not produce an empty
        # body — fall back to "N/A" so the response is still readable.
        response = self._call_ping()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), 'N/A')
