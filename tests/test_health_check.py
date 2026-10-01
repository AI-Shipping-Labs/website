from contextlib import ExitStack
from unittest import mock

from django.conf import settings
from django.core.cache import caches
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


class PingDependencyFreeTest(SimpleTestCase):
    """Issue #1854: /ping must stay fast while the database is struggling.

    The ALB health check shares gunicorn's request slots, so the full
    middleware stack must answer it without any database or cache call.
    ``SimpleTestCase`` forbids database queries; every cache read or write
    method is patched to fail.
    """

    CACHE_METHODS = (
        'get', 'get_many', 'get_or_set', 'has_key', 'set', 'set_many',
        'add', 'incr', 'decr', 'touch', 'delete',
    )

    @override_settings(VERSION='20260930-120000-abc1234')
    def test_ping_through_full_stack_touches_no_database_or_cache(self):
        with ExitStack() as stack:
            for alias in settings.CACHES:
                for method in self.CACHE_METHODS:
                    stack.enter_context(mock.patch.object(
                        caches[alias],
                        method,
                        side_effect=AssertionError(f'/ping used cache {alias}'),
                    ))
            response = self.client.get('/ping', HTTP_HOST='10.0.1.189:8000')

        self.assertEqual(response.content.decode(), '20260930-120000-abc1234')
