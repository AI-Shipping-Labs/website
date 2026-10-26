"""Tests for RequestIdMiddleware and the gunicorn access-log contract."""

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from website.gunicorn_logging import (
    is_token_bearing_path,
    redact_token_path,
)
from website.request_id_middleware import RequestIdMiddleware

factory = RequestFactory()


def _respond(request):
    return HttpResponse('ok')


class RequestIdMiddlewareTest(SimpleTestCase):
    def test_valid_ids_propagate_to_response(self):
        request = factory.get(
            '/', HTTP_X_REQUEST_ID='req-1.0_A',
            HTTP_X_CORRELATION_ID='corr-2.0_B',
        )
        response = RequestIdMiddleware(_respond)(request)

        self.assertEqual(response['X-Request-ID'], 'req-1.0_A')
        self.assertEqual(response['X-Correlation-ID'], 'corr-2.0_B')
        self.assertEqual(request.request_id, 'req-1.0_A')

    def test_missing_ids_are_minted_and_correlated(self):
        request = factory.get('/')
        response = RequestIdMiddleware(_respond)(request)
        minted = response['X-Request-ID']

        self.assertTrue(minted)
        self.assertEqual(response['X-Correlation-ID'], minted)
        self.assertEqual(request.correlation_id, minted)

    def test_invalid_ids_are_replaced_not_echoed(self):
        request = factory.get(
            '/', HTTP_X_REQUEST_ID='has space',
            HTTP_X_CORRELATION_ID='also bad!',
        )
        response = RequestIdMiddleware(_respond)(request)

        self.assertNotEqual(response['X-Request-ID'], 'has space')
        self.assertEqual(response['X-Request-ID'], response['X-Correlation-ID'])


class RecipientTokenRedactionTest(SimpleTestCase):
    def test_token_segments_are_redacted(self):
        self.assertEqual(
            redact_token_path('/t/o/abc123.gif'),
            '/t/o/[token].gif',
        )
        self.assertEqual(
            redact_token_path('/t/c/abc123'),
            '/t/c/[token]',
        )
        self.assertEqual(
            redact_token_path('/unsubscribe/abc123'),
            '/unsubscribe/[token]',
        )

    def test_plain_paths_are_untouched(self):
        self.assertEqual(redact_token_path('/about/'), '/about/')
        self.assertFalse(is_token_bearing_path('/about/'))
        self.assertTrue(is_token_bearing_path('/t/c/abc123'))
