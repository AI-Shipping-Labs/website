"""Playwright coverage for the public header account menu (issue #475)."""

import os
import uuid

import pytest

from playwright_tests.conftest import auth_context, create_staff_user, create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"


def _seed_user(db_blocker, email, **kwargs):
    with db_blocker.unblock():
        return create_user(email=email, **kwargs)


def _seed_user_with_plan(db_blocker, email, **kwargs):
    with db_blocker.unblock():
        import datetime

        from django.utils import timezone

        from plans.models import Plan, Sprint

        user = create_user(email=email, **kwargs)
        sprint, _ = Sprint.objects.get_or_create(
            slug=f"header-menu-{uuid.uuid4().hex[:8]}",
            defaults={
                "name": "Header Menu Sprint",
                "start_date": timezone.localdate() - datetime.timedelta(days=7),
                "duration_weeks": 4,
                "status": "active",
            },
        )
        plan = Plan.objects.create(
            member=user,
            sprint=sprint,
            visibility="private",
        )
        return user, plan


def _create_sprint(slug_prefix, *, start_offset, duration_weeks=4, status="active"):
    import datetime

    from django.utils import timezone

    from plans.models import Sprint

    return Sprint.objects.create(
        name=slug_prefix.replace("-", " ").title(),
        slug=f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
        start_date=timezone.localdate() + datetime.timedelta(days=start_offset),
        duration_weeks=duration_weeks,
        status=status,
    )


def _set_plan_created_at(plan, *, days_ago):
    import datetime

    from django.utils import timezone

    from plans.models import Plan

    Plan.objects.filter(pk=plan.pk).update(
        created_at=timezone.now() - datetime.timedelta(days=days_ago),
    )
    plan.refresh_from_db()
    return plan


def _seed_user_with_current_and_stale_ended_plan(db_blocker, email):
    with db_blocker.unblock():
        from django.db import connection

        from plans.models import Plan

        user = create_user(email=email, tier_slug="main", first_name="Current")
        current = _create_sprint("header-current", start_offset=-7)
        ended = _create_sprint(
            "header-ended",
            start_offset=-70,
            duration_weeks=4,
            status="completed",
        )
        current_plan = Plan.objects.create(member=user, sprint=current)
        ended_plan = Plan.objects.create(member=user, sprint=ended)
        _set_plan_created_at(current_plan, days_ago=30)
        _set_plan_created_at(ended_plan, days_ago=1)
        connection.close()
        return current_plan, ended_plan


def _seed_user_with_upcoming_and_stale_ended_plan(db_blocker, email):
    with db_blocker.unblock():
        from django.db import connection

        from plans.models import Plan

        user = create_user(email=email, tier_slug="main", first_name="Upcoming")
        upcoming = _create_sprint("header-upcoming", start_offset=14)
        ended = _create_sprint(
            "header-upcoming-ended",
            start_offset=-70,
            duration_weeks=4,
            status="completed",
        )
        upcoming_plan = Plan.objects.create(member=user, sprint=upcoming)
        ended_plan = Plan.objects.create(member=user, sprint=ended)
        _set_plan_created_at(upcoming_plan, days_ago=30)
        _set_plan_created_at(ended_plan, days_ago=1)
        connection.close()
        return upcoming_plan, ended_plan


def test_desktop_account_menu_opens_and_closes_by_keyboard(
    django_server, browser, django_db_blocker
):
    email = _email("header-menu")
    _seed_user(django_db_blocker, email, first_name="Ada")
    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    assert page.locator("#notification-bell-btn").is_visible()
    assert page.locator("#account-menu-trigger").is_visible()
    assert not page.get_by_role("link", name=email).is_visible()

    trigger = page.locator("#account-menu-trigger")
    trigger.click()
    menu = page.locator("#account-menu-dropdown")
    assert trigger.get_attribute("aria-expanded") == "true"
    assert menu.is_visible()
    for label in ["Account", "Profile", "Theme", "Log out"]:
        assert menu.get_by_text(label, exact=True).is_visible()

    page.keyboard.press("Escape")
    assert trigger.get_attribute("aria-expanded") == "false"
    assert not menu.is_visible()

    trigger.click()
    page.mouse.click(640, 500)
    assert trigger.get_attribute("aria-expanded") == "false"
    assert not menu.is_visible()

    context.close()


def test_desktop_header_dropdowns_are_mutually_exclusive(
    django_server, browser, django_db_blocker
):
    email = _email("header-exclusive")
    _seed_user(django_db_blocker, email, first_name="Ada")
    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    account_trigger = page.locator("#account-menu-trigger")
    account_menu = page.locator("#account-menu-dropdown")
    notification_button = page.locator("#notification-bell-btn")
    notification_dropdown = page.locator("#notification-dropdown")

    account_trigger.click()
    assert account_menu.is_visible()
    assert account_trigger.get_attribute("aria-expanded") == "true"

    notification_button.click()
    notification_dropdown.wait_for(state="visible", timeout=5000)
    assert not account_menu.is_visible()
    assert account_trigger.get_attribute("aria-expanded") == "false"
    assert notification_button.get_attribute("aria-expanded") == "true"

    notification_box = notification_dropdown.bounding_box()
    assert notification_box is not None
    top_element_id = page.evaluate(
        """([x, y]) => {
            var element = document.elementFromPoint(x, y);
            return element ? element.closest('#account-menu-dropdown, #notification-dropdown').id : null;
        }""",
        [
            notification_box["x"] + notification_box["width"] / 2,
            notification_box["y"] + 24,
        ],
    )
    assert top_element_id == "notification-dropdown"

    account_trigger.click()
    assert account_menu.is_visible()
    assert account_trigger.get_attribute("aria-expanded") == "true"
    assert not notification_dropdown.is_visible()
    assert notification_button.get_attribute("aria-expanded") == "false"

    page.mouse.click(640, 500)
    assert not account_menu.is_visible()
    assert account_trigger.get_attribute("aria-expanded") == "false"

    notification_button.click()
    assert notification_dropdown.is_visible()
    page.mouse.click(640, 500)
    assert not notification_dropdown.is_visible()
    assert notification_button.get_attribute("aria-expanded") == "false"

    context.close()


def test_staff_desktop_account_menu_includes_studio_link(
    django_server, browser, django_db_blocker
):
    email = _email("header-staff")
    with django_db_blocker.unblock():
        create_staff_user(email)
    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator("#account-menu-trigger").click()
    menu = page.locator("#account-menu-dropdown")
    assert menu.is_visible()
    assert menu.get_by_role("menuitem", name="Studio").is_visible()
    assert menu.get_by_text("Theme", exact=True).is_visible()

    context.close()


def test_member_with_plan_desktop_account_menu_includes_plan_link(
    django_server, browser, django_db_blocker
):
    email = _email("header-plan")
    _, plan = _seed_user_with_plan(django_db_blocker, email, first_name="Plan")
    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator("#account-menu-trigger").click()
    menu = page.locator("#account-menu-dropdown")
    plan_link = menu.locator('[data-testid="header-plan-link"]')
    assert menu.is_visible()
    assert plan_link.is_visible()
    assert plan_link.get_attribute("href") == (
        f"/sprints/{plan.sprint.slug}/plan/{plan.pk}"
    )

    context.close()


@pytest.mark.core
def test_desktop_account_menu_uses_next_upcoming_plan_over_stale_ended_plan(
    django_server, browser, django_db_blocker
):
    email = _email("header-upcoming-plan")
    upcoming_plan, ended_plan = _seed_user_with_upcoming_and_stale_ended_plan(
        django_db_blocker,
        email,
    )
    context = auth_context(browser, email)
    page = context.new_page()
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator("#account-menu-trigger").click()
    plan_link = page.locator("#account-menu-dropdown").locator(
        '[data-testid="header-plan-link"]'
    )
    assert plan_link.inner_text().strip() == "Plan"
    assert plan_link.get_attribute("href") == (
        f"/sprints/{upcoming_plan.sprint.slug}/plan/{upcoming_plan.pk}"
    )

    plan_link.click()
    assert f"/sprints/{upcoming_plan.sprint.slug}/plan/{upcoming_plan.pk}" in page.url
    assert f"/sprints/{ended_plan.sprint.slug}/plan/{ended_plan.pk}" not in page.url
    assert ended_plan.sprint.slug not in page.url

    context.close()


@pytest.mark.core
def test_mobile_account_menu_uses_current_plan_over_stale_ended_plan(
    django_server, browser, django_db_blocker
):
    email = _email("header-mobile-current")
    current_plan, ended_plan = _seed_user_with_current_and_stale_ended_plan(
        django_db_blocker,
        email,
    )
    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator("#mobile-menu-btn").click()
    plan_link = page.locator('[data-testid="mobile-header-plan-link"]')
    assert plan_link.is_visible()
    assert plan_link.inner_text().strip() == "Plan"
    assert plan_link.get_attribute("href") == (
        f"/sprints/{current_plan.sprint.slug}/plan/{current_plan.pk}"
    )

    plan_link.click()
    assert f"/sprints/{current_plan.sprint.slug}/plan/{current_plan.pk}" in page.url
    assert f"/sprints/{ended_plan.sprint.slug}/plan/{ended_plan.pk}" not in page.url
    assert ended_plan.sprint.slug not in page.url

    context.close()


def test_anonymous_header_has_sign_in_without_account_menu(django_server, page):
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    assert page.get_by_role("link", name="Sign in").first.is_visible()
    assert page.locator("#account-menu-trigger").count() == 0
    assert page.locator("#account-menu-dropdown").count() == 0


def test_mobile_account_section_and_text_nav_coexist_without_overflow(
    django_server, browser, django_db_blocker
):
    email = (
        "very.long.account.identity.for.header.menu.regression."
        f"{uuid.uuid4().hex[:10]}@example.com"
    )
    _seed_user(django_db_blocker, email)
    context = auth_context(browser, email)
    page = context.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    page.locator("#mobile-menu-btn").click()
    page.locator("#mobile-community-toggle").click()
    page.locator("#mobile-resources-toggle").click()

    menu = page.locator("#mobile-menu")
    assert menu.is_visible()
    assert page.locator("#mobile-community-list").is_visible()
    assert page.locator("#mobile-resources-list").is_visible()
    assert page.locator('[data-testid="mobile-account-section"]').is_visible()
    for label in ["Notifications", "Account", "Log out"]:
        assert menu.get_by_role("link", name=label).is_visible()
    assert menu.get_by_role("link", name="Profile").count() == 0
    assert menu.get_by_text("Theme", exact=True).is_visible()

    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth > window.innerWidth"
    )
    assert not overflow

    context.close()
