"""Browser behavior and screenshot evidence for issue #1281."""

import base64
import datetime
import os
from pathlib import Path

import pytest
from django.db import connection
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from playwright_tests.test_pricing_layout_1188 import _seed_pricing

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.visual_regression,
]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1281")
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _dismiss_consent(page):
    button = page.get_by_role("button", name="Keep analytics off")
    if button.is_visible():
        with page.expect_navigation(wait_until="domcontentloaded"):
            button.click()
    expect(page.get_by_test_id("analytics-consent-panel")).to_be_hidden()


def _set_theme(page, theme):
    page.evaluate(
        """theme => {
            localStorage.setItem('theme', theme);
            document.documentElement.classList.toggle('dark', theme === 'dark');
        }""",
        theme,
    )


def _shot(page, name):
    page.evaluate("async () => { if (document.fonts) await document.fonts.ready; }")
    page.wait_for_function(
        "() => document.getAnimations().every(animation => animation.playState === 'finished')"
    )
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _no_body_overflow(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth + 1"
    )


def _seed_projects():
    from content.models import Project

    common = {
        "description": "Resilient cover fallback fixture.",
        "content_markdown": "The project body stays visible.",
        "date": datetime.date(2026, 7, 17),
        "published": True,
        "required_level": 0,
    }
    broken = Project.objects.create(
        title="Broken cover fallback project",
        slug="broken-cover-fallback-project-1281",
        cover_image_url="https://images.example.test/broken-cover-1281.png",
        **common,
    )
    success = Project.objects.create(
        title="Successful cover control project",
        slug="successful-cover-control-project-1281",
        cover_image_url="https://images.example.test/success-cover-1281.png",
        **common,
    )
    coverless = Project.objects.create(
        title="Coverless layout control project",
        slug="coverless-layout-control-project-1281",
        **common,
    )
    connection.close()
    return broken, success, coverless


@pytest.mark.parametrize("width", [1280, 1440])
def test_paid_pricing_actions_share_desktop_baseline(
    django_server, page, django_db_blocker, width,
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    _dismiss_consent(page)

    carousel = page.get_by_test_id("pricing-tier-carousel")
    assert carousel.evaluate("node => getComputedStyle(node).display") == "grid"
    boxes = []
    for slug in ("basic", "main", "premium"):
        action = page.locator(f'[data-tier-card="{slug}"]').locator(
            ".tier-cta-link, .tier-cta-form button, [data-action]"
        ).first
        expect(action).to_be_visible()
        boxes.append(action.bounding_box())
    assert max(box["y"] for box in boxes) - min(box["y"] for box in boxes) <= 1
    bottoms = [box["y"] + box["height"] for box in boxes]
    assert max(bottoms) - min(bottoms) <= 1
    expect(
        page.locator('[data-tier-card="free"] [data-testid="inline-register-card"]')
    ).to_be_visible()

    if width == 1440:
        for theme in ("light", "dark"):
            _set_theme(page, theme)
            _shot(page, f"pricing-{theme}-1440x900")


@pytest.mark.parametrize("width,height", [(393, 852), (1023, 900)])
def test_pricing_sub_desktop_carousel_contract(
    django_server, page, django_db_blocker, width, height,
):
    with django_db_blocker.unblock():
        _seed_pricing(oauth=True)

    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{django_server}/pricing", wait_until="networkidle")
    _dismiss_consent(page)
    carousel = page.get_by_test_id("pricing-tier-carousel")
    assert carousel.evaluate("node => getComputedStyle(node).display") == "flex"
    assert carousel.evaluate("node => node.scrollWidth > node.clientWidth")
    assert carousel.evaluate("node => getComputedStyle(node).scrollSnapType").startswith("x")
    main = page.locator('[data-tier-card="main"]')
    assert main.evaluate("node => getComputedStyle(node).scrollSnapAlign") == "center"
    badge = main.get_by_text("Most popular")
    expect(badge).to_be_visible()
    badge_box = badge.bounding_box()
    carousel_box = carousel.bounding_box()
    assert badge_box["y"] >= carousel_box["y"] - 1
    assert _no_body_overflow(page)
    if width == 393:
        _shot(page, "pricing-light-393x852")


def test_shared_preview_reveals_existing_fallback_without_layout_shift(
    django_server, page, django_db_blocker,
):
    with django_db_blocker.unblock():
        broken, success, _ = _seed_projects()

    page.route(broken.cover_image_url, lambda route: route.fulfill(status=404, body="missing"))
    page.route(
        success.cover_image_url,
        lambda route: route.fulfill(status=200, content_type="image/png", body=TINY_PNG),
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}/projects", wait_until="networkidle")
    _dismiss_consent(page)

    broken_card = page.get_by_test_id("project-card").filter(has_text=broken.title)
    preview = broken_card.get_by_test_id("project-card-preview")
    image = broken_card.get_by_test_id("project-card-preview-image")
    fallback = broken_card.get_by_test_id("project-card-preview-fallback")
    expect(image).to_be_hidden()
    expect(fallback).to_be_visible()
    preview_box = preview.bounding_box()
    assert abs(preview_box["height"] - preview_box["width"] * 9 / 16) <= 1
    assert image.get_attribute("alt") == f"Cover image for {broken.title}"
    assert _no_body_overflow(page)

    success_card = page.get_by_test_id("project-card").filter(has_text=success.title)
    expect(success_card.get_by_test_id("project-card-preview-image")).to_be_visible()
    expect(success_card.get_by_test_id("project-card-preview-fallback")).to_be_hidden()

    for theme in ("light", "dark"):
        _set_theme(page, theme)
        _shot(page, f"broken-catalog-preview-{theme}-desktop")

    broken_card.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.endswith(broken.get_absolute_url())

    page.set_viewport_size({"width": 393, "height": 852})
    page.goto(f"{django_server}/projects", wait_until="networkidle")
    _set_theme(page, "light")
    expect(
        page.get_by_test_id("project-card").filter(has_text=broken.title).get_by_test_id(
            "project-card-preview-fallback"
        )
    ).to_be_visible()
    assert _no_body_overflow(page)
    _shot(page, "broken-catalog-preview-light-393x852")


def test_project_detail_failed_cover_collapses_to_coverless_layout(
    django_server, page, django_db_blocker,
):
    with django_db_blocker.unblock():
        broken, success, coverless = _seed_projects()

    page.route(broken.cover_image_url, lambda route: route.fulfill(status=404, body="missing"))
    page.route(
        success.cover_image_url,
        lambda route: route.fulfill(status=200, content_type="image/png", body=TINY_PNG),
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{django_server}{broken.get_absolute_url()}", wait_until="networkidle")
    _dismiss_consent(page)
    expect(page.get_by_test_id("project-detail-cover")).to_be_hidden()
    expect(page.get_by_role("heading", name=broken.title)).to_be_visible()
    expect(page.get_by_test_id("project-body")).to_be_visible()
    broken_heading_y = page.get_by_role("heading", name=broken.title).bounding_box()["y"]
    assert _no_body_overflow(page)
    for theme in ("light", "dark"):
        _set_theme(page, theme)
        _shot(page, f"project-detail-broken-{theme}-desktop")

    page.goto(f"{django_server}{coverless.get_absolute_url()}", wait_until="networkidle")
    coverless_heading_y = page.get_by_role("heading", name=coverless.title).bounding_box()["y"]
    assert abs(broken_heading_y - coverless_heading_y) <= 1

    page.goto(f"{django_server}{success.get_absolute_url()}", wait_until="networkidle")
    expect(page.get_by_test_id("project-detail-cover-image")).to_be_visible()
    expect(page.get_by_test_id("project-detail-cover")).to_be_visible()
    _set_theme(page, "light")
    _shot(page, "project-detail-success-light-desktop")


def test_stale_dashboard_and_account_controls_remain_usable(
    django_server, browser, django_db_blocker, settings,
):
    settings.SLACK_INVITE_URL = "https://join.slack.com/issue-1281"
    with django_db_blocker.unblock():
        create_user("stale-controls-1281@test.com", tier_slug="main")

    context = auth_context(browser, "stale-controls-1281@test.com")
    page = context.new_page()
    try:
        page.set_viewport_size({"width": 320, "height": 720})
        page.goto(f"{django_server}/", wait_until="networkidle")
        _dismiss_consent(page)
        onboarding = page.get_by_test_id("onboarding-prompt")
        onboarding_dismiss = page.get_by_test_id("onboarding-prompt-dismiss")
        slack = page.get_by_test_id("slack-account-card")
        slack_dismiss = page.get_by_test_id("slack-account-card-dismiss")
        for button in (onboarding_dismiss, slack_dismiss):
            box = button.bounding_box()
            assert box["width"] >= 44 and box["height"] >= 44
            assert button.get_attribute("aria-label") == "Dismiss"
        expect(onboarding).to_be_visible()
        expect(slack).to_be_visible()
        slack_dismiss.focus()
        assert ":focus-visible" in slack_dismiss.evaluate(
            "node => node.matches(':focus-visible') ? ':focus-visible' : ''"
        )
        assert _no_body_overflow(page)
        _shot(page, "dashboard-dismiss-focus-320x720")
        page.keyboard.press("Enter")
        expect(slack).to_be_hidden()
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_test_id("slack-account-card")).to_have_count(0)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/account/#api-keys", wait_until="networkidle")
        submit = page.get_by_test_id("member-api-key-create-submit")
        expect(submit).to_be_visible()
        assert "whitespace-nowrap" in submit.get_attribute("class")
        assert submit.evaluate("node => node.scrollWidth <= node.clientWidth")
        assert _no_body_overflow(page)
        _shot(page, "account-new-key-1280x900")
        page.get_by_test_id("member-api-key-name-input").fill("issue 1281 key")
        submit.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_test_id("member-api-key-plaintext")).to_be_visible()
    finally:
        context.close()
