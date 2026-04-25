"""Capture screenshots of Mermaid diagrams in dark and light modes
plus the post-toggle state. Used by the SWE/tester report for #306.

Usage:
    uv run python -m pytest playwright_tests/test_mermaid_theme_screenshots.py -v
"""

import os

import pytest

os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from playwright_tests.conftest import VIEWPORT  # noqa: E402
from playwright_tests.test_mermaid import (  # noqa: E402
    WORKSHOP_MERMAID_BODY,
)
from playwright_tests.test_mermaid_theme import (  # noqa: E402
    _add_localstorage_theme,
    _wait_for_mermaid_ready,
)
from playwright_tests.test_workshops import (  # noqa: E402
    _clear_workshops,
    _create_workshop,
)

SHOT_DIR = '/tmp/mermaid-306-screenshots'


@pytest.mark.django_db(transaction=True)
class TestThemeScreenshots:
    """Captures three states: dark initial, light initial, post-toggle."""

    def _setup(self):
        _clear_workshops()
        _create_workshop(
            slug='shot-walkthrough',
            title='Shot Walkthrough',
            landing=0,
            pages=0,
            recording=0,
            pages_data=[
                ('arch', 'Arch', WORKSHOP_MERMAID_BODY),
            ],
        )

    def test_screenshot_dark(self, browser, django_server):
        os.makedirs(SHOT_DIR, exist_ok=True)
        self._setup()

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'dark')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}/workshops/shot-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)
            page.locator('div.mermaid').first.screenshot(
                path=f'{SHOT_DIR}/01-dark-mode-diagram.png'
            )
        finally:
            ctx.close()

    def test_screenshot_light(self, browser, django_server):
        os.makedirs(SHOT_DIR, exist_ok=True)
        self._setup()

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}/workshops/shot-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)
            page.locator('div.mermaid').first.screenshot(
                path=f'{SHOT_DIR}/02-light-mode-diagram.png'
            )
        finally:
            ctx.close()

    def test_screenshot_post_toggle(self, browser, django_server):
        os.makedirs(SHOT_DIR, exist_ok=True)
        self._setup()

        ctx = browser.new_context(viewport=VIEWPORT)
        _add_localstorage_theme(ctx, 'light')
        page = ctx.new_page()
        try:
            page.goto(
                f'{django_server}/workshops/shot-walkthrough/tutorial/arch',
                wait_until='domcontentloaded',
            )
            _wait_for_mermaid_ready(page)

            # Toggle to dark.
            page.locator(
                '[data-testid="theme-toggle"]'
            ).first.click()
            page.wait_for_function(
                "() => document.documentElement.classList.contains('dark')",
                timeout=3000,
            )
            # Wait for re-render to complete.
            page.wait_for_function(
                """() => {
                    const r = document.querySelector(
                        'div.mermaid svg .node rect'
                    );
                    if (!r) return false;
                    const f = getComputedStyle(r).fill
                        || r.getAttribute('fill') || '';
                    if (!f) return false;
                    // Look for the dark card color.
                    const rgb = f.match(/\\d+/g);
                    if (!rgb || rgb.length < 3) return false;
                    const r1 = parseInt(rgb[0]);
                    const g1 = parseInt(rgb[1]);
                    const b1 = parseInt(rgb[2]);
                    return r1 < 50 && g1 < 50 && b1 < 50;
                }""",
                timeout=10000,
            )
            page.locator('div.mermaid').first.screenshot(
                path=f'{SHOT_DIR}/03-post-toggle-diagram.png'
            )
        finally:
            ctx.close()
