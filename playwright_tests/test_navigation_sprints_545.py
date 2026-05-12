"""Playwright coverage for issue #545 navigation and /sprints."""

import datetime
import os
import uuid
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.django_db(transaction=True)

SCREENSHOT_DIR = Path("/tmp/aisl-issue-545-screenshots")


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _clear_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(
    name="May Shipping Sprint",
    slug="may-shipping-sprint",
    status="active",
    min_tier_level=20,
    duration_weeks=4,
):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=datetime.date(2026, 5, 15),
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )


def _desktop_text_nav(page):
    return page.locator('[data-testid="desktop-primary-nav"]')


@pytest.mark.core
def test_anonymous_desktop_navigation_groups_and_sprints_link(
    django_server, page
):
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    nav = _desktop_text_nav(page)
    assert nav.get_by_role("link", name="About").get_attribute("href") == "/about"
    assert nav.get_by_role("link", name="Membership").get_attribute("href") == "/pricing"
    assert nav.get_by_role("link", name="FAQ").get_attribute("href") == "/faq"
    assert nav.get_by_role("button", name="Community").is_visible()
    assert nav.get_by_role("button", name="Resources").is_visible()
    assert page.locator("#community-dropdown a", has_text="Community Sprints").get_attribute("href") == "/sprints"
    assert page.locator("#community-dropdown a", has_text="Events").get_attribute("href") == "/events"
    assert page.locator("#resources-dropdown a", has_text="Courses").get_attribute("href") == "/courses"
    assert page.locator("#resources-dropdown a", has_text="Curated Links").get_attribute("href") == "/resources"
    assert nav.get_by_role("link", name="Activities").count() == 0
    _assert_no_horizontal_overflow(page)
    _shot(page, "01-anonymous-desktop-nav")


@pytest.mark.core
def test_anonymous_mobile_navigation_groups(django_server, browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator("#mobile-menu-btn").click()
    page.locator("#mobile-community-toggle").click()
    page.locator("#mobile-resources-toggle").click()

    menu = page.locator("#mobile-menu")
    assert menu.get_by_role("link", name="About").get_attribute("href") == "/about"
    assert menu.get_by_role("link", name="Membership").get_attribute("href") == "/pricing"
    assert menu.get_by_role("link", name="FAQ").get_attribute("href") == "/faq"

    resources = page.locator("#mobile-resources-list")
    for label in [
        "Courses",
        "Workshops",
        "Learning Path",
        "Project Ideas",
        "Interview Prep",
        "Blog",
        "Curated Links",
    ]:
        assert resources.get_by_text(label, exact=True).is_visible()

    community = page.locator("#mobile-community-list")
    for label in ["Community Sprints", "Events"]:
        assert community.get_by_text(label, exact=True).is_visible()
    assert community.locator('a[href="/sprints"]').count() == 1
    assert menu.get_by_role("link", name="Activities").count() == 0
    _assert_no_horizontal_overflow(page)
    _shot(page, "02-anonymous-mobile-nav")
    context.close()


@pytest.mark.core
def test_authenticated_member_navigation_preserves_account_controls(
    django_server, browser, django_db_blocker
):
    email = _email("member-545")
    with django_db_blocker.unblock():
        create_user(email, tier_slug="main")

    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    nav = _desktop_text_nav(page)
    assert nav.get_by_role("link", name="About").is_visible()
    assert nav.get_by_role("link", name="Membership").is_visible()
    assert nav.get_by_role("link", name="FAQ").is_visible()
    assert nav.get_by_role("button", name="Community").is_visible()
    assert nav.get_by_role("button", name="Resources").is_visible()
    assert page.locator("#notification-bell-btn").is_visible()
    assert page.locator("#account-menu-trigger").is_visible()
    page.locator("#account-menu-trigger").click()
    assert page.locator("#account-menu-dropdown").get_by_role("menuitem", name="Account").is_visible()
    _assert_no_horizontal_overflow(page)
    _shot(page, "03-authenticated-member-nav")
    context.close()


@pytest.mark.core
def test_staff_navigation_preserves_studio_in_account_controls(
    django_server, browser, django_db_blocker
):
    email = _email("staff-545")
    with django_db_blocker.unblock():
        create_staff_user(email)

    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    _desktop_text_nav(page).get_by_role("button", name="Community").wait_for()
    _desktop_text_nav(page).get_by_role("button", name="Resources").wait_for()
    page.locator("#account-menu-trigger").click()
    assert page.locator("#account-menu-dropdown").get_by_role("menuitem", name="Studio").is_visible()
    _shot(page, "04-staff-nav")
    context.close()


@pytest.mark.core
def test_sprints_page_lists_active_sprint(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _clear_sprints()
        _create_sprint()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    card = page.locator('[data-testid="sprints-sprint-card"]').first
    assert card.is_visible()
    text = card.inner_text()
    assert "May Shipping Sprint" in text
    assert "ACTIVE" in text
    assert "May 15, 2026" in text
    assert "4 weeks" in text
    assert "Membership: Main" in text
    assert card.locator('[data-testid="sprints-sprint-cta"]').get_attribute("href") == "/accounts/login/?next=/sprints/may-shipping-sprint"
    _shot(page, "05-sprints-active")


@pytest.mark.core
def test_sprints_page_empty_state(django_server, page, django_db_blocker):
    with django_db_blocker.unblock():
        _clear_sprints()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    empty = page.locator('[data-testid="sprints-empty"]')
    assert empty.is_visible()
    assert "Next sprint coming soon" in empty.inner_text()
    assert page.locator('[data-testid="sprints-sprint-card"]').count() == 0
    _shot(page, "06-sprints-empty")


@pytest.mark.core
def test_sprints_draft_visibility(django_server, browser, page, django_db_blocker):
    staff_email = _email("staff-draft-545")
    member_email = _email("member-draft-545")
    with django_db_blocker.unblock():
        _clear_sprints()
        _create_sprint(name="Public Sprint", slug="public-sprint")
        _create_sprint(name="Draft Sprint", slug="draft-sprint", status="draft")
        create_user(member_email)
        create_staff_user(staff_email)

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    assert "Public Sprint" in page.locator("body").inner_text()
    assert "Draft Sprint" not in page.locator("body").inner_text()

    member_context = auth_context(browser, member_email)
    member_page = member_context.new_page()
    member_page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    assert "Draft Sprint" not in member_page.locator("body").inner_text()
    member_context.close()

    staff_context = auth_context(browser, staff_email)
    staff_page = staff_context.new_page()
    staff_page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    assert "Draft Sprint" in staff_page.locator("body").inner_text()
    _shot(staff_page, "07-sprints-staff-draft")
    staff_context.close()


@pytest.mark.core
def test_existing_activities_page_still_loads(django_server, page):
    page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

    assert page.locator('[data-testid="activities-sprints-section"]').is_visible()
    assert page.get_by_text("Member activities and support").is_visible()
    _shot(page, "08-activities-regression")
