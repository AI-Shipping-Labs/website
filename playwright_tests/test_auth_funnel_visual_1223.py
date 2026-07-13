"""Manual screenshot audit for the shared auth funnel (issue #1223)."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.local_only, pytest.mark.manual_visual]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1223")
DESKTOP = {"width": 1280, "height": 900}
PIXEL_7 = {"width": 393, "height": 851}


def _set_theme(context, theme):
    context.add_init_script(
        f"""
        localStorage.setItem('theme', '{theme}');
        document.documentElement.classList.toggle('dark', '{theme}' === 'dark');
        """
    )


def _assert_inside_viewport(page, locator):
    box = locator.bounding_box()
    assert box is not None
    viewport_width = page.viewport_size["width"]
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= viewport_width + 1


@pytest.mark.django_db(transaction=True)
def test_auth_funnel_desktop_and_pixel7_light_dark_screenshots(
    django_server, browser
):
    routes = (
        ("login", "/accounts/login/"),
        ("register", "/accounts/register/"),
        ("password-reset-request", "/accounts/password-reset-request"),
    )
    viewports = (("desktop", DESKTOP), ("pixel7", PIXEL_7))
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    for theme in ("light", "dark"):
        for viewport_name, viewport in viewports:
            for route_name, path in routes:
                context = browser.new_context(viewport=viewport)
                _set_theme(context, theme)
                page = context.new_page()
                try:
                    page.goto(
                        f"{django_server}{path}", wait_until="domcontentloaded"
                    )
                    card = page.locator('[data-testid="auth-card"]')
                    card.wait_for(state="visible")
                    has_dark_theme = page.evaluate(
                        "document.documentElement.classList.contains('dark')"
                    )
                    assert has_dark_theme is (theme == "dark")
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= "
                        "document.documentElement.clientWidth + 1"
                    )
                    assert card.is_visible()
                    _assert_inside_viewport(page, card)
                    assert page.locator("#newsletter").count() == 0
                    if route_name == "password-reset-request":
                        assert card.locator("[data-auth-oauth-divider]").count() == 0
                        assert "By signing" not in card.inner_text()

                    page.screenshot(
                        path=SCREENSHOT_DIR
                        / f"{route_name}-{viewport_name}-{theme}.png",
                        full_page=True,
                    )
                finally:
                    context.close()
