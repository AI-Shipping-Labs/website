from threading import Barrier, Event, Lock, Thread
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from content.caching import cache_anonymous_page


@override_settings(PUBLIC_PAGE_CACHE_ENABLED=True)
class AnonymousCacheSingleflightTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def _request(self, path="/cold-page"):
        request = self.factory.get(path)
        request.user = AnonymousUser()
        return request

    def _run_concurrent_requests(self, view, synchronized_get):
        responses = []
        errors = []

        def request_page():
            try:
                responses.append(view(self._request()))
            except Exception as exc:
                errors.append(exc)

        with patch("content.caching.cache.get", side_effect=synchronized_get):
            threads = [Thread(target=request_page) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        return responses, errors, threads

    def test_concurrent_same_key_misses_render_once(self):
        cache_reads = Barrier(2)
        read_lock = Lock()
        initial_reads = 0
        original_get = cache.get
        renders = []

        def synchronized_get(key, default=None, version=None):
            nonlocal initial_reads
            value = original_get(key, default, version)
            with read_lock:
                initial_reads += 1
                should_wait = initial_reads <= 2
            if should_wait:
                cache_reads.wait(timeout=3)
            return value

        @cache_anonymous_page()
        def view(request):
            renders.append(request.path)
            return HttpResponse("shared page")

        responses, errors, threads = self._run_concurrent_requests(
            view, synchronized_get,
        )

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(renders), 1)
        self.assertEqual([response.content for response in responses], [b"shared page"] * 2)

    def test_failed_render_releases_key_and_does_not_cache_error(self):
        renders = []

        @cache_anonymous_page()
        def view(request):
            renders.append(request.path)
            if len(renders) == 1:
                raise RuntimeError("render failed")
            return HttpResponse("recovered")

        with self.assertRaisesRegex(RuntimeError, "render failed"):
            view(self._request())

        response = view(self._request())
        cached = view(self._request())

        self.assertEqual(response.content, b"recovered")
        self.assertEqual(cached.content, b"recovered")
        self.assertEqual(len(renders), 2)

    def test_distinct_keys_render_without_waiting_for_each_other(self):
        first_started = Event()
        release_first = Event()
        other_finished = Event()

        @cache_anonymous_page()
        def view(request):
            if request.path == "/first":
                first_started.set()
                release_first.wait(timeout=3)
                return HttpResponse("first")
            other_finished.set()
            return HttpResponse("other")

        first = Thread(target=lambda: view(self._request("/first")))
        other = Thread(target=lambda: view(self._request("/other")))
        first.start()
        self.assertTrue(first_started.wait(timeout=3))
        other.start()
        try:
            self.assertTrue(other_finished.wait(timeout=1))
        finally:
            release_first.set()
            first.join(timeout=5)
            other.join(timeout=5)
        self.assertFalse(first.is_alive())
        self.assertFalse(other.is_alive())
