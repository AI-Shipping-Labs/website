import threading
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from website.middleware import (
    AnonymousRequestLoadShedMiddleware,
    _new_anonymous_request_semaphore,
)


class AnonymousRequestLoadShedTest(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, path='/projects', method='get', user=None):
        request = getattr(self.factory, method)(path)
        request.user = user or AnonymousUser()
        return request

    def test_capacity_is_derived_from_worker_thread_budget(self):
        with patch('website.middleware.gunicorn_thread_count', return_value=8):
            semaphore = _new_anonymous_request_semaphore()

        capacity = 0
        while semaphore.acquire(blocking=False):
            capacity += 1
        self.assertEqual(capacity, 4)
        self.assertLess(capacity, 8)
        for _ in range(capacity):
            semaphore.release()

    def test_saturated_request_gets_fast_retryable_no_store_response(self):
        semaphore = threading.BoundedSemaphore(1)
        self.assertTrue(semaphore.acquire(blocking=False))
        with patch.object(AnonymousRequestLoadShedMiddleware, '_semaphore', semaphore):
            middleware = AnonymousRequestLoadShedMiddleware(
                lambda request: self.fail('saturated request reached the view'),
            )
            response = middleware(self._request())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.content, b'Temporarily busy. Retry shortly.\n')
        self.assertEqual(response['Retry-After'], '1')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertFalse(semaphore.acquire(blocking=False))
        semaphore.release()

    def test_forged_session_cookie_does_not_bypass_anonymous_limit(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        request = self._request()
        request.COOKIES['sessionid'] = 'not-a-real-session'
        with patch.object(AnonymousRequestLoadShedMiddleware, '_semaphore', semaphore):
            response = AnonymousRequestLoadShedMiddleware(
                lambda current: self.fail('saturated request reached the view'),
            )(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '1')
        semaphore.release()

    def test_successful_response_releases_its_slot_without_changing_body(self):
        semaphore = threading.BoundedSemaphore(1)
        with patch.object(AnonymousRequestLoadShedMiddleware, '_semaphore', semaphore):
            middleware = AnonymousRequestLoadShedMiddleware(
                lambda request: HttpResponse('catalog response'),
            )
            response = middleware(self._request())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'catalog response')
        self.assertTrue(semaphore.acquire(blocking=False))
        semaphore.release()

    def test_exception_releases_acquired_slot(self):
        semaphore = threading.BoundedSemaphore(1)
        with patch.object(AnonymousRequestLoadShedMiddleware, '_semaphore', semaphore):
            middleware = AnonymousRequestLoadShedMiddleware(
                lambda request: self._raise_view_error(),
            )
            with self.assertRaisesRegex(RuntimeError, 'view failed'):
                middleware(self._request())

        self.assertTrue(semaphore.acquire(blocking=False))
        semaphore.release()

    def _raise_view_error(self):
        raise RuntimeError('view failed')

    def test_authenticated_and_operational_requests_bypass_saturation(self):
        semaphore = threading.BoundedSemaphore(1)
        self.assertTrue(semaphore.acquire(blocking=False))
        requests = (
            self._request(user=SimpleNamespace(is_authenticated=True)),
            self._request('/studio/'),
            self._request('/admin/'),
            self._request('/api/webhooks/payments'),
            self._request('/api/ses-events'),
            self._request(method='post'),
        )
        with patch.object(AnonymousRequestLoadShedMiddleware, '_semaphore', semaphore):
            middleware = AnonymousRequestLoadShedMiddleware(
                lambda request: HttpResponse('allowed'),
            )
            for request in requests:
                with self.subTest(path=request.path, method=request.method):
                    response = middleware(request)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, b'allowed')
        semaphore.release()

    @override_settings(VERSION='health-version')
    def test_ping_bypasses_a_saturated_anonymous_limiter_in_full_stack(self):
        semaphore = threading.BoundedSemaphore(1)
        self.assertTrue(semaphore.acquire(blocking=False))
        with patch.object(AnonymousRequestLoadShedMiddleware, '_semaphore', semaphore):
            response = self.client.get('/ping', HTTP_HOST='10.0.1.189:8000')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'health-version')
        semaphore.release()
