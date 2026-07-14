"""Eight-state visual-review fixtures for the #1241 homepage IA."""

import os
from pathlib import Path

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_site_config_tiers,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.manual_visual,
]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1241")
VISUAL_CASES = [
    pytest.param(auth, width, height, theme, id=f"{auth}-{width}x{height}-{theme}")
    for auth in ("anonymous", "main")
    for width, height in ((1440, 1000), (393, 852))
    for theme in ("light", "dark")
]


@pytest.mark.parametrize(("auth", "width", "height", "theme"), VISUAL_CASES)
def test_capture_homepage_ia_review_matrix(
    django_server,
    browser,
    django_db_blocker,
    auth,
    width,
    height,
    theme,
):
    """Capture the binding desktop/mobile, light/dark, guest/member matrix."""
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()

    if auth == "main":
        email = f"visual-1241-{width}-{theme}@example.com"
        with django_db_blocker.unblock():
            create_user(email, tier_slug="main")
        context = auth_context(browser, email)
        context.set_default_timeout(10_000)
    else:
        context = browser.new_context()

    context.add_init_script(f"localStorage.setItem('theme', '{theme}')")
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": height})
    try:
        response = page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert response.status == 200
        page.wait_for_load_state("load")
        assert page.locator("html").evaluate("el => el.classList.contains('dark')") is (
            theme == "dark"
        )
        assert page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth"
        ) <= 1

        if auth == "anonymous":
            tiers = page.locator("#tiers")
            join = page.locator("#join-free")
            expect(tiers.locator('[data-testid="home-tier-card"]')).to_have_count(4)
            expect(tiers.locator("form, input, [data-auth-oauth-providers]")).to_have_count(0)
            expect(join.locator("#register-form")).to_have_count(1)
            assert join.evaluate("el => !el.closest('[data-tier-carousel]')")
            if width == 393:
                carousel = page.get_by_test_id("home-tier-carousel")
                centered_delta = page.wait_for_function(
                    """carousel => {
                      const card = carousel.querySelector('[data-tier-card="main"]');
                      const outer = carousel.getBoundingClientRect();
                      const inner = card.getBoundingClientRect();
                      const delta = Math.abs(
                        (inner.left + inner.width / 2) -
                        (outer.left + outer.width / 2)
                      );
                      return delta < 60 ? delta : false;
                    }""",
                    arg=carousel.element_handle(),
                )
                assert centered_delta.json_value() < 60
                free = tiers.locator('[data-tier-card="free"]')
                free.evaluate(
                    "el => el.scrollIntoView({block: 'nearest', inline: 'center'})"
                )
                expect(free.get_by_role("link", name="Join free", exact=True)).to_be_visible()
                if theme == "dark":
                    page.locator("#register-email").fill("visual-error@example.com")
                    page.locator("#register-password").fill("Password123!")
                    page.locator("#register-password-confirm").fill("Different123!")
                    page.locator("#register-submit").click()
                    expect(page.locator("#register-error")).to_have_text(
                        "Passwords do not match"
                    )
        else:
            expect(page.get_by_role("heading", name="Recent content", exact=True)).to_be_visible()
            expect(page.locator("#join-free, #register-form")).to_have_count(0)

        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=SCREENSHOT_DIR / f"home-{auth}-{width}x{height}-{theme}.png",
            full_page=True,
        )
    finally:
        context.close()
