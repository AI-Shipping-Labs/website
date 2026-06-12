"""Unit tests for the dev-smoke bounded-retry navigation helper (Issue #928).

These exercise ``goto_with_retry`` and ``_is_retryable_status`` from
``playwright_tests.conftest`` WITHOUT a live browser or a deployed dev
environment: ``page`` is a hand-rolled stub whose ``goto`` returns a scripted
sequence of fake responses. The constant backoff sleep is patched to a no-op so
the test is fast.

Why this lives in ``tests/`` as a Django ``SimpleTestCase`` tagged ``core``:
the retry decision is pure Python (no DB, no network), and tagging it ``core``
makes it run in ``make test-core`` and push CI — exactly where the issue
requires the retry logic to be proven without standing up the dev stack.
"""

from unittest import mock

from django.test import SimpleTestCase, tag

from playwright_tests.conftest import _is_retryable_status, goto_with_retry


class FakeResponse:
    """Minimal stand-in for a Playwright navigation response."""

    def __init__(self, status):
        self.status = status


class StubPage:
    """Stub ``page`` whose ``goto`` returns a scripted sequence of responses.

    ``responses`` is a list consumed one entry per ``goto`` call. Each entry is
    either a ``FakeResponse`` or ``None`` (navigation error). Records every call
    so tests can assert the exact attempt count.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def goto(self, url, wait_until=None):
        self.calls.append((url, wait_until))
        # If the script runs short, keep returning the last scripted value so a
        # "persistent" sequence of length 1 still represents every attempt.
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


def _no_sleep():
    """Patch the conftest backoff sleep so retries add no wall-clock time."""
    return mock.patch("playwright_tests.conftest.time.sleep")


@tag("core")
class IsRetryableStatusTest(SimpleTestCase):
    """The pure retry-decision boundary: only None / 5xx are retryable."""

    def test_none_response_is_retryable(self):
        self.assertTrue(_is_retryable_status(None))

    def test_5xx_statuses_are_retryable(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertTrue(_is_retryable_status(status))

    def test_non_5xx_statuses_are_not_retryable(self):
        for status in (200, 301, 302, 401, 403, 404):
            with self.subTest(status=status):
                self.assertFalse(_is_retryable_status(status))


@tag("core")
class GotoWithRetryTest(SimpleTestCase):
    """The bounded-retry loop around the pure decision function."""

    def test_first_attempt_200_returns_immediately_without_sleep(self):
        page = StubPage([FakeResponse(200)])
        with _no_sleep() as sleep:
            response = goto_with_retry(page, "/")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(page.calls), 1)
        sleep.assert_not_called()

    def test_transient_500_then_200_retries_and_returns_200(self):
        page = StubPage([FakeResponse(500), FakeResponse(200)])
        with _no_sleep() as sleep:
            response = goto_with_retry(page, "/")
        self.assertEqual(response.status, 200)
        # Exactly two goto calls: the failed 500 then the recovered 200.
        self.assertEqual(len(page.calls), 2)
        # One backoff between the two attempts.
        self.assertEqual(sleep.call_count, 1)

    def test_persistent_500_returns_500_after_exactly_attempts_calls(self):
        page = StubPage([FakeResponse(500)])
        with _no_sleep():
            response = goto_with_retry(page, "/", attempts=3)
        # The helper must NOT raise and must NOT fabricate a 200: it returns
        # the last (still-500) response so the downstream assert can fail.
        self.assertEqual(response.status, 500)
        self.assertEqual(len(page.calls), 3)
        # Proves a real outage is never masked: a downstream == 200 assert fails.
        with self.assertRaises(AssertionError):
            assert response.status == 200

    def test_none_response_is_retried_and_exhausts_attempts(self):
        page = StubPage([None])
        with _no_sleep():
            response = goto_with_retry(page, "/", attempts=3)
        self.assertIsNone(response)
        self.assertEqual(len(page.calls), 3)

    def test_404_returned_on_first_attempt_without_retry(self):
        page = StubPage([FakeResponse(404), FakeResponse(200)])
        with _no_sleep() as sleep:
            response = goto_with_retry(page, "/missing", expected_status=404)
        self.assertEqual(response.status, 404)
        # 404 is non-retryable: exactly one goto, no backoff.
        self.assertEqual(len(page.calls), 1)
        sleep.assert_not_called()

    def test_403_and_302_are_not_retried(self):
        for status in (403, 302):
            with self.subTest(status=status):
                page = StubPage([FakeResponse(status), FakeResponse(200)])
                with _no_sleep():
                    response = goto_with_retry(page, "/x")
                self.assertEqual(response.status, status)
                self.assertEqual(len(page.calls), 1)
