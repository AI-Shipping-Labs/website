"""Offline guard for /member-api/docs (issue #1846).

The docs page used to load swagger-ui-dist 5.17.14 from cdn.jsdelivr.net
with parser-blocking scripts, so DOMContentLoaded — and with it every
``wait_until="domcontentloaded"`` navigation — depended on a third-party
host responding within the navigation timeout. These tests abort every
request to a non-local host via ``page.route`` and prove the page still
navigates and renders the full interactive Swagger UI from the
self-hosted vendored assets under ``static/vendor/swagger-ui-dist/``.
"""

import os
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

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
class TestMemberApiDocsSelfHosted:
    @pytest.mark.core
    @browser_journey
    def test_docs_render_with_every_external_host_blocked(
        self, django_server, browser
    ):
        from django.db import connection

        email = "member-api-docs-offline@test.com"
        create_user(email, tier_slug="free")
        connection.close()

        context = auth_context(browser, email)
        page = context.new_page()
        external_requests = _block_external_hosts(page)

        response = page.goto(
            f"{django_server}/member-api/docs", wait_until="domcontentloaded"
        )
        assert response is not None
        assert response.status == 200

        # Swagger UI renders fully from self-hosted assets: the bundle
        # mounted #swagger-ui, the StandaloneLayout topbar and the spec info
        # title are visible, and the endpoint groups are listed.
        docs = page.locator('[data-testid="member-api-docs"]')
        expect(docs.locator(".swagger-ui").first).to_be_visible()
        expect(docs.locator(".topbar")).to_be_visible()
        expect(docs.locator(".info .title")).to_contain_text(
            "AI Shipping Labs Member API"
        )
        expect(docs.locator(".opblock-tag-section").first).to_be_visible()

        # The on-site header bar with the usage guide link is untouched: on
        # the docs page it points at the GitHub usage guide.
        expect(
            page.locator('[data-testid="member-api-usage-guide-link"]')
        ).to_have_attribute(
            "href",
            "https://github.com/AI-Shipping-Labs/website/blob/main/"
            "docs/member-api/plans.md",
        )

        assert external_requests == []

        connection.close()
        context.close()

    @pytest.mark.core
    @browser_journey
    def test_docs_stay_interactive_with_every_external_host_blocked(
        self, django_server, browser
    ):
        from django.db import connection

        email = "member-api-docs-offline-try@test.com"
        create_user(email, tier_slug="free")
        connection.close()

        context = auth_context(browser, email)
        page = context.new_page()
        external_requests = _block_external_hosts(page)

        response = page.goto(
            f"{django_server}/member-api/docs", wait_until="domcontentloaded"
        )
        assert response is not None
        assert response.status == 200

        docs = page.locator('[data-testid="member-api-docs"]')
        expect(docs.locator(".opblock-tag-section").first).to_be_visible()

        # Expand GET /member-api/v1/plans; deep linking updates the URL
        # fragment once an operation is open.
        opblock = page.locator(
            ".opblock-get",
            has=page.locator(
                ".opblock-summary-path",
                has_text=re.compile(r"^/member-api/v1/plans$"),
            ),
        ).first
        opblock.locator(".opblock-summary").click()
        expect(opblock).to_have_class(re.compile(r"is-open"))
        assert "#/" in page.url

        # "Try it out" turns the docs into a working console: the page query
        # parameter becomes an editable field and the Execute control
        # appears.
        opblock.get_by_role("button", name="Try it out").click()
        expect(opblock.locator(".execute")).to_be_visible()
        expect(opblock.locator(".opblock-body input").first).to_be_editable()

        assert external_requests == []

        connection.close()
        context.close()
