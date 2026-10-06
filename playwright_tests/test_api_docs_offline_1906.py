"""Offline guard for /api/docs (issue #1906).

The operator API docs page still loaded swagger-ui-dist 5.17.14 from
cdn.jsdelivr.net after #1846 vendored the same assets for the member
docs. This test aborts every request to a non-local host via
``page.route`` and proves the page still renders the full interactive
Swagger UI from the self-hosted vendored assets under
``static/vendor/swagger-ui-dist/``.
"""

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1906")

LOCAL_HOSTS = {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _block_external_hosts(page):
    """Abort every request to a non-local host and record its URL."""

    external_requests = []

    def handler(route):
        host = urlparse(route.request.url).hostname or ""
        if host in LOCAL_HOSTS:
            route.continue_()
            return
        external_requests.append(route.request.url)
        route.abort()

    page.route("**/*", handler)
    return external_requests


@pytest.mark.django_db(transaction=True)
class TestApiDocsSelfHosted:
    @pytest.mark.core
    @browser_journey
    def test_docs_render_with_every_external_host_blocked(
        self, django_server, browser
    ):
        from django.db import connection

        email = "api-docs-offline-staff@test.com"
        create_user(email, tier_slug="free", is_staff=True)
        connection.close()

        context = auth_context(browser, email)
        page = context.new_page()
        external_requests = _block_external_hosts(page)

        response = page.goto(
            f"{django_server}/api/docs", wait_until="domcontentloaded"
        )
        assert response is not None
        assert response.status == 200

        # Swagger UI renders fully from self-hosted assets: the bundle
        # mounted #swagger-ui, the StandaloneLayout topbar and the spec
        # info title are visible, and the endpoint groups are listed.
        docs = page.locator("#swagger-ui")
        expect(docs.locator(".swagger-ui").first).to_be_visible()
        expect(docs.locator(".topbar")).to_be_visible()
        expect(docs.locator(".info .title")).to_contain_text(
            "AI Shipping Labs Operator API"
        )
        expect(docs.locator(".opblock-tag-section").first).to_be_visible()

        assert external_requests == []

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=SCREENSHOT_DIR / "api-docs-offline.png", full_page=True
        )

        connection.close()
        context.close()
