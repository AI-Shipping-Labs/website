"""Playwright browser-lifecycle policy owners relocated from the E2E suite (#1485).

These nodes prove harness cleanup, not a product journey. Cross-node probes run
in focused pytest subprocesses with a synthetic browser so the Django gate does
not depend on the product Playwright suite or a Chromium install.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

from django.test import SimpleTestCase

from playwright_tests.conftest import (
    _browser_lifecycle_error,
    _close_browser_contexts,
)

ROOT = Path(__file__).resolve().parents[1]
PROBE_ROOT = ROOT / ".tmp"


class _FakeContext:
    def __init__(self, browser, label, *, close_error=None):
        self.browser = browser
        self.label = label
        self.close_error = close_error
        self.pages = [object()]
        self.close_calls = 0

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise RuntimeError(self.close_error)
        self.browser.contexts.remove(self)


class _FakeBrowser:
    def __init__(self):
        self.contexts = []

    def add_context(self, label, *, close_error=None):
        context = _FakeContext(self, label, close_error=close_error)
        self.contexts.append(context)
        return context


_PROBE_HARNESS = textwrap.dedent(
    """
    import pytest

    from playwright_tests.conftest import (
        _browser_node_lifecycle as _lifecycle_fixture,
        _browser_resource_counts,
        page as _page_fixture,
    )


    class _FakePage:
        def __init__(self):
            self._title = ""

        def goto(self, url, wait_until=None):
            marker = "<title>"
            if marker in url:
                start = url.find(marker) + len(marker)
                end = url.find("</title>", start)
                self._title = url[start:end] if end != -1 else ""

        def title(self):
            return self._title


    class _FakeContext:
        def __init__(self, browser):
            self.browser = browser
            self.pages = []

        def new_page(self):
            page = _FakePage()
            self.pages.append(page)
            return page

        def close(self):
            self.browser.contexts.remove(self)


    class _FakeBrowser:
        def __init__(self):
            self.contexts = []

        def new_context(self, **kwargs):
            context = _FakeContext(self)
            self.contexts.append(context)
            return context


    @pytest.fixture(scope="session")
    def browser():
        yield _FakeBrowser()


    @pytest.fixture
    def page(browser):
        yield from _page_fixture.__wrapped__(browser)


    @pytest.fixture(autouse=True)
    def _browser_node_lifecycle(request):
        yield from _lifecycle_fixture.__wrapped__(request)


    def _resource_counts(browser):
        return _browser_resource_counts(browser)
    """
)


def _run_lifecycle_probe(body: str) -> subprocess.CompletedProcess[str]:
    probe_dir = PROBE_ROOT / f"browser-lifecycle-1485-{os.getpid()}-{uuid.uuid4().hex}"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_file = probe_dir / "test_browser_lifecycle_probe.py"
    probe_file.write_text(_PROBE_HARNESS + "\n" + textwrap.dedent(body), encoding="utf-8")
    env = os.environ.copy()
    env["PLAYWRIGHT_BASE_URL"] = "https://example.invalid"
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:django",
                "-p",
                "no:playwright",
                "--confcutdir",
                str(probe_dir),
                str(probe_file),
                "-v",
                "-s",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        shutil.rmtree(probe_dir)


class BrowserLifecyclePolicyTests(SimpleTestCase):
    def test_cleanup_attempts_every_context_and_aggregates_all_errors(self):
        browser = _FakeBrowser()
        first = browser.add_context("first", close_error="first close failed")
        middle = browser.add_context("middle")
        last = browser.add_context("last", close_error="last close failed")

        cleanup = _close_browser_contexts(browser)
        message = _browser_lifecycle_error(
            "probe.py::test_owner",
            "teardown",
            cleanup,
        )

        self.assertEqual(first.close_calls, 1)
        self.assertEqual(middle.close_calls, 1)
        self.assertEqual(last.close_calls, 1)
        self.assertEqual(cleanup["before_contexts"], 3)
        self.assertEqual(cleanup["before_pages"], 3)
        self.assertEqual(cleanup["after_contexts"], 2)
        self.assertEqual(cleanup["after_pages"], 2)
        self.assertIn("Node: probe.py::test_owner", message)
        self.assertIn("Before cleanup: 3 contexts / 3 pages", message)
        self.assertIn("After cleanup: 2 contexts / 2 pages", message)
        self.assertIn("Context close errors (2):", message)
        self.assertIn("context 1: RuntimeError: first close failed", message)
        self.assertIn("context 3: RuntimeError: last close failed", message)

    def test_static_node_does_not_require_browser(self):
        result = _run_lifecycle_probe(
            """
            def test_static_node_does_not_require_browser(request):
                assert "browser" not in request.fixturenames
                print("policy node fixturenames exclude browser")
            """
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("policy node fixturenames exclude browser", output)

    def test_node_after_direct_contexts_starts_with_zero_resources(self):
        result = _run_lifecycle_probe(
            """
            def test_01_direct_contexts(browser):
                context = browser.new_context()
                context.new_page()
                raw_context = browser.new_context()
                raw_context.new_page()
                raw_context.new_page()
                assert _resource_counts(browser) == (2, 3)
                print("direct contexts live: 2 contexts / 3 pages")

            def test_02_after_direct_contexts_starts_empty(browser):
                assert _resource_counts(browser) == (0, 0)
                print("following node state: 0 contexts / 0 pages")
            """
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("direct contexts live: 2 contexts / 3 pages", output)
        self.assertIn("following node state: 0 contexts / 0 pages", output)
        self.assertIn("2 passed", output)

    def test_node_after_page_fixture_starts_with_zero_resources(self):
        result = _run_lifecycle_probe(
            """
            def test_01_ordinary_page(page, browser):
                assert _resource_counts(browser) == (1, 1)
                page.goto("data:text/html,<title>page fixture lifecycle</title>")
                assert page.title() == "page fixture lifecycle"
                print("page fixture live: 1 context / 1 page")

            def test_02_after_page_fixture_starts_empty(browser):
                assert _resource_counts(browser) == (0, 0)
                print("following node state: 0 contexts / 0 pages")
            """
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("page fixture live: 1 context / 1 page", output)
        self.assertIn("following node state: 0 contexts / 0 pages", output)
        self.assertIn("2 passed", output)

    def test_intentional_failure_probe_still_cleans_before_following_node(self):
        result = _run_lifecycle_probe(
            """
            @pytest.fixture
            def owner_setup_error(browser):
                context = browser.new_context()
                context.new_page()
                raise RuntimeError("intentional owner setup error")


            def test_01_intentional_owner_failure(browser):
                context = browser.new_context()
                context.new_page()
                assert False, "intentional owner failure"


            def test_02_after_failure_starts_empty(browser):
                assert _resource_counts(browser) == (0, 0)


            def test_03_intentional_owner_skip(browser):
                context = browser.new_context()
                context.new_page()
                pytest.skip("intentional owner skip")


            def test_04_after_skip_starts_empty(browser):
                assert _resource_counts(browser) == (0, 0)


            def test_05_intentional_owner_setup_error(owner_setup_error):
                raise AssertionError("setup error should prevent this body")


            def test_06_after_error_starts_empty(browser):
                assert _resource_counts(browser) == (0, 0)
                print("following nodes state: 0 contexts / 0 pages")
            """
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("intentional owner failure", output)
        self.assertIn("intentional owner setup error", output)
        self.assertIn("1 failed, 3 passed, 1 skipped", output)
        self.assertIn("1 error", output)
        self.assertIn("following nodes state: 0 contexts / 0 pages", output)
