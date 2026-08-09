"""Compiled-CSS, browser rendering, and review evidence for issue #1383."""

import re
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_staff_user,
    create_user,
    ensure_site_config_tiers,
    goto_with_retry,
)

ROOT = Path(__file__).resolve().parents[1]
CSS_PATH = ROOT / "static/css/tailwind.css"
SCREENSHOT_DIR = ROOT / ".tmp/screenshots/issue-1383/after"


def _escaped_selector(class_name):
    return "." + re.sub(r"([^a-zA-Z0-9_-])", r"\\\1", class_name)


DYNAMIC_CLASSES = {
    # Product button helper: all sizes and variants.
    "min-h-[44px]",
    "px-3",
    "py-1.5",
    "px-4",
    "py-2",
    "px-6",
    "py-3",
    "bg-accent",
    "text-accent-foreground",
    "bg-secondary",
    "text-foreground",
    "text-red-700",
    "dark:text-red-400",
    # Member and content badges.
    "bg-green-500/15",
    "text-green-800",
    "dark:text-green-400",
    "bg-yellow-500/15",
    "text-yellow-800",
    "dark:text-yellow-400",
    "bg-red-500/15",
    "text-red-800",
    "dark:text-red-400",
    "bg-purple-500/20",
    "text-purple-400",
    "bg-orange-500/20",
    # Studio lifecycle/severity/tier producers.
    "bg-sky-500/15",
    "text-sky-300",
    "bg-emerald-500/15",
    "text-emerald-300",
    "bg-amber-500/15",
    "text-amber-300",
    # Sole operator-authored runtime token family.
    "from-accent/30",
    "from-blue-500/30",
    # First-party JavaScript-only state classes.
    "bg-amber-500",
    "bg-emerald-500",
    "translate-x-0.5",
    "translate-x-5",
}


@pytest.mark.core
@pytest.mark.local_only
def test_compiled_bundle_contains_dynamic_product_selectors():
    css = CSS_PATH.read_text()
    missing = sorted(name for name in DYNAMIC_CLASSES if _escaped_selector(name) not in css)
    assert missing == []


@pytest.mark.core
@pytest.mark.local_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("path", ["/", "/blog", "/pricing"])
def test_public_pages_load_local_css_without_tailwind_play_cdn(django_server, page, path):
    if path == "/":
        ensure_site_config_tiers()
    tailwind_cdn_requests = []
    page.on(
        "request",
        lambda request: tailwind_cdn_requests.append(request.url) if "cdn.tailwindcss.com" in request.url else None,
    )

    response = goto_with_retry(
        page,
        f"{django_server}{path}",
        wait_until="domcontentloaded",
    )

    assert response.status == 200
    link = page.locator('link[rel="stylesheet"][href*="/static/css/tailwind.css"]')
    assert link.count() == 1
    stylesheet_response = page.request.get(link.evaluate("el => el.href"))
    assert stylesheet_response.status == 200
    assert tailwind_cdn_requests == []
    assert page.locator("body").evaluate("el => getComputedStyle(el).fontFamily").startswith("Inter")
    assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1

    if path == "/":
        tiers_link = page.get_by_role("link", name="View membership tiers")
        assert tiers_link.evaluate("el => getComputedStyle(el).display") in {"flex", "inline-flex"}
        tiers_link.click()
        assert page.locator("#tiers").is_visible()

        toggle = page.get_by_test_id("theme-toggle").first
        was_dark = page.locator("html").evaluate("el => el.classList.contains('dark')")
        toggle.click()
        assert page.locator("html").evaluate("el => el.classList.contains('dark')") is not was_dark

        billing = page.locator("#billing-toggle")
        if billing.count():
            before = page.locator(".tier-price").first.inner_text()
            billing.click()
            assert page.locator(".tier-price").first.inner_text() != before


@pytest.mark.core
@pytest.mark.local_only
@pytest.mark.django_db(transaction=True)
def test_member_and_studio_surfaces_keep_layout_and_theme(django_server, browser, django_db_blocker):
    with django_db_blocker.unblock():
        ensure_site_config_tiers()
        create_user("tailwind-main-1383@example.com", tier_slug="main")
        create_staff_user("tailwind-staff-1383@example.com")

    for email, path in (
        ("tailwind-main-1383@example.com", "/account/"),
        ("tailwind-staff-1383@example.com", "/studio/"),
    ):
        context = auth_context(browser, email)
        context.add_init_script("localStorage.setItem('theme', 'dark')")
        page = context.new_page()
        response = goto_with_retry(
            page,
            f"{django_server}{path}",
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        assert page.locator("html").evaluate("el => el.classList.contains('dark')")
        assert page.locator('link[href*="/static/css/tailwind.css"]').count() == 1
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1
        context.close()


VISUAL_CASES = [
    pytest.param(surface, path, width, height, theme, id=f"{surface}-{width}x{height}-{theme}")
    for surface, path in (
        ("public", "/"),
        ("member", "/"),
        ("auth", "/accounts/login/"),
        ("studio", "/studio/"),
    )
    for width, height in ((1280, 900), (393, 851))
    for theme in ("light", "dark")
]


@pytest.mark.manual_visual
@pytest.mark.local_only
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(("surface", "path", "width", "height", "theme"), VISUAL_CASES)
def test_capture_tailwind_review_matrix(django_server, browser, django_db_blocker, surface, path, width, height, theme):
    with django_db_blocker.unblock():
        ensure_site_config_tiers()
        create_user("tailwind-visual-main-1383@example.com", tier_slug="main")
        create_staff_user("tailwind-visual-staff-1383@example.com")

    if surface == "member":
        context = auth_context(browser, "tailwind-visual-main-1383@example.com")
    elif surface == "studio":
        context = auth_context(browser, "tailwind-visual-staff-1383@example.com")
    else:
        context = browser.new_context()
    context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": height})
    try:
        response = goto_with_retry(
            page,
            f"{django_server}{path}",
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        page.wait_for_load_state("load")
        page.evaluate("() => document.fonts.ready")
        assert page.locator("html").evaluate("el => el.classList.contains('dark')") is (theme == "dark")
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 1
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        surface_name = {
            "public": "public-home",
            "member": "member-dashboard",
            "auth": "auth-login",
            "studio": "studio",
        }[surface]
        viewport_name = "desktop" if width == 1280 else "mobile"
        page.screenshot(
            path=SCREENSHOT_DIR / f"{surface_name}-{viewport_name}-{theme}.png",
            full_page=True,
        )
    finally:
        context.close()
