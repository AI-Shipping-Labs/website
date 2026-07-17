"""Live operator journeys for the Studio stacked-header migration (#1274)."""

import os
from datetime import timedelta
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = [pytest.mark.local_only, pytest.mark.core]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1274")
FOCUS_RING = (
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent "
    "focus-visible:ring-offset-2 focus-visible:ring-offset-background"
)

ROUTE_MATRIX = [
    "/studio/users/",
    "/studio/events/",
    "/studio/events/past/",
    "/studio/sprints/",
    "/studio/plans/",
    "/studio/workshops/",
    "/studio/campaigns/",
    "/studio/api-tokens/",
    "/studio/redirects/",
    "/studio/hosts/",
    "/studio/personas/",
    "/studio/questionnaires/",
    "/studio/marketing-pages/",
    "/studio/event-series/",
    "/studio/utm-campaigns/",
    "/studio/imports/",
    "/studio/triggers/subscriptions/",
    "/studio/triggers/widgets/",
    "/studio/users/payment-mismatches/",
    "/studio/projects/",
    "/studio/sync/",
    "/studio/settings/",
    "/studio/notifications/",
    "/studio/articles/",
    "/studio/courses/",
    "/studio/recordings/",
    "/studio/downloads/",
    "/studio/call-hosts/",
    "/studio/ses-events/",
    "/studio/tags/",
    "/studio/maven-events/",
]

ACTIONLESS_ROUTES = {
    "/studio/projects/",
    "/studio/notifications/",
    "/studio/articles/",
    "/studio/courses/",
    "/studio/recordings/",
    "/studio/downloads/",
    "/studio/call-hosts/",
    "/studio/ses-events/",
    "/studio/maven-events/",
}

SCREENSHOT_THEMES = {
    "/studio/events/": "light",
    "/studio/users/": "light",
    "/studio/sync/": "dark",
    "/studio/settings/": "dark",
}


def _staff_context(browser, email):
    _create_staff_user(email)
    return _auth_context(browser, email)


def _seed_past_event():
    from events.models import Event

    Event.objects.filter(slug="stacked-header-past").delete()
    start = timezone.now() - timedelta(days=7)
    Event.objects.create(
        title="Stacked header past event",
        slug="stacked-header-past",
        status="completed",
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        platform="zoom",
        timezone="UTC",
        origin="studio",
    )
    connection.close()


def _assert_no_document_overflow(page):
    widths = page.evaluate(
        """() => ({
          scroll: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth
        })"""
    )
    assert widths["scroll"] <= widths["client"] + 2, widths


def _open_overflow_and_assert_viewport_containment(page):
    details = page.get_by_test_id("studio-header-overflow")
    if details.get_attribute("open") is None:
        page.get_by_label("More actions").click()

    viewport_width = page.viewport_size["width"]
    panel = details.locator(":scope > div")
    boxes = {"panel": panel.bounding_box()}
    for index in range(panel.locator(":scope > *").count()):
        boxes[f"item-{index}"] = panel.locator(":scope > *").nth(index).bounding_box()

    for label, box in boxes.items():
        assert box is not None, label
        assert box["x"] >= -0.5, {label: box, "viewport_width": viewport_width}
        assert box["x"] + box["width"] <= viewport_width + 0.5, {
            label: box,
            "viewport_width": viewport_width,
        }
    return details


def _dismiss_analytics_prompt(page):
    button = page.get_by_role("button", name="Keep analytics off")
    if button.count() and button.is_visible():
        # Saving consent deliberately reloads the page. Wait for that reload
        # so header assertions never race a destroyed execution context in a
        # long full-suite run.
        with page.expect_navigation(wait_until="domcontentloaded"):
            button.click()


def _assert_focus_contract(locator):
    classes = locator.get_attribute("class") or ""
    for token in FOCUS_RING.split():
        assert token in classes
    locator.focus()
    assert locator.evaluate("el => el.matches(':focus-visible')")


@pytest.mark.django_db(transaction=True)
def test_users_import_export_stay_visible_and_mobile_safe(django_server, browser):
    context = _staff_context(browser, "headers-1274-users@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")

    header = page.get_by_test_id("studio-header")
    actions = page.get_by_test_id("studio-header-actions")
    assert header.locator("h1").inner_text() == "Users"
    assert actions.get_by_text("Import contacts", exact=True).is_visible()
    assert actions.get_by_text("Export CSV", exact=True).is_visible()
    assert actions.locator("a").nth(0).get_attribute("href") == "/studio/users/import/"
    assert actions.locator("a").nth(1).get_attribute("href").startswith(
        "/studio/users/export?"
    )
    page.set_viewport_size({"width": 393, "height": 852})
    _assert_no_document_overflow(page)
    context.close()


@pytest.mark.django_db(transaction=True)
def test_events_primary_navigation_and_overflow_destinations(django_server, browser):
    _seed_past_event()
    context = _staff_context(browser, "headers-1274-events@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    actions = page.get_by_test_id("studio-header-actions")
    assert "bg-accent" in page.get_by_test_id("event-new-button").get_attribute("class")
    assert actions.locator(".bg-accent").count() == 1
    assert page.get_by_test_id("event-past-link").is_visible()
    assert "Past events (1)" in page.get_by_test_id("event-past-link").inner_text()
    _open_overflow_and_assert_viewport_containment(page)
    assert page.get_by_test_id("event-series-new-button").is_visible()
    assert page.get_by_test_id("event-duplicates-button").is_visible()

    page.get_by_test_id("event-series-new-button").click()
    page.wait_for_url("**/studio/event-series/new")
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    _open_overflow_and_assert_viewport_containment(page)
    page.get_by_test_id("event-duplicates-button").click()
    page.wait_for_url("**/studio/events/duplicates/")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_past_events_keep_action_hierarchy_and_return_navigation(django_server, browser):
    _seed_past_event()
    context = _staff_context(browser, "headers-1274-past@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/past/", wait_until="domcontentloaded")

    assert page.get_by_test_id("studio-header").locator("h1").inner_text() == "Past events"
    assert page.get_by_test_id("studio-header-actions").locator(".bg-accent").count() == 1
    assert page.get_by_test_id("event-upcoming-link").is_visible()
    assert page.get_by_label("More actions").count() == 1
    page.get_by_test_id("event-upcoming-link").click()
    page.wait_for_url("**/studio/events/")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_events_overflow_only_dismisses_on_outside_click(django_server, browser):
    context = _staff_context(browser, "headers-1274-dismiss@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    details = page.get_by_test_id("studio-header-overflow")
    page.get_by_label("More actions").click()
    assert details.get_attribute("open") is not None
    details.locator("div").evaluate("el => el.click()")
    assert details.get_attribute("open") is not None
    page.get_by_test_id("studio-header").locator("h1").click()
    assert details.get_attribute("open") is None
    context.close()


@pytest.mark.django_db(transaction=True)
def test_events_keyboard_focus_order_and_accessible_menu(django_server, browser):
    context = _staff_context(browser, "headers-1274-keyboard@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    new_event = page.get_by_test_id("event-new-button")
    current_view = page.get_by_test_id("event-past-link")
    trigger = page.get_by_label("More actions")
    for control in (new_event, current_view, trigger):
        _assert_focus_contract(control)

    new_event.focus()
    page.keyboard.press("Tab")
    assert current_view.evaluate("el => el === document.activeElement")
    page.keyboard.press("Tab")
    assert trigger.evaluate("el => el === document.activeElement")
    page.keyboard.press("Enter")
    page.keyboard.press("Tab")
    first_item = page.get_by_test_id("event-series-new-button")
    assert first_item.evaluate("el => el === document.activeElement")
    _assert_focus_contract(first_item)
    assert first_item.evaluate("el => el.getBoundingClientRect().height") >= 44
    context.close()


@pytest.mark.django_db(transaction=True)
def test_sync_actions_metadata_overflow_and_import_card(django_server, browser):
    context = _staff_context(browser, "headers-1274-sync@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/sync/", wait_until="domcontentloaded")

    header = page.locator('header[data-testid]').first
    actions = page.get_by_test_id("studio-header-actions")
    assert header.locator("#sync-live-indicator").count() == 1
    assert actions.locator("#sync-all-form").count() == 1
    assert actions.locator(".bg-accent").count() == 1
    assert actions.get_by_text("Add content source", exact=True).is_visible()
    assert actions.get_by_text("History", exact=True).is_visible()
    _open_overflow_and_assert_viewport_containment(page)
    assert page.get_by_test_id("content-sources-download").is_visible()

    card = page.get_by_test_id("content-sources-import-card")
    assert card.get_by_role("heading", name="Import content sources").is_visible()
    upload = card.get_by_test_id("content-sources-upload")
    assert upload.is_disabled()
    assert header.locator('input[type="file"]').count() == 0
    assert page.get_by_test_id("studio-header-overflow").locator('input[type="file"]').count() == 0
    context.close()


@pytest.mark.django_db(transaction=True)
def test_settings_file_selection_stays_in_body_card(django_server, browser):
    context = _staff_context(browser, "headers-1274-settings@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/settings/", wait_until="domcontentloaded")

    header = page.get_by_test_id("studio-header")
    assert header.get_by_test_id("settings-download").is_visible()
    assert header.locator('input[type="file"]').count() == 0
    card = page.get_by_test_id("settings-import-card")
    upload = card.get_by_test_id("settings-upload")
    assert upload.is_disabled()
    card.locator('input[name="settings_file"]').set_input_files(
        {"name": "settings-1274.json", "mimeType": "application/json", "buffer": b"{}"}
    )
    assert card.locator("[data-upload-filename]").inner_text() == "settings-1274.json"
    assert upload.is_enabled()
    assert page.url.endswith("/studio/settings/")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_projects_pending_metadata_and_zero_state(django_server, browser):
    from content.models import Project

    Project.objects.filter(slug__startswith="stacked-header-project-").delete()
    for number in range(3):
        Project.objects.create(
            title=f"Stacked header project {number}",
            slug=f"stacked-header-project-{number}",
            date=timezone.now().date(),
            status="pending_review",
            published=False,
        )
    connection.close()

    context = _staff_context(browser, "headers-1274-projects@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/projects/", wait_until="domcontentloaded")
    meta = page.get_by_test_id("projects-pending-meta")
    assert meta.inner_text().strip() == "3 pending review"
    assert "bg-yellow-500/20" in meta.get_attribute("class")
    assert page.get_by_test_id("studio-header-actions").count() == 0

    Project.objects.filter(slug__startswith="stacked-header-project-").delete()
    connection.close()
    page.reload(wait_until="domcontentloaded")
    assert page.get_by_test_id("projects-pending-meta").count() == 0
    assert page.get_by_test_id("studio-header-meta").count() == 0
    assert page.get_by_test_id("studio-header-actions").count() == 0
    context.close()


@pytest.mark.django_db(transaction=True)
def test_utm_primary_precedes_visible_import_and_keeps_routes(django_server, browser):
    context = _staff_context(browser, "headers-1274-utm@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/utm-campaigns/", wait_until="domcontentloaded")

    actions = page.get_by_test_id("studio-header-actions").locator(":scope > a")
    assert actions.nth(0).inner_text().strip() == "New UTM campaign"
    assert "bg-accent" in actions.nth(0).get_attribute("class")
    assert actions.nth(1).inner_text().strip() == "Import UTM campaigns"
    assert "bg-accent" not in actions.nth(1).get_attribute("class")
    assert actions.nth(0).get_attribute("href") == "/studio/utm-campaigns/new"
    assert actions.nth(1).get_attribute("href") == "/studio/utm-campaigns/import"
    context.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ROUTE_MATRIX, ids=lambda route: route.strip("/").replace("/", "-"))
def test_all_migrated_routes_are_stacked_and_mobile_safe(route, django_server, browser):
    context = _staff_context(browser, "headers-1274-matrix@test.com")
    page = context.new_page()
    theme = SCREENSHOT_THEMES.get(route, "light")
    page.add_init_script(f"localStorage.setItem('theme', '{theme}')")

    if route in SCREENSHOT_THEMES:
        page.set_viewport_size({"width": 1280, "height": 900})
        response = page.goto(f"{django_server}{route}", wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        _dismiss_analytics_prompt(page)
        if route == "/studio/events/":
            _open_overflow_and_assert_viewport_containment(page)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        slug = route.strip("/").replace("/", "-")
        page.screenshot(
            path=SCREENSHOT_DIR / f"{slug}-desktop-{theme}.png",
            full_page=True,
        )

    page.set_viewport_size({"width": 393, "height": 852})
    response = page.goto(f"{django_server}{route}", wait_until="domcontentloaded")
    assert response is not None and response.status == 200
    _dismiss_analytics_prompt(page)
    header = page.locator('header[data-testid]').first
    assert header.count() == 1
    assert header.locator("h1").is_visible()
    actions = page.get_by_test_id("studio-header-actions")
    if route in ACTIONLESS_ROUTES:
        assert actions.count() == 0
    elif actions.count():
        assert page.evaluate(
            "([title, action]) => Boolean(title.compareDocumentPosition(action) & Node.DOCUMENT_POSITION_FOLLOWING)",
            [header.locator("h1").element_handle(), actions.element_handle()],
        )
    if route in {"/studio/events/", "/studio/sync/"}:
        _open_overflow_and_assert_viewport_containment(page)
    _assert_no_document_overflow(page)

    if route in SCREENSHOT_THEMES:
        slug = route.strip("/").replace("/", "-")
        page.screenshot(
            path=SCREENSHOT_DIR / f"{slug}-mobile-{theme}.png",
            full_page=True,
        )
    context.close()
