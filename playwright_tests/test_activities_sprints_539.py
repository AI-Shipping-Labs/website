"""Playwright coverage for the sprint-first /activities redesign.

Screenshots are written to ``/tmp/aisl-issue-539-screenshots`` for tester
review.
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_site_config_tiers, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

SCREENSHOT_DIR = Path("/tmp/aisl-issue-539-screenshots")


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _clear_activity_data():
    from django.db import connection

    from content.models import CuratedLink
    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    CuratedLink.objects.all().delete()
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


def _create_curated_link():
    from django.db import connection

    from content.models import CuratedLink

    CuratedLink.objects.create(
        item_id="library-link-539",
        title="Useful Reference",
        description="A durable library link.",
        url="https://example.com/reference",
        category="articles",
        published=True,
    )
    connection.close()


def _seed_base(active=True):
    ensure_tiers()
    ensure_site_config_tiers()
    _clear_activity_data()
    if active:
        _create_sprint()


def _assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "() => document.documentElement.scrollWidth <= "
        "document.documentElement.clientWidth"
    )


def _top(primary_locator):
    box = primary_locator.bounding_box()
    assert box is not None
    return box["y"]


def _primary_nav_labels(page):
    return page.evaluate(
        """() => {
          const nav = document.querySelector('header nav');
          const labels = [];
          for (const el of nav.querySelectorAll('a, button')) {
            const text = el.textContent.trim();
            if (['About', 'Membership', 'Activities', 'Resources', 'FAQ'].includes(text)) {
              labels.push(text);
            }
          }
          return labels;
        }"""
    )


@pytest.mark.django_db(transaction=True)
class TestActivitiesSprintFirstLayout:
    def test_anonymous_desktop_discovers_active_sprints_first(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        assert _primary_nav_labels(page)[:5] == [
            "About",
            "Membership",
            "Activities",
            "Resources",
            "FAQ",
        ]
        page.get_by_role("heading", name="Active community sprints").wait_for()
        card = page.locator('[data-testid="activities-sprint-card"]').first
        assert _top(card) < 900
        body = page.locator("body").inner_text()
        assert "May Shipping Sprint" in body
        assert "Active" in body
        assert "May 15, 2026" in body
        assert "4 weeks" in body
        assert "Membership: Main" in body
        cta = card.locator('[data-testid="activities-sprint-cta"]')
        assert "Log in to join" in cta.inner_text()
        assert "/accounts/login/?next=/sprints/may-shipping-sprint" in (
            cta.get_attribute("href")
        )
        secondary_top = _top(page.locator('[data-testid="activities-secondary-nav"]'))
        assert _top(card) < secondary_top
        _assert_no_horizontal_overflow(page)
        _shot(page, "01-activities-anonymous-desktop")

    def test_mobile_anonymous_sees_sprint_within_first_600px(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        context = browser.new_context(viewport={"width": 393, "height": 851})
        page = context.new_page()
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        heading = page.get_by_role("heading", name="Active community sprints")
        card = page.locator('[data-testid="activities-sprint-card"]').first
        assert _top(heading) < 220
        assert _top(card) < 600
        _assert_no_horizontal_overflow(page)
        _shot(page, "02-activities-anonymous-pixel7")
        context.close()

    def test_community_sprints_anchor_lands_on_live_section(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(
            f"{django_server}/activities#community-sprints",
            wait_until="domcontentloaded",
        )

        section = page.locator('[data-testid="activities-sprints-section"]')
        assert section.is_visible()
        assert page.locator("#community-sprints").count() == 1
        assert "May Shipping Sprint" in section.inner_text()
        _shot(page, "03-activities-anchor-desktop")

    def test_empty_state_links_to_events_and_workshops(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=False)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        empty = page.locator('[data-testid="activities-sprints-empty"]')
        assert empty.is_visible()
        assert "Next sprint coming soon" in empty.inner_text()
        assert empty.locator('a[href="/events"]').count() == 1
        assert empty.locator('a[href="/workshops"]').count() == 1
        assert page.locator('[data-testid="activities-sprint-card"]').count() == 0
        _shot(page, "04-activities-empty-state")

    def test_free_member_understands_premium_requirement(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=False)
            _create_sprint(
                name="Premium Shipping Sprint",
                slug="premium-shipping-sprint",
                min_tier_level=30,
            )
            _create_user("free-539@test.com", tier_slug="free")

        ctx = _auth_context(browser, "free-539@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        card = page.locator('[data-testid="activities-sprint-card"]').first
        assert "Membership: Premium" in card.inner_text()
        cta = card.locator('[data-testid="activities-sprint-cta"]')
        assert "Upgrade to Premium" in cta.inner_text()
        assert cta.get_attribute("href") == "/pricing"
        ctx.close()

    def test_main_member_cta_reaches_sprint_detail(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)
            _create_user("main-539@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main-539@test.com")
        page = ctx.new_page()
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        page.locator('[data-testid="activities-sprint-cta"]').first.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/sprints/may-shipping-sprint")
        assert page.locator('[data-testid="sprint-detail-name"]').is_visible()
        _shot(page, "05-activities-main-member-destination")
        ctx.close()

    def test_staff_can_preview_draft_without_public_exposure(
        self, django_server, browser, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=False)
            _create_sprint(name="Public Sprint", slug="public-sprint")
            _create_sprint(name="Draft Sprint", slug="draft-sprint", status="draft")
            _create_user("staff-539@test.com", tier_slug="premium", is_staff=True)

        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")
        assert "Public Sprint" in page.locator("body").inner_text()
        assert "Draft Sprint" not in page.locator("body").inner_text()

        ctx = _auth_context(browser, "staff-539@test.com")
        staff_page = ctx.new_page()
        staff_page.set_viewport_size({"width": 1280, "height": 900})
        staff_page.goto(f"{django_server}/activities", wait_until="domcontentloaded")
        section_text = staff_page.locator(
            '[data-testid="activities-sprints-section"]'
        ).inner_text()
        assert "Draft Sprint" in section_text
        assert "Draft" in section_text
        _shot(staff_page, "06-activities-staff-draft")
        ctx.close()

    def test_secondary_nav_and_tier_filters_remain_available(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        secondary = page.locator('[data-testid="activities-secondary-nav"]')
        assert secondary.locator('a[href="/events"]').count() == 1
        assert secondary.locator('a[href="/workshops"]').count() == 1
        assert _top(page.locator('[data-testid="activities-sprint-card"]').first) < (
            _top(secondary)
        )

        page.get_by_role("button", name="Basic").click()
        assert "active" in page.locator('.tier-filter-btn[data-tier="basic"]').get_attribute(
            "class"
        )
        page.get_by_role("button", name="Main").click()
        assert "active" in page.locator('.tier-filter-btn[data-tier="main"]').get_attribute(
            "class"
        )
        page.get_by_text("Quick comparison").scroll_into_view_if_needed()
        assert page.get_by_text("Quick comparison").is_visible()

    def test_resources_stays_library_without_sprint_hub(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)
            _create_curated_link()

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/resources", wait_until="domcontentloaded")

        body = page.locator("body").inner_text()
        assert "Useful Reference" in body
        assert "CURATED LINKS" in body
        assert "May Shipping Sprint" not in body
        assert page.locator('[data-testid="activities-sprint-card"]').count() == 0
        assert page.locator('[data-testid="activities-sprints-section"]').count() == 0
        _shot(page, "07-resources-no-sprint-hub")
