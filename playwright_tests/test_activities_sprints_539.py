"""Browser coverage for the canonical sprint preview on Membership (#539)."""

import datetime
import os

import pytest
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user, ensure_site_config_tiers, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _current_start(days_ago=14):
    return datetime.date.today() - datetime.timedelta(days=days_ago)


def _reset_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(
    *,
    name,
    slug,
    start_date=None,
    status="active",
    min_tier_level=20,
):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date or _current_start(),
        duration_weeks=4,
        status=status,
        min_tier_level=min_tier_level,
        description="A focused cohort for shipping a useful AI product.",
        outcomes="A shipped project\nA repeatable delivery habit",
        audience="Builders who want structure\nTeams learning AI delivery",
    )
    connection.close()
    return sprint


def _seed_base():
    ensure_tiers()
    ensure_site_config_tiers()
    _reset_sprints()


def test_membership_renders_only_the_first_current_sprint_with_canonical_card(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()
        first = _create_sprint(
            name="First Membership Sprint",
            slug="first-membership-sprint",
            start_date=_current_start(14),
        )
        _create_sprint(
            name="Second Membership Sprint",
            slug="second-membership-sprint",
            start_date=_current_start(13),
        )

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    section = page.get_by_test_id("membership-sprints-section")
    card = page.get_by_test_id("membership-sprint-card")
    expect(section).to_contain_text(first.name)
    expect(section).not_to_contain_text("Second Membership Sprint")
    assert card.count() == 1
    assert card.locator("a").count() == 1
    detail = page.get_by_test_id("membership-sprint-detail-link")
    assert detail.get_attribute("href") == "/sprints/first-membership-sprint"
    assert card.get_by_test_id("sprints-sprint-context").is_visible()
    assert card.get_by_test_id("sprints-sprint-dates").is_visible()
    assert card.get_by_test_id("sprints-sprint-tier").get_attribute(
        "data-required-level"
    ) == "20"

    detail.click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.rstrip("/").endswith("/sprints/first-membership-sprint")
    expect(page.get_by_test_id("sprint-detail-name")).to_contain_text(first.name)


def test_stale_active_sprint_is_hidden_from_membership(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()
        _create_sprint(
            name="Old Active Sprint",
            slug="old-active-sprint",
            start_date=_current_start(70),
        )
        _create_sprint(
            name="Current Active Sprint",
            slug="current-active-sprint",
        )

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    section = page.get_by_test_id("membership-sprints-section")
    expect(section).to_contain_text("Current Active Sprint")
    expect(section).not_to_contain_text("Old Active Sprint")


def test_anonymous_and_member_do_not_see_draft_but_staff_can_preview_one(
    django_server, browser, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()
        _create_sprint(
            name="Draft Membership Sprint",
            slug="draft-membership-sprint",
            start_date=datetime.date.today() + datetime.timedelta(days=14),
            status="draft",
        )
        create_user("member-539@test.com", tier_slug="main")
        create_user("staff-539@test.com", tier_slug="premium", is_staff=True)

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")
    expect(page.get_by_test_id("membership-sprints-section")).not_to_contain_text(
        "Draft Membership Sprint"
    )

    member_context = auth_context(browser, "member-539@test.com")
    member_page = member_context.new_page()
    member_page.goto(f"{django_server}/membership", wait_until="domcontentloaded")
    expect(member_page.get_by_test_id("membership-sprints-section")).not_to_contain_text(
        "Draft Membership Sprint"
    )
    member_context.close()

    staff_context = auth_context(browser, "staff-539@test.com")
    staff_page = staff_context.new_page()
    staff_page.goto(f"{django_server}/membership", wait_until="domcontentloaded")
    expect(staff_page.get_by_test_id("membership-sprint-card")).to_contain_text(
        "Draft Membership Sprint"
    )
    expect(staff_page.get_by_test_id("membership-sprint-card")).to_contain_text(
        "Draft"
    )
    staff_context.close()


def test_free_member_reaches_premium_sprint_detail_and_upgrade_action(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()
        _create_sprint(
            name="Premium Membership Sprint",
            slug="premium-membership-sprint",
            min_tier_level=30,
        )
        create_user("free-539@test.com", tier_slug="free")

    context = auth_context(browser, "free-539@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    card = page.get_by_test_id("membership-sprint-card")
    assert card.get_by_test_id("sprints-sprint-tier").get_attribute(
        "data-required-level"
    ) == "30"
    page.get_by_test_id("membership-sprint-detail-link").click()
    page.wait_for_load_state("domcontentloaded")
    assert page.url.rstrip("/").endswith("/sprints/premium-membership-sprint")
    expect(page.get_by_test_id("sprint-cta-upgrade")).to_be_visible()
    assert page.get_by_test_id("sprint-cta-upgrade").get_attribute("href") == "/membership"
    context.close()


def test_sprint_empty_state_keeps_events_and_workshops_available(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        _seed_base()

    page.goto(f"{django_server}/membership", wait_until="domcontentloaded")

    empty = page.get_by_test_id("membership-sprints-empty")
    expect(empty).to_contain_text("Next sprint coming soon")
    assert empty.locator('a[href="/events"]').count() == 1
    assert empty.locator('a[href="/workshops"]').count() == 1
    assert page.get_by_test_id("membership-sprint-card").count() == 0
