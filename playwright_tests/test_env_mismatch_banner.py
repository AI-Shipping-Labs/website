"""Playwright coverage for the compact Studio env-mismatch banner (#408)."""

import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("/tmp/aisl-issue-408-screenshots")
MOBILE_VIEWPORT = {"width": 390, "height": 900}


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


def _login_staff(browser, email="env-mismatch-admin@test.com"):
    _ensure_tiers()
    _create_staff_user(email)
    return _auth_context(browser, email)


@pytest.mark.django_db(transaction=True)
def test_studio_mismatch_banner_is_compact_by_default(django_server, browser, settings):
    settings.SITE_BASE_URL = "https://aishippinglabs.com"
    context = _login_staff(browser)
    page = context.new_page()

    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    banner = page.locator('[data-testid="env-mismatch-banner"]')
    assert banner.is_visible()
    assert "Environment mismatch" in banner.inner_text()
    assert "https://aishippinglabs.com" in banner.inner_text()
    assert django_server in banner.inner_text()

    details = page.locator('[data-testid="env-mismatch-details"]')
    assert not details.is_visible()
    assert not page.get_by_text("Generated links (unsubscribe").is_visible()
    assert page.locator('[data-testid="env-mismatch-toggle"]').get_attribute(
        "aria-expanded"
    ) == "false"

    _capture(page, "desktop-collapsed")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_studio_mismatch_banner_expands_and_collapses(django_server, browser, settings):
    settings.SITE_BASE_URL = "https://aishippinglabs.com"
    context = _login_staff(browser, "env-mismatch-toggle@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")
    toggle = page.locator('[data-testid="env-mismatch-toggle"]')
    details = page.locator('[data-testid="env-mismatch-details"]')

    toggle.press("Enter")
    assert toggle.get_attribute("aria-expanded") == "true"
    assert details.is_visible()
    assert "password resets" in details.inner_text()
    assert "calendar invites" in details.inner_text()
    _capture(page, "desktop-expanded")

    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert not details.is_visible()
    assert page.locator('[data-testid="env-mismatch-banner"]').is_visible()

    context.close()


@pytest.mark.django_db(transaction=True)
def test_studio_mismatch_banner_stays_compact_on_mobile(django_server, browser, settings):
    settings.SITE_BASE_URL = "https://aishippinglabs.com"
    context = _login_staff(browser, "env-mismatch-mobile@test.com")
    page = context.new_page()
    page.set_viewport_size(MOBILE_VIEWPORT)

    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    banner = page.locator('[data-testid="env-mismatch-banner"]')
    banner_box = banner.bounding_box()
    assert banner_box is not None
    assert banner_box["height"] <= 120

    title_box = page.get_by_role("heading", name="Courses").bounding_box()
    search_box = page.get_by_placeholder("Search courses...").bounding_box()
    assert title_box is not None
    assert search_box is not None
    assert title_box["y"] < MOBILE_VIEWPORT["height"]
    assert search_box["y"] < MOBILE_VIEWPORT["height"]

    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2

    _capture(page, "mobile-collapsed")
    context.close()
