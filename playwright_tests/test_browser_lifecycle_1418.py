"""Regression coverage for shared-browser per-node ownership (#1418)."""

import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    _browser_lifecycle_error,
    _browser_resource_counts,
    _close_browser_contexts,
    auth_context,
    create_staff_user,
)

pytestmark = pytest.mark.local_only

_SESSION_BROWSER = None


def test_static_node_does_not_require_browser(request):
    """The autouse boundary must not add Chromium to a static node."""
    assert "browser" not in request.fixturenames


@pytest.mark.django_db(transaction=True)
def test_direct_contexts_own_live_header_and_worker_pollers(
    django_server,
    browser,
):
    """Direct auth/raw contexts remain open so shared teardown must own them."""
    global _SESSION_BROWSER

    staff_email = "browser-lifecycle-1418@test.com"
    create_staff_user(staff_email)
    _SESSION_BROWSER = browser

    context = auth_context(browser, staff_email)
    header_page = context.new_page()
    with header_page.expect_request(
        lambda request: "/api/notifications/unread-count" in request.url
    ) as unread_request:
        header_page.goto(django_server, wait_until="domcontentloaded")
    assert unread_request.value.method == "GET"

    worker_page = context.new_page()
    with worker_page.expect_request(
        lambda request: "/studio/worker/?fragment=pending" in request.url
    ) as pending_request:
        worker_page.goto(
            f"{django_server}/studio/worker/",
            wait_until="domcontentloaded",
        )
    assert pending_request.value.method == "GET"

    raw_context = browser.new_context()
    raw_context.new_page()
    assert _browser_resource_counts(browser) == (2, 3)


def test_node_after_direct_contexts_starts_with_zero_resources(browser):
    assert browser is _SESSION_BROWSER
    assert _browser_resource_counts(browser) == (0, 0)


def test_ordinary_page_uses_one_context_in_same_session(page, browser):
    assert browser is _SESSION_BROWSER
    assert _browser_resource_counts(browser) == (1, 1)
    page.goto("data:text/html,<title>page fixture lifecycle</title>")
    assert page.title() == "page fixture lifecycle"


def test_node_after_page_fixture_starts_with_zero_resources(browser):
    assert browser is _SESSION_BROWSER
    assert _browser_resource_counts(browser) == (0, 0)


def test_intentional_failure_probe_still_cleans_before_following_node():
    """Run a red owner node in a child pytest session without reddening this run."""
    probe_dir = (
        Path.cwd()
        / ".tmp"
        / f"browser-lifecycle-1418-probe-{os.getpid()}-{uuid.uuid4().hex}"
    )
    probe_dir.mkdir(parents=True)
    probe_file = probe_dir / "test_failure_cleanup_probe.py"
    probe_file.write_text(
        textwrap.dedent(
            """
            import pytest


            @pytest.fixture
            def owner_setup_error(browser):
                context = browser.new_context()
                context.new_page()
                raise RuntimeError("intentional owner setup error")


            def _resource_counts(browser):
                contexts = list(browser.contexts)
                return len(contexts), sum(len(context.pages) for context in contexts)


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
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PLAYWRIGHT_BASE_URL"] = "https://example.invalid"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:django",
                "--confcutdir",
                str(probe_dir),
                "-p",
                "playwright_tests.conftest",
                str(probe_file),
                "-v",
                "-s",
            ],
            cwd=Path.cwd(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        shutil.rmtree(probe_dir)

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "intentional owner failure" in output
    assert "intentional owner setup error" in output
    assert "1 failed, 3 passed, 1 skipped" in output
    assert "1 error" in output
    assert "following nodes state: 0 contexts / 0 pages" in output


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


def test_cleanup_attempts_every_context_and_aggregates_all_errors():
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

    assert first.close_calls == 1
    assert middle.close_calls == 1
    assert last.close_calls == 1
    assert cleanup["before_contexts"] == 3
    assert cleanup["before_pages"] == 3
    assert cleanup["after_contexts"] == 2
    assert cleanup["after_pages"] == 2
    assert "Node: probe.py::test_owner" in message
    assert "Before cleanup: 3 contexts / 3 pages" in message
    assert "After cleanup: 2 contexts / 2 pages" in message
    assert "Context close errors (2):" in message
    assert "context 1: RuntimeError: first close failed" in message
    assert "context 3: RuntimeError: last close failed" in message
