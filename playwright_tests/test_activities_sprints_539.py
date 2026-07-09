"""Playwright coverage for the /activities tier benefits and sprint hub.

Screenshots are written to ``.tmp/screenshots/issue-1181`` for tester
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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1181")


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


def _active_sprint_start():
    return datetime.date.today() - datetime.timedelta(days=14)


def _expected_sprint_range(start_date, duration_weeks):
    end_date = start_date + datetime.timedelta(weeks=duration_weeks)
    if start_date.year == end_date.year:
        return (
            f"{start_date:%B} {start_date.day} – "
            f"{end_date:%B} {end_date.day}, {end_date.year} "
            f"({duration_weeks} weeks)"
        )
    return (
        f"{start_date:%B} {start_date.day}, {start_date.year} – "
        f"{end_date:%B} {end_date.day}, {end_date.year} "
        f"({duration_weeks} weeks)"
    )


def _create_sprint(
    name="May Shipping Sprint",
    slug="may-shipping-sprint",
    status="active",
    min_tier_level=20,
    duration_weeks=4,
    start_date=None,
):
    from django.db import connection

    from plans.models import Sprint

    if start_date is None:
        start_date = _active_sprint_start()

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


def _bottom(primary_locator):
    box = primary_locator.bounding_box()
    assert box is not None
    return box["y"] + box["height"]


def _primary_nav_labels(page):
    return page.evaluate(
        """() => {
          const nav = document.querySelector(
            '[data-testid="desktop-primary-nav"]'
          );
          const labels = [];
          for (const el of nav.querySelectorAll(':scope > div > button')) {
            const text = el.textContent.trim();
            if (text) {
              labels.push(text);
            }
          }
          return labels;
        }"""
    )


def _visible_activity_titles(page):
    return page.locator(
        '[data-testid="activity-card"]:visible [data-testid="activity-card-title"]'
    ).all_inner_texts()


@pytest.mark.django_db(transaction=True)
class TestActivitiesAccessByTierLayout:
    def test_anonymous_desktop_discovers_tier_benefits_before_sprints(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        assert _primary_nav_labels(page) == ["About", "Community", "Resources"]
        page.get_by_role("heading", name="Membership benefits by tier").wait_for()
        benefits = page.locator('[data-testid="activities-access-by-tier-section"]')
        sprints = page.locator('[data-testid="activities-sprints-section"]')
        assert benefits.get_attribute("id") == "access-by-tier"
        assert _top(benefits) < _top(sprints)
        assert _top(page.get_by_role("heading", name="Membership benefits by tier")) < 260
        assert page.locator('[data-testid="activities-tier-filter"]').count() == 4
        assert page.locator('[data-testid="activity-card"]').count() == 15
        assert page.locator('[data-testid="activities-quick-comparison"]').is_visible()

        page.get_by_role("heading", name="Active community sprints").wait_for()
        intro = page.locator('[data-testid="activities-sprints-intro-row"]')
        card = page.locator('[data-testid="activities-sprint-card"]').first
        assert _bottom(intro) <= _top(card)
        body = page.locator("body").inner_text()
        assert "May Shipping Sprint" in body
        assert "Active" in body
        assert _expected_sprint_range(_active_sprint_start(), 4) in body
        assert "Membership: Main" in body
        cta = card.locator('[data-testid="activities-sprint-cta"]')
        assert "Log in to join" in cta.inner_text()
        assert "/accounts/login/?next=/sprints/may-shipping-sprint" in (
            cta.get_attribute("href")
        )
        assert _top(card.locator('[data-testid="activities-sprint-dates"]')) > _top(
            card.locator('[data-testid="activities-sprint-name"]')
        )
        assert _top(cta) > _top(
            card.locator('[data-testid="activities-sprint-guidance"]')
        )
        secondary_top = _top(page.locator('[data-testid="activities-secondary-nav"]'))
        assert _top(card) < secondary_top
        _assert_no_horizontal_overflow(page)
        _shot(page, "01-activities-anonymous-desktop")

    def test_mobile_anonymous_sees_tier_benefits_first(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        context = browser.new_context(viewport={"width": 393, "height": 851})
        page = context.new_page()
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        heading = page.get_by_role("heading", name="Membership benefits by tier")
        benefits = page.locator('[data-testid="activities-access-by-tier-section"]')
        sprints = page.locator('[data-testid="activities-sprints-section"]')
        first_activity = page.locator('[data-testid="activity-card"]').first
        assert _top(heading) < 220
        assert _top(first_activity) < 650
        assert _top(benefits) < _top(sprints)
        assert page.locator('[data-testid="activities-tier-filter"]').count() == 4
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

    def test_stale_active_sprint_is_hidden_from_public_activities(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=False)
            _create_sprint(
                name="Old Active Sprint",
                slug="old-active-sprint",
                start_date=datetime.date.today() - datetime.timedelta(days=70),
            )
            _create_sprint(
                name="Current Active Sprint",
                slug="current-active-sprint",
            )

        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        section_text = page.locator(
            '[data-testid="activities-sprints-section"]'
        ).inner_text()
        assert "Current Active Sprint" in section_text
        assert "Old Active Sprint" not in section_text

    def test_empty_state_links_to_events_and_workshops(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=False)

        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        empty = page.locator('[data-testid="activities-sprints-empty"]')
        assert empty.is_visible()
        assert _bottom(page.locator('[data-testid="activities-sprints-intro-row"]')) <= _top(
            empty
        )
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

    def test_pricing_compare_link_lands_on_access_by_tier_without_changing_ctas(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        payment_links_before = page.locator(".tier-cta-link").evaluate_all(
            "(links) => links.map((link) => link.getAttribute('href'))"
        )
        page.locator('[data-testid="pricing-activities-compare-link"]').click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith("/activities#access-by-tier")
        assert page.locator("#access-by-tier").is_visible()
        assert page.get_by_role(
            "heading", name="Membership benefits by tier"
        ).is_visible()

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        payment_links_after = page.locator(".tier-cta-link").evaluate_all(
            "(links) => links.map((link) => link.getAttribute('href'))"
        )
        assert payment_links_after == payment_links_before

    def test_tier_filters_show_cumulative_membership_activities(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=True)

        page.goto(
            f"{django_server}/activities#access-by-tier",
            wait_until="domcontentloaded",
        )

        secondary = page.locator('[data-testid="activities-secondary-nav"]')
        assert secondary.locator('a[href="/events"]').count() == 1
        assert secondary.locator('a[href="/workshops"]').count() == 1

        basic_filter = page.locator('.tier-filter-btn[data-tier="basic"]')
        main_filter = page.locator('.tier-filter-btn[data-tier="main"]')
        premium_filter = page.locator('.tier-filter-btn[data-tier="premium"]')

        basic_filter.click()
        assert basic_filter.get_attribute("aria-pressed") == "true"
        basic_titles = _visible_activity_titles(page)
        assert "Exclusive Substack Content" in basic_titles
        assert "Closed Community Access" not in basic_titles
        assert "Mini-Courses on Specialized Topics" not in basic_titles

        main_filter.click()
        assert main_filter.get_attribute("aria-pressed") == "true"
        assert basic_filter.get_attribute("aria-pressed") == "false"
        main_titles = _visible_activity_titles(page)
        assert "Exclusive Substack Content" in main_titles
        assert "Closed Community Access" in main_titles
        assert "Interactive Group Coding Sessions" in main_titles
        assert "Profile Teardowns" not in main_titles

        premium_filter.click()
        assert premium_filter.get_attribute("aria-pressed") == "true"
        premium_titles = _visible_activity_titles(page)
        assert "Mini-Courses on Specialized Topics" in premium_titles
        assert "Vote on Course Topics" in premium_titles
        assert "Profile Teardowns" in premium_titles
        assert len(premium_titles) == 15

        page.get_by_text("Quick comparison").scroll_into_view_if_needed()
        assert page.get_by_text("Quick comparison").is_visible()

    def test_missing_tier_activity_config_shows_empty_state(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _seed_base(active=False)
            from content.models import SiteConfig

            SiteConfig.objects.filter(key="tiers").delete()

        page.goto(
            f"{django_server}/activities#access-by-tier",
            wait_until="domcontentloaded",
        )

        empty = page.locator('[data-testid="activities-tier-empty"]')
        assert empty.is_visible()
        assert "Membership activities are being updated" in empty.inner_text()
        assert empty.locator('a[href="/pricing"]').count() == 1
        assert page.locator('[data-testid="activity-card"]').count() == 0

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
