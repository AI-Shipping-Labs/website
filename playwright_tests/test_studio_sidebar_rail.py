"""E2E: the desktop Studio sidebar collapses to an icon rail.

Collapse -> the rail shows icons only, the main content column is
centred; reload -> still collapsed; expand -> labels come back.
"""

import os

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from playwright_tests.conftest import ensure_tiers as _ensure_tiers
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.django_db(transaction=True)]

TOGGLE = '[data-testid="studio-sidebar-collapse-toggle"]'
COURSES_LINK = '#studio-sidebar-nav a[href="/studio/courses/"]'


def _label_width(label):
    # Collapsed labels stay in the accessibility tree as 1px sr-only text,
    # so Playwright still calls them "visible"; measure the rendered width.
    return label.bounding_box()["width"]


@browser_journey
def test_collapse_persists_and_expand_restores_labels(django_server, browser):
    _ensure_tiers()
    _create_staff_user("admin@test.com")
    context = _auth_context(browser, "admin@test.com")
    page = context.new_page()
    page.set_viewport_size({"width": 1600, "height": 900})
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    sidebar = page.locator("#studio-sidebar")
    courses_label = page.locator(f"{COURSES_LINK} > span")
    assert sidebar.bounding_box()["width"] == pytest.approx(256, abs=1)
    assert _label_width(courses_label) > 30

    page.click(TOGGLE)

    # Rail: slim, icons only, active item still highlighted and named.
    assert sidebar.bounding_box()["width"] == pytest.approx(64, abs=1)
    assert _label_width(courses_label) <= 1
    courses_link = page.locator(COURSES_LINK)
    assert courses_link.locator("svg").first.is_visible()
    assert courses_link.get_attribute("aria-current") == "page"
    assert courses_link.get_attribute("title") == "Courses"
    assert courses_link.get_attribute("aria-label") == "Courses"
    assert page.get_by_test_id("studio-rail-search").is_visible()
    assert not page.get_by_test_id("studio-global-search-input").is_visible()
    assert page.locator(TOGGLE).get_attribute("aria-label") == "Expand Studio sidebar"

    # Main content keeps its expanded width and is centred in the space
    # right of the rail.
    main = page.locator("main").bounding_box()
    inner = page.locator("#studio-main-inner").bounding_box()
    left_gap = inner["x"] - main["x"]
    right_gap = (main["x"] + main["width"]) - (inner["x"] + inner["width"])
    assert left_gap > 50
    assert left_gap == pytest.approx(right_gap, abs=2)

    # Rail search opens the quick-jump dialog.
    page.get_by_test_id("studio-rail-search").click()
    assert page.get_by_test_id("studio-quick-jump-input").is_visible()
    page.keyboard.press("Escape")

    page.reload(wait_until="domcontentloaded")
    assert sidebar.bounding_box()["width"] == pytest.approx(64, abs=1)
    assert _label_width(courses_label) <= 1

    page.click(TOGGLE)
    assert sidebar.bounding_box()["width"] == pytest.approx(256, abs=1)
    assert _label_width(courses_label) > 30
    assert page.locator(COURSES_LINK).get_attribute("title") is None
    assert page.get_by_test_id("studio-global-search-input").is_visible()

    page.reload(wait_until="domcontentloaded")
    assert _label_width(courses_label) > 30
    context.close()

@browser_journey
def test_mobile_drawer_ignores_collapsed_preference(django_server, browser):
    _ensure_tiers()
    _create_staff_user("admin@test.com")
    context = _auth_context(browser, "admin@test.com")
    context.add_init_script(
        "try { localStorage.setItem('studio-sidebar-collapsed', '1'); } catch (e) {}"
    )
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    page.click("#studio-sidebar-toggle")
    assert page.locator(f"{COURSES_LINK} > span").is_visible()
    assert not page.locator(TOGGLE).is_visible()
    context.close()
