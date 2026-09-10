import errno
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase, tag

from scripts import capture_screenshots


class _FakeThread:
    def __init__(self, *, target, daemon):
        self.target = target
        self.daemon = daemon
        self.started = False
        self.joined = False
        self.ident = None

    def start(self):
        self.started = True
        self.ident = 1

    def is_alive(self):
        return self.started

    def join(self, timeout=None):
        self.joined = True


class _FakeServer:
    def __init__(self):
        self.shutdown_calls = 0
        self.close_calls = 0

    def serve_forever(self):
        raise AssertionError("fake thread must not execute its target")

    def shutdown(self):
        self.shutdown_calls += 1

    def server_close(self):
        self.close_calls += 1


@tag("core")
class ScreenshotServerLifecycleTest(SimpleTestCase):
    def test_port_selection_asks_os_for_loopback_ephemeral_port(self):
        candidate = Mock()
        candidate.__enter__ = Mock(return_value=candidate)
        candidate.__exit__ = Mock(return_value=False)
        candidate.getsockname.return_value = (capture_screenshots.DJANGO_HOST, 43127)

        with patch.object(capture_screenshots.socket, "socket", return_value=candidate):
            port = capture_screenshots._select_loopback_port()

        self.assertEqual(port, 43127)
        candidate.bind.assert_called_once_with((capture_screenshots.DJANGO_HOST, 0))

    def test_first_bind_collision_uses_new_candidate_and_owned_base_url(self):
        collision = OSError(errno.EADDRINUSE, "candidate claimed")
        server = _FakeServer()

        with (
            patch.object(
                capture_screenshots,
                "_select_loopback_port",
                side_effect=[41001, 41002],
            ) as select_port,
            patch.object(
                capture_screenshots,
                "_bind_django_server",
                side_effect=[collision, server],
            ) as bind_server,
            patch.object(capture_screenshots, "Thread", _FakeThread),
            patch.object(
                capture_screenshots,
                "_server_is_running",
                return_value=True,
            ) as readiness,
        ):
            owned = capture_screenshots._start_django_server()

        self.assertEqual(select_port.call_count, 2)
        self.assertEqual(bind_server.call_args_list, [call(41001), call(41002)])
        self.assertEqual(owned.base_url, "http://127.0.0.1:41002")
        readiness.assert_called_once_with("http://127.0.0.1:41002/")
        owned.stop()
        self.assertEqual(server.shutdown_calls, 1)
        self.assertEqual(server.close_calls, 1)
        self.assertTrue(owned.thread.joined)

    def test_three_bind_collisions_fail_before_any_readiness_request(self):
        collision = OSError(errno.EADDRINUSE, "candidate claimed")

        with (
            patch.object(
                capture_screenshots,
                "_select_loopback_port",
                side_effect=[41001, 41002, 41003],
            ),
            patch.object(
                capture_screenshots,
                "_bind_django_server",
                side_effect=[collision, collision, collision],
            ) as bind_server,
            patch.object(capture_screenshots, "_server_is_running") as readiness,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "after 3 loopback bind collisions",
            ):
                capture_screenshots._start_django_server()

        self.assertEqual(bind_server.call_count, 3)
        readiness.assert_not_called()

    def test_two_concurrent_invocations_own_distinct_servers(self):
        first_server = _FakeServer()
        second_server = _FakeServer()
        barrier = threading.Barrier(2)
        port_lock = threading.Lock()
        ports = iter([42001, 42002])

        def select_port():
            barrier.wait()
            with port_lock:
                return next(ports)

        with (
            patch.object(
                capture_screenshots,
                "_select_loopback_port",
                side_effect=select_port,
            ),
            patch.object(
                capture_screenshots,
                "_bind_django_server",
                side_effect=[first_server, second_server],
            ),
            patch.object(capture_screenshots, "Thread", _FakeThread),
            patch.object(capture_screenshots, "_server_is_running", return_value=True),
        ):
            with ThreadPoolExecutor(max_workers=2) as executor:
                owned = list(
                    executor.map(
                        lambda _index: capture_screenshots._start_django_server(),
                        range(2),
                    )
                )

        self.assertEqual(
            {item.base_url for item in owned},
            {"http://127.0.0.1:42001", "http://127.0.0.1:42002"},
        )
        for item in owned:
            item.stop()
        self.assertEqual(first_server.shutdown_calls, 1)
        self.assertEqual(second_server.shutdown_calls, 1)

    def test_thread_start_failure_releases_bound_listener(self):
        server = _FakeServer()
        thread = _FakeThread(target=server.serve_forever, daemon=True)
        thread.start = Mock(side_effect=RuntimeError("thread unavailable"))

        with (
            patch.object(capture_screenshots, "_select_loopback_port", return_value=42501),
            patch.object(capture_screenshots, "_bind_django_server", return_value=server),
            patch.object(capture_screenshots, "Thread", return_value=thread),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                capture_screenshots._start_django_server()

        self.assertEqual(server.shutdown_calls, 0)
        self.assertEqual(server.close_calls, 1)

    def test_readiness_failure_stops_owned_server_without_real_sleep(self):
        server = _FakeServer()

        with (
            patch.object(capture_screenshots, "_select_loopback_port", return_value=42601),
            patch.object(capture_screenshots, "_bind_django_server", return_value=server),
            patch.object(capture_screenshots, "Thread", _FakeThread),
            patch.object(capture_screenshots, "_server_is_running", return_value=False),
            patch.object(capture_screenshots.time, "sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "did not start at http://127.0.0.1:42601",
            ):
                capture_screenshots._start_django_server()

        self.assertEqual(sleep.call_count, 30)
        self.assertEqual(server.shutdown_calls, 1)
        self.assertEqual(server.close_calls, 1)

    def test_owned_server_cleanup_runs_on_success_and_failure(self):
        for error in (None, RuntimeError("capture failed")):
            with self.subTest(error=error):
                owned = Mock()
                with patch.object(
                    capture_screenshots,
                    "_start_django_server",
                    return_value=owned,
                ):
                    if error is None:
                        with capture_screenshots._owned_django_server() as yielded:
                            self.assertIs(yielded, owned)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "capture failed"):
                            with capture_screenshots._owned_django_server():
                                raise error
                owned.stop.assert_called_once_with()


class _FakePage:
    def __init__(self):
        self.goto_calls = []
        self.fill_calls = []
        self.click_calls = []
        self.screenshot_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append(call(url, **kwargs))

    def fill(self, selector, value):
        self.fill_calls.append(call(selector, value))

    def click(self, selector):
        self.click_calls.append(call(selector))

    def wait_for_timeout(self, _milliseconds):
        return None

    def screenshot(self, **kwargs):
        self.screenshot_calls.append(call(**kwargs))


class _FakePlaywrightContext:
    def __init__(self, playwright):
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


@tag("core")
class ScreenshotCaptureContractTest(SimpleTestCase):
    def test_login_navigation_and_capture_use_one_owned_base_url(self):
        page = _FakePage()
        browser = Mock()
        context = Mock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright = Mock()
        playwright.chromium.launch.return_value = browser

        with TemporaryDirectory() as output_dir:
            with patch(
                "playwright.sync_api.sync_playwright",
                return_value=_FakePlaywrightContext(playwright),
            ):
                results = capture_screenshots.capture_screenshots(
                    ["/account/", "/blog/"],
                    output_dir,
                    login_as={"email": "member@example.com", "password": "secret"},
                    viewport={"width": 393, "height": 851},
                    base_url="http://127.0.0.1:43001",
                )

        self.assertEqual(
            page.goto_calls,
            [
                call("http://127.0.0.1:43001/accounts/login/", wait_until="networkidle"),
                call(
                    "http://127.0.0.1:43001/account/",
                    wait_until="networkidle",
                    timeout=30000,
                ),
                call(
                    "http://127.0.0.1:43001/blog/",
                    wait_until="networkidle",
                    timeout=30000,
                ),
            ],
        )
        self.assertEqual(
            page.fill_calls,
            [call("#login-email", "member@example.com"), call("#login-password", "secret")],
        )
        self.assertEqual(page.click_calls, [call("#login-submit")])
        self.assertEqual([url for url, _path in results], ["/account/", "/blog/"])
        self.assertTrue(results[0][1].endswith("account_393x851.png"))
        self.assertTrue(results[1][1].endswith("blog_393x851.png"))
        browser.new_context.assert_called_once_with(
            viewport={"width": 393, "height": 851},
            color_scheme="dark",
        )
        self.assertEqual(
            page.screenshot_calls,
            [
                call(path=results[0][1], full_page=True),
                call(path=results[1][1], full_page=True),
            ],
        )
        browser.close.assert_called_once_with()

    def test_navigation_failure_closes_browser(self):
        page = _FakePage()
        page.goto = Mock(side_effect=RuntimeError("navigation failed"))
        browser = Mock()
        context = Mock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright = Mock()
        playwright.chromium.launch.return_value = browser

        with TemporaryDirectory() as output_dir:
            with patch(
                "playwright.sync_api.sync_playwright",
                return_value=_FakePlaywrightContext(playwright),
            ):
                with self.assertRaisesRegex(RuntimeError, "navigation failed"):
                    capture_screenshots.capture_screenshots(
                        ["/broken/"],
                        output_dir,
                        base_url="http://127.0.0.1:43002",
                    )

        page.goto.assert_called_once_with(
            "http://127.0.0.1:43002/broken/",
            wait_until="networkidle",
            timeout=30000,
        )
        browser.close.assert_called_once_with()

    def test_main_migrates_then_passes_owned_base_url_and_cleans_up(self):
        owned = Mock(base_url="http://127.0.0.1:44001")

        @contextmanager
        def owned_context():
            try:
                yield owned
            finally:
                owned.stop()

        with TemporaryDirectory() as output_dir:
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "capture_screenshots.py",
                        "--urls",
                        "/blog/",
                        "--output",
                        output_dir,
                    ],
                ),
                patch.object(capture_screenshots, "_migrate_database") as migrate,
                patch.object(
                    capture_screenshots,
                    "_owned_django_server",
                    side_effect=owned_context,
                ),
                patch.object(
                    capture_screenshots,
                    "capture_screenshots",
                    return_value=[("/blog/", os.path.join(output_dir, "blog.png"))],
                ) as capture,
            ):
                results = capture_screenshots.main()

        migrate.assert_called_once_with()
        capture.assert_called_once_with(
            ["/blog/"],
            output_dir,
            login_as=None,
            viewport=capture_screenshots.DEFAULT_VIEWPORT,
            base_url="http://127.0.0.1:44001",
        )
        owned.stop.assert_called_once_with()
        self.assertEqual(results[0][0], "/blog/")
