"""Playwright E2E tests for the login-flash isolation bug (issue #347).

Two scenarios from the spec:

1. Login flash is consumed on first page render and never replayed.
   After a flash message is queued, the next page render must show it
   exactly once. Reloading or navigating must not re-show it.

2. Login flash from one user does not leak into another user's session.
   When user A queues a flash on a shared browser, user B logging in on
   a fresh context must not see anything that mentions user A or A's
   flash text.

Implementation note:
    This codebase replaces allauth's stock ``account_login`` view with a
    custom ``login_view`` that POSTs to ``/api/login`` and uses Django's
    plain ``django.contrib.auth.login()`` — which does NOT trigger
    allauth's "Successfully signed in as <name>" flash. The bug we fix
    is generic to any ``django.contrib.messages`` flash that lands in
    the queue and is not drained by the page the user is redirected
    to. We exercise that exact lifecycle by queuing a real Studio flash
    via the ``studio_worker_drain_queue`` endpoint (which always emits
    ``messages.info('Queue is already empty.')``) and verifying that
    drains happen on the very next render.
"""

import os

import pytest

from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

FLASH_TEXT = "Queue is already empty."

VIEWPORT = {"width": 1280, "height": 720}


def _auth_with_db(browser, email, db_blocker):
    """Create an authenticated Playwright BrowserContext for ``email``.

    Wraps ``create_session_for_user`` in ``db_blocker.unblock()`` so it
    runs in the test thread (Playwright tests use
    ``@pytest.mark.django_db(transaction=True)``).
    """
    with db_blocker.unblock():
        session_key = _create_session_for_user(email)
    context = browser.new_context(viewport=VIEWPORT)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _get_csrf_from_cookies(context):
    """Read the csrftoken cookie value from a Playwright BrowserContext."""
    for c in context.cookies():
        if c["name"] == "csrftoken":
            return c["value"]
    return ""


def _trigger_drain_queue_flash(page, base_url):
    """POST to the drain-queue endpoint to deposit a real flash message.

    Returns the redirect-target Response so the caller can assert on
    where allauth-style flow lands the user.
    """
    csrf = _get_csrf_from_cookies(page.context)
    return page.request.post(
        f"{base_url}/studio/worker/queue/drain/",
        headers={
            "X-CSRFToken": csrf,
            "Cookie": "; ".join(
                f"{c['name']}={c['value']}" for c in page.context.cookies()
            ),
            "Referer": f"{base_url}/studio/worker/",
        },
        max_redirects=0,
    )


@pytest.mark.django_db(transaction=True)
class TestLoginFlashConsumedOnce:
    """Scenario 1: Flash message is drained on the very next page."""

    def test_flash_renders_once_then_drains(
        self, django_server, browser, django_db_blocker
    ):
        """Given: A staff user with a clean browser context.
        1. Trigger a real server-side flash message via the
           studio drain-queue endpoint.
        Then: The /studio/ page renders the flash exactly once.
        2. Reload /studio/.
        Then: The flash is gone — the previous render drained it.
        3. Navigate to /studio/settings/.
        Then: The flash is still gone (drained, not just hidden).
        """
        with django_db_blocker.unblock():
            _create_staff_user(email="staff-flash@test.com")

        context = _auth_with_db(
            browser, "staff-flash@test.com", django_db_blocker
        )
        page = context.new_page()

        # Visit a Studio page first so the CSRF cookie is set.
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # Deposit a real flash via the drain-queue endpoint.
        post = _trigger_drain_queue_flash(page, django_server)
        assert post.status in (302, 200), (
            f"drain-queue failed: status={post.status} body={post.text()[:200]}"
        )

        # First render after the flash: should show it exactly once.
        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
        body_first = page.content()
        assert body_first.count(FLASH_TEXT) == 1, (
            f"expected flash exactly once on first render, "
            f"got {body_first.count(FLASH_TEXT)}"
        )

        # Reload — the message must have been drained on the previous
        # render and must NOT reappear.
        page.reload(wait_until="domcontentloaded")
        body_reload = page.content()
        assert FLASH_TEXT not in body_reload, (
            "flash leaked into a second render of the same page"
        )

        # Navigate elsewhere in Studio — still drained.
        page.goto(
            f"{django_server}/studio/settings/", wait_until="domcontentloaded"
        )
        body_settings = page.content()
        assert FLASH_TEXT not in body_settings, (
            "flash leaked into /studio/settings/ after first render"
        )

        context.close()


@pytest.mark.django_db(transaction=True)
class TestLoginFlashIsolatedAcrossSessions:
    """Scenario 2: User A's flash does not leak into user B's session."""

    def test_flash_from_user_a_does_not_appear_for_user_b(
        self, django_server, browser, django_db_blocker
    ):
        """Given: Two distinct staff users.
        1. User A logs in (via session cookie injection) and triggers a
           server-side flash on their session.
        2. User B logs in via a SECOND browser context with completely
           different cookies.
        Then: User B's first /studio/ render must not contain the flash
              text deposited for user A.
        Then: Navigating user B around Studio still shows no leak.
        """
        with django_db_blocker.unblock():
            _create_staff_user(email="user-a@test.com")
            _create_staff_user(email="user-b@test.com")

        # User A: triggers a flash that sits in their session.
        ctx_a = _auth_with_db(browser, "user-a@test.com", django_db_blocker)
        page_a = ctx_a.new_page()
        page_a.goto(
            f"{django_server}/studio/", wait_until="domcontentloaded"
        )
        post = _trigger_drain_queue_flash(page_a, django_server)
        assert post.status in (302, 200)
        # Don't render the flash for A — we want it sitting unrendered
        # in A's storage, then assert B doesn't get it. (A would still
        # see it on their next render — that's correct behaviour.)
        ctx_a.close()

        # User B: completely fresh context, completely different session.
        ctx_b = _auth_with_db(browser, "user-b@test.com", django_db_blocker)
        page_b = ctx_b.new_page()
        page_b.goto(
            f"{django_server}/studio/", wait_until="domcontentloaded"
        )
        body_b_studio = page_b.content()
        assert FLASH_TEXT not in body_b_studio, (
            "user-b saw user-a's flash on /studio/ — cross-session leak!"
        )
        # The literal email of user A must never appear in user B's DOM
        # via a flash. (It can appear via legitimate UI such as a user
        # list, so we look specifically inside the messages region.)
        messages_region = page_b.locator('[data-testid="messages-region"]')
        if messages_region.count() > 0:
            messages_html = messages_region.inner_html()
            assert "user-a@test.com" not in messages_html, (
                "user-a's email leaked into user-b's messages region"
            )

        # Same on a different studio sub-page.
        page_b.goto(
            f"{django_server}/studio/settings/",
            wait_until="domcontentloaded",
        )
        body_b_settings = page_b.content()
        assert FLASH_TEXT not in body_b_settings, (
            "user-b saw user-a's flash on /studio/settings/"
        )
        ctx_b.close()
