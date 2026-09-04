"""Regression coverage for shared-browser per-node ownership (#1418)."""

import pytest

from playwright_tests.conftest import (
    _browser_resource_counts,
    auth_context,
    create_staff_user,
)

pytestmark = pytest.mark.local_only

_SESSION_BROWSER = None


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


def test_ordinary_page_uses_one_context_in_same_session(page, browser):
    assert browser is _SESSION_BROWSER
    assert _browser_resource_counts(browser) == (1, 1)
    page.goto("data:text/html,<title>page fixture lifecycle</title>")
    assert page.title() == "page fixture lifecycle"
