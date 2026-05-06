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

# Tests added by issue #462 use these helpers.
from django.db import connection  # noqa: E402

from integrations.config import clear_config_cache  # noqa: E402


def _reset_site_settings():
    """Delete SITE_BASE_URL* rows so each test starts clean."""
    from integrations.models import IntegrationSetting

    IntegrationSetting.objects.filter(
        key__in=['SITE_BASE_URL', 'SITE_BASE_URL_ALIASES'],
    ).delete()
    clear_config_cache()
    connection.close()

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


# ---------------------------------------------------------------------------
# Issue #462: cross-process DB override of SITE_BASE_URL clears the banner
# without a process restart. Exercises the operator-reported flow end to end:
# env value disagrees with the request host, the operator saves the matching
# value via Studio, and on the next page load the banner is gone.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_db_override_clears_banner_on_next_page_load(django_server, browser, settings):
    settings.SITE_BASE_URL = "https://prod.aishippinglabs.com"
    _reset_site_settings()
    context = _login_staff(browser, "env-mismatch-override@test.com")
    page = context.new_page()

    # Step 1: env says prod, the test server runs on 127.0.0.1 — banner fires.
    page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
    banner = page.locator('[data-testid="env-mismatch-banner"]')
    assert banner.is_visible()
    assert "https://prod.aishippinglabs.com" in banner.inner_text()

    # Step 2: save SITE_BASE_URL to the test server URL via the Site card form.
    site_card = page.locator("#integration-site")
    site_card.locator('input[name="SITE_BASE_URL"]').fill(django_server)
    site_card.locator('button[type="submit"]').click()
    page.wait_for_load_state("domcontentloaded")

    # Step 3: navigate to a different Studio page and confirm the banner is gone.
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")
    assert not page.locator('[data-testid="env-mismatch-banner"]').is_visible(), (
        "Banner must disappear after the DB override matches the request host. "
        "If it is still visible, the worker that served this request is reading "
        "the env-time settings.SITE_BASE_URL because the cross-process cache "
        "stamp was not updated by the previous save."
    )

    _reset_site_settings()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_alias_set_via_db_suppresses_banner(django_server, browser, settings):
    # Env disagrees with request host, but operator sets SITE_BASE_URL_ALIASES
    # to include the request host — banner must not fire.
    settings.SITE_BASE_URL = "https://prod.aishippinglabs.com"
    _reset_site_settings()
    context = _login_staff(browser, "env-mismatch-alias@test.com")
    page = context.new_page()

    page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")
    site_card = page.locator("#integration-site")
    site_card.locator('textarea[name="SITE_BASE_URL_ALIASES"]').fill(django_server)
    site_card.locator('button[type="submit"]').click()
    page.wait_for_load_state("domcontentloaded")

    page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")
    assert not page.locator('[data-testid="env-mismatch-banner"]').is_visible(), (
        "Banner must not fire when the request host is listed in "
        "SITE_BASE_URL_ALIASES."
    )

    _reset_site_settings()
    context.close()
