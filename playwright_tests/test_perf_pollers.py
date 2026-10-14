"""Browser regressions for the bounded polling lifecycle on live pages."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_staff_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = [
    pytest.mark.core,
    pytest.mark.local_only,
    pytest.mark.django_db(transaction=True),
]


def _staff(email):
    user = create_staff_user(email)
    connection.close()
    return user


def _flush_promises(page):
    page.evaluate("() => Promise.resolve()")


def _set_page_visibility(page, state):
    page.evaluate(
        """
        state => {
            window.__testVisibilityState = state;
            if (!window.__testVisibilityInstalled) {
                Object.defineProperty(document, 'visibilityState', {
                    configurable: true,
                    get: () => window.__testVisibilityState,
                });
                Object.defineProperty(document, 'hidden', {
                    configurable: true,
                    get: () => window.__testVisibilityState === 'hidden',
                });
                window.__testVisibilityInstalled = true;
            }
            document.dispatchEvent(new Event('visibilitychange'));
        }
        """,
        state,
    )


@pytest.mark.parametrize("status", [401, 403])
@browser_journey
def test_notification_poll_stops_after_auth_failure(
    django_server,
    browser,
    status,
):
    email = f"poll-header-{status}@test.com"
    _staff(email)
    context = auth_context(browser, email)
    page = context.new_page()
    page.clock.install()
    calls = []

    def reject(route):
        calls.append(route.request.url)
        route.fulfill(status=status, body="signed out")

    page.route("**/api/notifications/unread-count", reject)
    with page.expect_response("**/api/notifications/unread-count"):
        page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
    _flush_promises(page)

    page.clock.fast_forward(3_600_000)
    assert len(calls) == 1
    context.close()


@browser_journey
def test_worker_poll_never_overlaps_a_slow_response(django_server, browser):
    email = "poll-worker@test.com"
    _staff(email)
    context = auth_context(browser, email)
    page = context.new_page()
    page.clock.install()
    held = []

    def hold(route):
        held.append(route)

    fragment = "**/studio/worker/?fragment=pending"
    page.route(fragment, hold)
    page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")

    with page.expect_request(fragment):
        page.clock.fast_forward(3000)
    page.clock.fast_forward(60_000)
    assert len(held) == 1

    held[0].fulfill(
        status=200,
        content_type="text/html",
        body='<div data-testid="worker-poll-result">Updated once</div>',
    )
    expect(page.get_by_test_id("worker-poll-result")).to_have_text("Updated once")

    with page.expect_request(fragment):
        page.clock.fast_forward(3000)
    _flush_promises(page)
    assert len(held) == 2
    held[1].fulfill(status=200, content_type="text/html", body="<div></div>")
    context.close()


@browser_journey
def test_sync_poll_backs_off_and_stops_when_sync_finishes(django_server, browser):
    from integrations.models import ContentSource

    email = "poll-sync@test.com"
    _staff(email)
    ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/poll-sync-test",
        last_sync_status="running",
    )
    connection.close()

    context = auth_context(browser, email)
    page = context.new_page()
    page.clock.install()
    calls = []

    def respond(route):
        calls.append(route.request.url)
        if len(calls) == 1:
            route.fulfill(status=500, body="temporary failure")
            return
        route.fulfill(
            status=200,
            content_type="text/html",
            body=(
                '<div id="sync-repos-section" '
                'data-any-running="false"></div>'
            ),
        )

    fragment = "**/studio/sync/?fragment=status"
    page.route(fragment, respond)
    page.goto(f"{django_server}/studio/sync/", wait_until="domcontentloaded")
    expect(page.locator("#sync-live-indicator")).to_have_attribute(
        "data-polling", "true",
    )

    with page.expect_response(fragment) as failed:
        page.clock.fast_forward(3000)
    assert failed.value.status == 500
    _flush_promises(page)

    _set_page_visibility(page, "hidden")
    assert page.evaluate("document.visibilityState") == "hidden"
    _set_page_visibility(page, "visible")
    assert page.evaluate("document.visibilityState") == "visible"

    page.clock.fast_forward(5999)
    assert len(calls) == 1
    with page.expect_response(fragment):
        page.clock.fast_forward(1)

    expect(page.locator("#sync-repos-section")).to_have_attribute(
        "data-any-running", "false",
    )
    expect(page.locator("#sync-live-indicator")).to_have_attribute(
        "data-polling", "false",
    )
    page.clock.fast_forward(600_000)
    assert len(calls) == 2
    context.close()


@browser_journey
def test_import_poll_pauses_hidden_and_stops_when_finished(django_server, browser):
    from accounts.models import ImportBatch

    email = "poll-import@test.com"
    staff = _staff(email)
    batch = ImportBatch.objects.create(
        source="stripe",
        actor=staff,
        status=ImportBatch.STATUS_RUNNING,
    )
    batch_id = batch.pk
    connection.close()

    context = auth_context(browser, email)
    page = context.new_page()
    page.clock.install()
    calls = []

    def finish(route):
        calls.append(route.request.url)
        route.fulfill(
            status=200,
            content_type="text/html",
            body=(
                '<div id="import-status-panel" data-finished="true">'
                '<p data-testid="import-poll-result">Completed</p>'
                "</div>"
            ),
        )

    fragment = f"**/studio/imports/{batch_id}/fragment/"
    page.route(fragment, finish)
    page.goto(
        f"{django_server}/studio/imports/{batch_id}/",
        wait_until="domcontentloaded",
    )

    _set_page_visibility(page, "hidden")
    assert page.evaluate("document.visibilityState") == "hidden"
    page.clock.fast_forward(60_000)
    assert calls == []

    _set_page_visibility(page, "visible")
    assert page.evaluate("document.visibilityState") == "visible"
    with page.expect_response(fragment):
        page.clock.fast_forward(5000)
    expect(page.get_by_test_id("import-poll-result")).to_have_text("Completed")

    page.clock.fast_forward(600_000)
    assert len(calls) == 1
    context.close()
