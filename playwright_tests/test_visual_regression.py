"""
Visual regression tests comparing the Django site against aishippinglabs.com.

These tests:
1. Capture baseline screenshots from the live site (https://aishippinglabs.com)
2. Start the Django dev server and capture screenshots of each page
3. Compare the two sets of screenshots pixel-by-pixel
4. Test passes if pages are visually identical (within a tolerance threshold)

Usage:
    uv run pytest playwright_tests/ -v

To regenerate baselines only:
    uv run pytest playwright_tests/ -v -k "capture_baselines"
"""

import os

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    LIVE_BASE_URL,
    PAGES,
    SCREENSHOT_DIR,
)


# Maximum allowed pixel difference ratio (0.0 = identical, 1.0 = completely different)
# We allow a small tolerance for font rendering differences across platforms
MAX_DIFF_RATIO = 0.10  # 10% pixel difference allowed

VIEWPORT = {"width": 1280, "height": 720}


def _screenshot_page(page, url, save_path, wait_ms=2000):
    """Navigate to a URL and take a full-page screenshot."""
    page.goto(url, wait_until="networkidle", timeout=30000)
    # Wait for any animations / lazy-loaded content
    page.wait_for_timeout(wait_ms)
    page.screenshot(path=save_path, full_page=True)


def _ensure_dirs():
    """Ensure screenshot directories exist."""
    for subdir in ("baseline", "django", "diff"):
        os.makedirs(os.path.join(SCREENSHOT_DIR, subdir), exist_ok=True)


def _compute_diff_ratio(baseline_path, django_path):
    """
    Compute pixel difference ratio between two screenshots.

    Returns a float between 0.0 (identical) and 1.0 (completely different).
    Uses Pillow for pixel-level RGB comparison with a per-channel threshold
    to account for minor rendering differences.
    """
    img1 = Image.open(baseline_path).convert("RGB")
    img2 = Image.open(django_path).convert("RGB")

    # Resize to same dimensions if needed
    if img1.size != img2.size:
        img2 = img2.resize(img1.size)

    pixels1 = list(img1.getdata())
    pixels2 = list(img2.getdata())

    total = len(pixels1)
    if total == 0:
        return 0.0

    diff_count = 0
    for p1, p2 in zip(pixels1, pixels2):
        # Consider pixels different if any channel differs by more than threshold
        if any(abs(a - b) > 30 for a, b in zip(p1, p2)):
            diff_count += 1

    return diff_count / total


class TestCaptureBaselines:
    """Capture baseline screenshots from the live site."""

    @pytest.mark.parametrize("name,path", PAGES, ids=[p[0] for p in PAGES])
    def test_capture_baseline(self, name, path):
        """Capture a baseline screenshot from the live site."""
        _ensure_dirs()
        save_path = os.path.join(SCREENSHOT_DIR, "baseline", f"{name}.png")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIEWPORT,
                color_scheme="dark",
            )
            page = context.new_page()

            try:
                _screenshot_page(page, f"{LIVE_BASE_URL}{path}", save_path)
                assert os.path.exists(save_path), f"Baseline screenshot was not saved: {save_path}"
                file_size = os.path.getsize(save_path)
                assert file_size > 0, f"Baseline screenshot is empty: {save_path}"
            finally:
                browser.close()


@pytest.mark.django_db
class TestVisualRegression:
    """Compare Django site screenshots against live baselines."""

    @pytest.mark.parametrize("name,path", PAGES, ids=[p[0] for p in PAGES])
    def test_page_visual_match(self, name, path, django_server, screenshot_dirs):
        """
        Screenshot the Django page and compare against the baseline.

        The test captures a baseline from the live site if one doesn't exist,
        then screenshots the local Django server and compares the two.
        """
        baseline_path = os.path.join(screenshot_dirs["baseline"], f"{name}.png")
        django_path = os.path.join(screenshot_dirs["django"], f"{name}.png")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIEWPORT,
                color_scheme="dark",
            )
            page = context.new_page()

            try:
                # Capture baseline from live site if it doesn't exist
                if not os.path.exists(baseline_path):
                    _screenshot_page(
                        page, f"{LIVE_BASE_URL}{path}", baseline_path
                    )

                # Capture Django site screenshot
                _screenshot_page(
                    page, f"{django_server}{path}", django_path
                )

                assert os.path.exists(baseline_path), (
                    f"Baseline screenshot missing: {baseline_path}"
                )
                assert os.path.exists(django_path), (
                    f"Django screenshot missing: {django_path}"
                )

                # Compare the two screenshots
                diff_ratio = _compute_diff_ratio(baseline_path, django_path)

                assert diff_ratio <= MAX_DIFF_RATIO, (
                    f"Visual regression on '{name}' page (path: {path}): "
                    f"diff ratio {diff_ratio:.2%} exceeds threshold {MAX_DIFF_RATIO:.2%}. "
                    f"Baseline: {baseline_path}, Django: {django_path}"
                )
            finally:
                browser.close()
