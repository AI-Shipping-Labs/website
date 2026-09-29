"""Browser coverage for the phone reader drawer and sticky bottom bar.

Mobile polish, Group B. The drawer is a modal overlay on every reader,
including workshop tutorials. The Previous / primary bar sticks to the
bottom of the viewport while the reader is mid-lesson. Both behaviours
depend on layout, scrolling, and JavaScript, so they are tested in a browser.
"""

import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.test_course_units import (
    _clear_courses,
    _create_course,
    _create_module,
    _create_unit,
)
from playwright_tests.test_workshops import _clear_workshops, _create_workshop
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

PHONE = {"width": 390, "height": 844}
LONG_BODY = "\n\n".join(
    f"Paragraph {n}. " + "Reading the lesson body on a phone. " * 12
    for n in range(40)
)


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_workshop_drawer_opens_as_overlay_and_closes_on_backdrop_tap(
    django_server, browser,
):
    _clear_workshops()
    _create_workshop(
        slug="drawer-overlay-ws",
        title="Drawer overlay workshop",
        landing=0,
        pages=0,
        recording=0,
        pages_data=[
            ("intro", "Introduction", "# Welcome\n\n" + LONG_BODY),
            ("setup", "Set up the server", "# Setup\n\n" + LONG_BODY),
            ("finish", "Finish", "# Done\n\nBody."),
        ],
    )
    context = browser.new_context(viewport=PHONE, has_touch=True)
    page = context.new_page()
    page.goto(
        f"{django_server}/workshops/drawer-overlay-ws/setup",
        wait_until="domcontentloaded",
    )
    page.evaluate("window.scrollTo(0, 0)")
    drawer = page.locator("#sidebar-nav")
    expect(drawer).to_be_hidden()

    page.get_by_test_id("reader-mobile-drawer-toggle").click()

    expect(drawer).to_be_visible()
    expect(drawer).to_have_attribute("role", "dialog")
    expect(drawer).to_have_attribute("aria-modal", "true")
    box = drawer.bounding_box()
    # An overlay under the site header, not a block far down the page.
    assert box["y"] < 150
    assert box["y"] + box["height"] <= PHONE["height"]
    expect(drawer.get_by_role("link", name="Set up the server")).to_be_in_viewport()
    expect(page.get_by_role("button", name="Close workshop pages")).to_be_focused()
    # Scroll lock: wheeling over the backdrop does not move the page.
    page.mouse.move(10, 830)
    page.mouse.wheel(0, 800)
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") == 0

    page.get_by_test_id("reader-drawer-backdrop").tap(position={"x": 10, "y": 830})

    expect(drawer).to_be_hidden()
    expect(page.get_by_test_id("reader-mobile-drawer-toggle")).to_be_focused()
    page.mouse.wheel(0, 800)
    page.wait_for_function("window.scrollY > 0")
    context.close()


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_sticky_bottom_nav_visible_mid_lesson(django_server, browser):
    _clear_courses()
    course = _create_course("Sticky bar course", "sticky-bar-course")
    module = _create_module(course, "Module one", sort_order=1)
    _create_unit(module, "First lesson", sort_order=1, body="Short.")
    lesson = _create_unit(module, "Long lesson", sort_order=2, body=LONG_BODY)
    _create_unit(module, "Third lesson", sort_order=3, body="Short.")
    _create_user("sticky-bar@test.com", tier_slug="free")
    context = _auth_context(browser, "sticky-bar@test.com")
    # A saved analytics choice keeps the first-visit consent panel closed,
    # so the bar sits on the viewport edge (the open-panel case is covered
    # by the consent-panel journey below).
    context.add_cookies([{
        "name": "aslab_analytics_consent", "value": "denied",
        "domain": "127.0.0.1", "path": "/",
    }])
    page = context.new_page()
    page.set_viewport_size(PHONE)
    page.goto(f"{django_server}{lesson.get_absolute_url()}", wait_until="domcontentloaded")

    page.evaluate(
        "document.documentElement.style.scrollBehavior = 'auto';"
        "window.scrollTo(0, document.documentElement.scrollHeight / 2)"
    )

    bar = page.get_by_test_id("reader-bottom-nav-bar")
    previous = bar.get_by_role("link", name="Previous")
    primary = bar.get_by_role("button", name="Complete & Next")
    expect(previous).to_be_in_viewport()
    expect(primary).to_be_in_viewport()
    bar_box = bar.bounding_box()
    assert abs(bar_box["y"] + bar_box["height"] - PHONE["height"]) <= 1
    # The primary action sits on the thumb side, right of Previous.
    assert primary.bounding_box()["x"] > previous.bounding_box()["x"]

    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
    # At the end of the lesson the bar settles above Q&A instead of covering it.
    body_bottom = page.get_by_test_id("course-unit-body").bounding_box()
    bar_box = bar.bounding_box()
    assert bar_box["y"] >= body_bottom["y"] + body_bottom["height"]
    context.close()


def _scroll_mid_lesson(page):
    page.evaluate(
        "document.documentElement.style.scrollBehavior = 'auto';"
        "window.scrollTo(0, document.documentElement.scrollHeight / 2)"
    )


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_first_visit_consent_panel_keeps_sticky_bar_and_studio_icon_reachable(
    django_server, browser,
):
    _clear_courses()
    course = _create_course("Consent stack course", "consent-stack-course")
    module = _create_module(course, "Module one", sort_order=1)
    _create_unit(module, "First lesson", sort_order=1, body="Short.")
    lesson = _create_unit(module, "Long lesson", sort_order=2, body=LONG_BODY)
    _create_unit(module, "Third lesson", sort_order=3, body="Short.")
    _create_user("consent-stack@test.com", tier_slug="free", is_staff=True)
    context = _auth_context(browser, "consent-stack@test.com")
    page = context.new_page()
    page.set_viewport_size(PHONE)
    page.goto(f"{django_server}{lesson.get_absolute_url()}", wait_until="domcontentloaded")
    _scroll_mid_lesson(page)

    panel = page.get_by_test_id("analytics-consent-panel")
    bar = page.get_by_test_id("reader-bottom-nav-bar")
    studio = page.get_by_test_id("studio-edit-button")
    expect(panel).to_be_visible()
    expect(bar.get_by_role("link", name="Previous")).to_be_in_viewport()
    # First visit: the bar sticks above the consent panel, not under it,
    # and the Studio icon floats above the bar.
    bar_box = bar.bounding_box()
    assert bar_box["y"] + bar_box["height"] <= panel.bounding_box()["y"]
    studio_box = studio.bounding_box()
    assert studio_box["y"] + studio_box["height"] <= bar_box["y"]

    page.get_by_test_id("analytics-consent-deny").click()
    expect(panel).to_be_hidden()
    page.wait_for_load_state("domcontentloaded")
    _scroll_mid_lesson(page)

    # With a saved choice the bar returns to the viewport edge and the
    # Studio icon still clears it.
    expect(bar.get_by_role("link", name="Previous")).to_be_in_viewport()
    bar_box = bar.bounding_box()
    assert abs(bar_box["y"] + bar_box["height"] - PHONE["height"]) <= 1
    studio_box = studio.bounding_box()
    assert studio_box["y"] + studio_box["height"] <= bar_box["y"]
    context.close()
