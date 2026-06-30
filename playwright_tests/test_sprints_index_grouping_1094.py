"""Playwright coverage for grouped /sprints discovery sections (#1094)."""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_staff_user,
    create_user,
    ensure_site_config_tiers,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.core,
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
]

SCREENSHOT_DIR = (
    Path(__file__).resolve().parents[1] / ".tmp" / "aisl-issue-1094-screenshots"
)


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _clear_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(
    name,
    slug,
    *,
    start_date,
    duration_weeks=4,
    status="active",
    min_tier_level=20,
):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date,
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _today():
    return datetime.date.today()


def _section(page, key):
    return page.get_by_test_id(f"sprints-section-{key}")


def _section_card_names(page, key):
    return [
        text.strip()
        for text in _section(page, key)
        .locator('[data-testid="sprints-sprint-name"]')
        .all_inner_texts()
    ]


def _card_for_slug(page, slug):
    return page.locator(
        f'[data-testid="sprints-sprint-card"]:has(a[href$="/sprints/{slug}"])'
    )


def test_visitor_scans_current_future_and_past_groups(django_server, page, django_db_blocker):
    today = _today()
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()
        _clear_sprints()
        _create_sprint(
            "Current Group Sprint",
            "current-group-sprint",
            start_date=today - datetime.timedelta(days=7),
        )
        _create_sprint(
            "Future Group Sprint",
            "future-group-sprint",
            start_date=today + datetime.timedelta(days=14),
        )
        _create_sprint(
            "Past Group Sprint",
            "past-group-sprint",
            start_date=today - datetime.timedelta(days=42),
            status="completed",
        )

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    headings = page.locator('[data-testid="sprints-section-heading"]').all_inner_texts()
    assert [heading.strip() for heading in headings] == [
        "Current sprint",
        "Future sprint",
        "Past sprint",
    ]
    assert _section_card_names(page, "current") == ["Current Group Sprint"]
    assert _section_card_names(page, "future") == ["Future Group Sprint"]
    assert _section_card_names(page, "past") == ["Past Group Sprint"]
    assert _section(page, "current").bounding_box()["y"] < _section(page, "future").bounding_box()["y"]
    assert _section(page, "future").bounding_box()["y"] < _section(page, "past").bounding_box()["y"]
    assert "ENDED" in _card_for_slug(page, "past-group-sprint").inner_text()
    _shot(page, "01-visitor-current-future-past")


def test_visitor_sees_plural_current_heading_and_sorted_current_cards(
    django_server, page, django_db_blocker
):
    today = _today()
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()
        _clear_sprints()
        _create_sprint(
            "Later Current",
            "later-current",
            start_date=today - datetime.timedelta(days=5),
        )
        _create_sprint(
            "Earlier Current",
            "earlier-current",
            start_date=today - datetime.timedelta(days=10),
        )

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    heading = _section(page, "current").get_by_test_id("sprints-section-heading")
    assert heading.inner_text().strip() == "Current sprints"
    assert _section_card_names(page, "current") == ["Earlier Current", "Later Current"]
    assert "No future sprints are scheduled yet." in _section(page, "future").inner_text()
    assert "No past sprints yet." in _section(page, "past").inner_text()
    _shot(page, "02-plural-current-sorted")


def test_member_ctas_survive_grouping(django_server, browser, django_db_blocker):
    today = _today()
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()
        _clear_sprints()
        member = create_user("issue1094-main@example.com", tier_slug="main")
        from plans.models import Plan, SprintEnrollment

        current = _create_sprint(
            "Current Enrolled Sprint",
            "current-enrolled-sprint",
            start_date=today - datetime.timedelta(days=7),
            min_tier_level=20,
        )
        _create_sprint(
            "Premium Future Sprint",
            "premium-future-sprint",
            start_date=today + datetime.timedelta(days=14),
            min_tier_level=30,
        )
        SprintEnrollment.objects.create(sprint=current, user=member)
        plan = Plan.objects.create(member=member, sprint=current, visibility="cohort")
        from django.db import connection

        connection.close()

    context = auth_context(browser, "issue1094-main@example.com")
    page = context.new_page()
    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    current_card = _card_for_slug(page, "current-enrolled-sprint")
    assert current_card.locator('[data-testid="sprints-sprint-cta"]').inner_text().strip().startswith(
        "Open my plan"
    )
    assert "Upgrade to Premium" in _card_for_slug(page, "premium-future-sprint").inner_text()

    current_card.locator('[data-testid="sprints-sprint-cta"]').click()
    page.wait_for_url(f"**/sprints/current-enrolled-sprint/plan/{plan.pk}")
    _shot(page, "03-member-open-my-plan")
    context.close()


def test_visitor_sees_single_page_empty_state_when_no_sprints_visible(
    django_server, page, django_db_blocker
):
    today = _today()
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()
        _clear_sprints()
        _create_sprint(
            "Draft Hidden Sprint",
            "draft-hidden-sprint",
            start_date=today - datetime.timedelta(days=7),
            status="draft",
        )
        _create_sprint(
            "Cancelled Hidden Sprint",
            "cancelled-hidden-sprint",
            start_date=today - datetime.timedelta(days=7),
            status="cancelled",
        )

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

    empty = page.get_by_test_id("sprints-empty")
    assert empty.is_visible()
    assert "Next sprint coming soon" in empty.inner_text()
    assert empty.get_by_role("link", name="Events").get_attribute("href") == "/events"
    assert empty.get_by_role("link", name="Workshops").get_attribute("href") == "/workshops"
    assert page.locator('[data-testid^="sprints-section-"]').count() == 0
    _shot(page, "04-page-empty-state")


def test_staff_previews_draft_sprint_but_cancelled_stays_hidden(
    django_server, browser, django_db_blocker
):
    today = _today()
    with django_db_blocker.unblock():
        ensure_tiers()
        ensure_site_config_tiers()
        _clear_sprints()
        create_user("issue1094-member@example.com", tier_slug="main")
        create_staff_user("issue1094-staff@example.com")
        _create_sprint(
            "Draft Current Sprint",
            "draft-current-sprint",
            start_date=today - datetime.timedelta(days=7),
            status="draft",
        )
        _create_sprint(
            "Cancelled Current Sprint",
            "cancelled-current-sprint",
            start_date=today - datetime.timedelta(days=7),
            status="cancelled",
        )

    member_context = auth_context(browser, "issue1094-member@example.com")
    member_page = member_context.new_page()
    member_page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    assert "Draft Current Sprint" not in member_page.locator("body").inner_text()
    member_context.close()

    staff_context = auth_context(browser, "issue1094-staff@example.com")
    staff_page = staff_context.new_page()
    staff_page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    current_text = _section(staff_page, "current").inner_text()
    assert "Draft Current Sprint" in current_text
    assert "Cancelled Current Sprint" not in staff_page.locator("body").inner_text()
    _shot(staff_page, "05-staff-draft-current")
    staff_context.close()
