"""Playwright coverage for standalone marketing pages (#1180)."""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

pytestmark = pytest.mark.local_only


def _create_page(**overrides):
    from content.models import MarketingPage

    defaults = {
        "title": "Community Story",
        "public_path": "/community-story",
        "description": "A standalone story for the community.",
        "content_markdown": "# Community Story\n\nOperator-created body.",
        "status": "published",
        "nav_section": "none",
        "nav_order": 10,
    }
    defaults.update(overrides)
    page = MarketingPage.objects.create(**defaults)
    connection.close()
    return page


def _create_api_token(email):
    from accounts.models import Token

    user = _create_staff_user(email)
    token, plaintext = Token.create_for_user(user=user, name="marketing pages")
    connection.close()
    return token, plaintext


@pytest.mark.django_db(transaction=True)
def test_staff_publishes_manual_marketing_page_from_studio(django_server, browser):
    staff_email = "marketing-page-studio@test.com"
    _create_staff_user(staff_email)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    try:
        page.goto(
            f"{django_server}/studio/marketing-pages/new",
            wait_until="domcontentloaded",
        )

        page.locator('input[name="title"]').fill("Studio Launch Page")
        page.locator('[data-testid="marketing-page-public-path"]').fill(
            "/studio-launch-page"
        )
        page.locator('textarea[name="description"]').fill(
            "A standalone launch page."
        )
        page.locator('textarea[name="content_markdown"]').fill(
            "# Studio Launch Page\n\nStandalone body from Studio."
        )
        page.locator('[data-testid="marketing-page-status"]').select_option(
            "published"
        )
        page.locator(
            '[data-testid="marketing-page-nav-section"]'
        ).select_option("community")
        page.locator('[data-testid="marketing-page-nav-label"]').fill(
            "Launch Page"
        )
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert "/studio/marketing-pages/" in page.url
        expect(page.locator('[data-testid="view-on-site"]')).to_be_visible()
        expect(page.get_by_text("/studio-launch-page")).to_be_visible()
        expect(page.locator('[data-testid="marketing-page-public-path"]')).to_have_value(
            "/studio-launch-page"
        )

        response = page.goto(
            f"{django_server}/studio-launch-page",
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        expect(page.get_by_role("heading", name="Studio Launch Page")).to_be_visible()
        expect(page.get_by_text("Standalone body from Studio.")).to_be_visible()
        expect(page.locator("footer")).to_be_visible()
        expect(page.get_by_text("Back to Blog")).to_have_count(0)
        expect(page.get_by_text("Register for this event")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_studio_rejects_reserved_route_without_shadowing_it(django_server, browser):
    from content.models import MarketingPage

    staff_email = "marketing-page-collision@test.com"
    _create_staff_user(staff_email)
    pages_before = MarketingPage.objects.count()
    connection.close()

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    try:
        page.goto(
            f"{django_server}/studio/marketing-pages/new",
            wait_until="domcontentloaded",
        )
        page.locator('input[name="title"]').fill("Events Collision")
        page.locator('[data-testid="marketing-page-public-path"]').fill("/events")
        page.locator('textarea[name="content_markdown"]').fill("Do not publish.")
        page.locator('[data-testid="marketing-page-status"]').select_option(
            "published"
        )
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_load_state("domcontentloaded")

        expect(page.locator('[data-testid="form-errors"]')).to_contain_text(
            "conflicts with an existing route"
        )
        public_path = page.get_by_test_id("marketing-page-public-path")
        expect(public_path).to_have_value("/events")
        expect(public_path).to_have_attribute("aria-invalid", "true")
        expect(public_path).to_have_attribute(
            "aria-describedby", "marketing-page-public-path-error"
        )
        error = page.locator("#marketing-page-public-path-error")
        expect(error).to_have_attribute("role", "alert")
        expect(error).to_have_attribute("aria-live", "polite")
        assert MarketingPage.objects.count() == pages_before
        connection.close()

        response = page.goto(f"{django_server}/events", wait_until="domcontentloaded")
        assert response.status == 200
        expect(page.get_by_text("Events Collision")).to_have_count(0)
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_create_prefill_yields_to_manual_path_and_survives_validation(
    django_server, browser
):
    staff_email = "marketing-page-prefill@test.com"
    _create_staff_user(staff_email)
    _create_page(
        title="Existing Manual Destination",
        public_path="/manual-destination",
        status="draft",
    )
    _create_page(
        title="Existing Pasted Destination",
        public_path="/pasted-destination",
        status="draft",
    )

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    try:
        page.goto(
            f"{django_server}/studio/marketing-pages/new",
            wait_until="domcontentloaded",
        )
        title = page.locator('input[name="title"]')
        public_path = page.get_by_test_id("marketing-page-public-path")
        title.fill("Generated Launch Page")
        page.locator('textarea[name="description"]').click()
        expect(public_path).to_have_value("/generated-launch-page")

        public_path.click()
        page.keyboard.type("/manual-destination")
        expect(public_path).to_have_value("/manual-destination")
        title.fill("Renamed Launch Page")
        page.locator('textarea[name="content_markdown"]').fill("Manual body")
        page.get_by_test_id("sticky-save-action").click()
        expect(page.get_by_test_id("form-errors")).to_contain_text(
            "already uses this public path"
        )
        expect(public_path).to_have_value("/manual-destination")
        expect(title).to_have_value("Renamed Launch Page")

        page.goto(
            f"{django_server}/studio/marketing-pages/new",
            wait_until="domcontentloaded",
        )
        title = page.locator('input[name="title"]')
        public_path = page.get_by_test_id("marketing-page-public-path")
        title.fill("Generated Paste Page")
        page.locator('textarea[name="description"]').click()
        expect(public_path).to_have_value("/generated-paste-page")
        context.grant_permissions(
            ["clipboard-read", "clipboard-write"], origin=django_server
        )
        page.evaluate("navigator.clipboard.writeText('/pasted-destination')")
        public_path.click()
        page.keyboard.press("Control+V")
        expect(public_path).to_have_value("/pasted-destination")
        page.get_by_test_id("sticky-save-action").click()
        expect(page.get_by_test_id("form-errors")).to_contain_text(
            "already uses this public path"
        )
        expect(public_path).to_have_value("/pasted-destination")
    finally:
        context.close()


@pytest.mark.django_db(transaction=True)
def test_marketing_pages_appear_in_desktop_and_mobile_nav(django_server, browser):
    _create_page(
        title="Community Field Guide",
        public_path="/community-field-guide",
        nav_section="community",
        nav_label="Field Guide",
    )
    _create_page(
        title="Builder Resources",
        public_path="/builder-resources",
        nav_section="resources",
        nav_label="Builder Resources",
        nav_order=20,
    )

    desktop = browser.new_context(viewport={"width": 1280, "height": 720})
    page = desktop.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator('[data-testid="nav-community-trigger"]').hover()
        community_link = page.locator(
            '[data-testid="nav-community-menu"] a',
            has_text="Field Guide",
        )
        expect(community_link).to_be_visible()
        community_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/community-field-guide")
    finally:
        desktop.close()

    mobile = browser.new_context(viewport={"width": 390, "height": 844})
    page = mobile.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator("#mobile-menu-btn").click()
        page.locator('[data-testid="mobile-nav-resources-trigger"]').click()
        resources_link = page.locator(
            '[data-testid="mobile-nav-resources-menu"] a',
            has_text="Builder Resources",
        )
        expect(resources_link).to_be_visible()
        resources_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/builder-resources")
    finally:
        mobile.close()


@pytest.mark.django_db(transaction=True)
def test_draft_preview_is_private_and_noindexed(django_server, browser):
    staff_email = "marketing-page-preview@test.com"
    _create_staff_user(staff_email)
    draft = _create_page(
        title="Draft Campaign Page",
        public_path="/draft-campaign-page",
        content_markdown="# Draft Campaign Page\n\nPreview-only copy.",
        status="draft",
        show_in_sitemap=False,
        nav_section="resources",
    )

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    try:
        page.goto(
            f"{django_server}/studio/marketing-pages/{draft.pk}/edit",
            wait_until="domcontentloaded",
        )
        preview_url = page.locator('[data-testid="draft-preview-url"]').input_value()

        response = page.goto(preview_url, wait_until="domcontentloaded")
        assert response.status == 200
        expect(page.locator('[data-testid="draft-preview-banner"]')).to_be_visible()
        assert (
            page.locator('meta[name="robots"]').get_attribute("content")
            == "noindex,nofollow,noarchive"
        )
        expect(page.locator('link[rel="canonical"]')).to_have_count(0)
    finally:
        context.close()

    public = browser.new_context(viewport={"width": 1280, "height": 720})
    public_page = public.new_page()
    try:
        response = public_page.goto(
            f"{django_server}/draft-campaign-page",
            wait_until="domcontentloaded",
        )
        assert response.status == 404

        public_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        public_page.locator('[data-testid="nav-resources-trigger"]').hover()
        expect(public_page.get_by_text("Draft Campaign Page")).to_have_count(0)
    finally:
        public.close()


@pytest.mark.django_db(transaction=True)
def test_staff_api_can_publish_page_without_navigation(django_server, browser):
    _, plaintext = _create_api_token("marketing-page-api@test.com")
    context = browser.new_context(viewport={"width": 1280, "height": 720})
    page = context.new_page()
    try:
        response = page.request.post(
            f"{django_server}/api/marketing-pages",
            headers={"Authorization": f"Token {plaintext}"},
            data={
                "title": "API Launch Page",
                "public_path": "/api-launch-page",
                "content_markdown": "# API Launch Page\n\nPublished through API.",
                "status": "published",
                "nav_section": "none",
            },
        )
        assert response.status == 201
        body = response.json()
        assert body["public_path"] == "/api-launch-page"

        public_response = page.goto(
            f"{django_server}/api-launch-page",
            wait_until="domcontentloaded",
        )
        assert public_response.status == 200
        expect(page.get_by_text("Published through API.")).to_be_visible()
        expect(page.locator('[data-testid^="nav-community-link-marketing"]')).to_have_count(
            0
        )

        deleted = page.request.delete(
            f"{django_server}/api/marketing-pages/{body['content_id']}",
            headers={"Authorization": f"Token {plaintext}"},
        )
        assert deleted.status == 405
        assert deleted.json()["code"] == "marketing_page_delete_not_available"
    finally:
        context.close()
