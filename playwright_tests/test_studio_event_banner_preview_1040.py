"""Playwright E2E for Studio event banner preview (issue #1040)."""

import os
from datetime import datetime, timedelta, timezone

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

AUTO_BANNER_URL = "https://cdn.example.com/banners/event/auto.jpg"
CUSTOM_BANNER_URL = "https://cdn.example.com/banners/event/custom.jpg"
COVER_BANNER_URL = "https://cdn.example.com/banners/event/cover.png"
NEW_BANNER_URL = "https://cdn.example.com/banners/event/new.jpg"


def _reset_state():
    from django.db import connection
    from django_q.models import OrmQ, Task

    from events.models import Event, EventRegistration, EventSeries
    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    OrmQ.objects.all().delete()
    Task.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    IntegrationSetting.objects.filter(
        key__startswith="BANNER_GENERATOR_",
    ).delete()
    clear_config_cache()
    connection.close()


def _enable_banner_generator():
    from django.db import connection

    from integrations.config import clear_config_cache
    from integrations.models import IntegrationSetting

    for key, value in (
        ("BANNER_GENERATOR_FUNCTION_URL", "https://lambda.example.com/"),
        ("BANNER_GENERATOR_AUTH_TOKEN", "token-abc"),
        ("AWS_S3_CONTENT_BUCKET", "content-bucket"),
        ("CONTENT_CDN_BASE", "https://cdn.example.com"),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                "value": value,
                "is_secret": False,
                "group": "banner_generator",
                "description": "",
            },
        )
    clear_config_cache()
    connection.close()


def _make_event(slug, title, **overrides):
    from django.db import connection

    from events.models import Event

    defaults = dict(
        slug=slug,
        title=title,
        description="A live session on shipping agents.",
        start_datetime=datetime.now(timezone.utc) + timedelta(days=30),
        timezone="Europe/Berlin",
        status="upcoming",
        origin="studio",
        published=True,
    )
    defaults.update(overrides)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


def _open_preview(page):
    control = page.get_by_role("button", name="Open banner preview")
    control.click()
    dialog = page.locator('[data-testid="banner-preview-dialog"]')
    expect(dialog).to_be_visible()
    return dialog


@pytest.mark.django_db(transaction=True)
class TestStudioEventBannerPreview:

    @pytest.mark.core
    def test_staff_opens_and_closes_generated_banner_preview(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-preview-generated@test.com")
        event = _make_event(
            "evt-preview-generated",
            "Preview Generated",
            auto_banner_url=AUTO_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-preview-generated@test.com")
        page = ctx.new_page()

        edit_url = f"{django_server}/studio/events/{event.pk}/edit"
        page.goto(edit_url, wait_until="domcontentloaded")

        control = page.get_by_role("button", name="Open banner preview")
        expect(control).to_be_visible()
        expect(page.locator('[data-testid="banner-preview-affordance"]')).to_contain_text(
            "Preview",
        )

        dialog = _open_preview(page)
        preview_img = dialog.locator('[data-testid="banner-preview-image"]')
        expect(preview_img).to_have_attribute("src", AUTO_BANNER_URL)
        expect(dialog.locator('[data-testid="banner-preview-source"]')).to_have_text(
            "Source: Generated",
        )

        open_link = dialog.locator('[data-testid="banner-preview-open-link"]')
        expect(open_link).to_have_attribute("href", AUTO_BANNER_URL)
        expect(open_link).to_have_attribute("target", "_blank")
        expect(open_link).to_have_attribute("rel", "noopener noreferrer")

        page.keyboard.press("Escape")
        expect(dialog).not_to_be_visible()
        assert page.evaluate("document.activeElement.dataset.testid") == (
            "banner-preview-control"
        )
        assert page.url == edit_url
        ctx.close()

    @pytest.mark.core
    def test_custom_uploaded_banner_preview_uses_custom_url_and_label(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-preview-custom@test.com")
        event = _make_event(
            "evt-preview-custom",
            "Preview Custom",
            custom_banner_url=CUSTOM_BANNER_URL,
            auto_banner_url=AUTO_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-preview-custom@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        dialog = _open_preview(page)

        expect(dialog.locator('[data-testid="banner-preview-image"]')).to_have_attribute(
            "src",
            CUSTOM_BANNER_URL,
        )
        expect(dialog.locator('[data-testid="banner-preview-source"]')).to_have_text(
            "Source: Custom upload",
        )
        ctx.close()

    @pytest.mark.core
    def test_frontmatter_cover_preview_works_on_read_only_github_event(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-preview-cover@test.com")
        event = _make_event(
            "evt-preview-cover",
            "Preview Cover",
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/preview-cover.md",
            source_commit="evt1234def5678901234567890123456789abcde",
            cover_image_url=COVER_BANNER_URL,
            auto_banner_url=AUTO_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-preview-cover@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert page.locator('input[name="title"]').is_disabled()

        dialog = _open_preview(page)

        expect(dialog.locator('[data-testid="banner-preview-image"]')).to_have_attribute(
            "src",
            COVER_BANNER_URL,
        )
        expect(dialog.locator('[data-testid="banner-preview-source"]')).to_have_text(
            "Source: Frontmatter cover",
        )
        assert page.locator('input[name="title"]').is_disabled()
        ctx.close()

    @pytest.mark.core
    def test_keyboard_open_escape_close_returns_focus(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-preview-keyboard@test.com")
        event = _make_event(
            "evt-preview-keyboard",
            "Preview Keyboard",
            auto_banner_url=AUTO_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-preview-keyboard@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        control = page.get_by_role("button", name="Open banner preview")
        control.focus()
        page.keyboard.press("Enter")

        dialog = page.locator('[data-testid="banner-preview-dialog"]')
        expect(dialog).to_be_visible()
        page.keyboard.press("Escape")

        expect(dialog).not_to_be_visible()
        assert page.evaluate("document.activeElement.dataset.testid") == (
            "banner-preview-control"
        )
        ctx.close()

    @pytest.mark.core
    def test_close_button_does_not_submit_or_mutate_event_form(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-preview-nomutate@test.com")
        event = _make_event(
            "evt-preview-nomutate",
            "Preview No Mutate",
            auto_banner_url=AUTO_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-preview-nomutate@test.com")
        page = ctx.new_page()

        edit_url = f"{django_server}/studio/events/{event.pk}/edit"
        page.goto(edit_url, wait_until="domcontentloaded")
        page.fill('input[name="title"]', "Unsaved title")

        dialog = _open_preview(page)
        dialog.locator('[data-testid="banner-preview-close"]').click()

        expect(dialog).not_to_be_visible()
        assert page.url == edit_url
        assert page.locator('input[name="title"]').input_value() == "Unsaved title"

        from django.db import connection

        event.refresh_from_db()
        assert event.title == "Preview No Mutate"
        connection.close()
        ctx.close()

    @pytest.mark.core
    def test_no_banner_has_placeholder_without_dead_preview_control(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _create_staff_user("staff-preview-empty@test.com")
        event = _make_event("evt-preview-empty", "Preview Empty")
        ctx = _auth_context(browser, "staff-preview-empty@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        expect(page.locator('[data-testid="banner-generator-placeholder"]')).to_have_text(
            "No banner generated yet",
        )
        assert page.locator('[data-testid="banner-preview-control"]').count() == 0
        assert page.locator('[data-testid="banner-preview-dialog"]').count() == 0
        ctx.close()

    @pytest.mark.core
    def test_regenerated_thumbnail_url_is_used_by_preview(
        self, django_server, browser,
    ):
        _reset_state()
        _ensure_tiers()
        _enable_banner_generator()
        _create_staff_user("staff-preview-regen@test.com")
        event = _make_event(
            "evt-preview-regen",
            "Preview Regen",
            auto_banner_url=AUTO_BANNER_URL,
        )
        ctx = _auth_context(browser, "staff-preview-regen@test.com")
        page = ctx.new_page()

        regen_path = f"/studio/events/{event.pk}/regenerate-banner"
        status_path = f"/studio/events/{event.pk}/banner-status"
        page.route(
            f"**{regen_path}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body='{"status": "queued", "task_id": "task-stub"}',
            ),
        )
        page.route(
            f"**{status_path}",
            lambda route: route.fulfill(
                status=200,
                content_type="application/json",
                body=(
                    '{"state": "success", "banner_url": "'
                    + NEW_BANNER_URL
                    + '", "task_detail_url": null}'
                ),
            ),
        )

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="banner-generator-regenerate-button"]').click()
        page.wait_for_function(
            "(u) => { const i = document.querySelector("
            "'[data-testid=\"banner-generator-image\"]'); "
            "return i && i.src.indexOf(u) === 0 && i.src.includes('?t='); }",
            arg=NEW_BANNER_URL,
            timeout=8000,
        )
        updated_src = page.locator(
            '[data-testid="banner-generator-image"]',
        ).get_attribute("src")

        dialog = _open_preview(page)

        expect(dialog.locator('[data-testid="banner-preview-image"]')).to_have_attribute(
            "src",
            updated_src,
        )
        expect(dialog.locator('[data-testid="banner-preview-open-link"]')).to_have_attribute(
            "href",
            updated_src,
        )
        ctx.close()

    def test_anonymous_and_non_staff_cannot_reach_preview_surface(
        self, django_server, browser, page,
    ):
        _reset_state()
        _ensure_tiers()
        event = _make_event(
            "evt-preview-denied",
            "Preview Denied",
            auto_banner_url=AUTO_BANNER_URL,
        )

        anon_response = page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert anon_response.status in (302, 403, 404) or "/accounts/login" in page.url
        assert page.locator('[data-testid="banner-preview-control"]').count() == 0

        _create_user("member-preview-denied@test.com")
        ctx = _auth_context(browser, "member-preview-denied@test.com")
        member_page = ctx.new_page()
        member_response = member_page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        assert member_response.status in (302, 403, 404) or (
            "/accounts/login" in member_page.url
        )
        assert member_page.locator('[data-testid="banner-preview-control"]').count() == 0
        ctx.close()
