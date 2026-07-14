import os
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_site_config_tiers,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.local_only,
    pytest.mark.core,
]


def _email(prefix):
    return f"{prefix}-{uuid4().hex[:8]}@example.com"


def _seed_homepage_tiers(django_db_blocker):
    with django_db_blocker.unblock():
        from allauth.socialaccount.models import SocialApp

        SocialApp.objects.all().delete()
        ensure_tiers()
        ensure_site_config_tiers()


def test_homepage_free_cta_hands_off_to_registration_and_creates_account(
    django_server, page, django_db_blocker
):
    _seed_homepage_tiers(django_db_blocker)
    email = _email("home-1162")

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    free_card = page.locator('[data-tier-card="free"]')
    free_card.scroll_into_view_if_needed()
    expect(free_card).to_contain_text("€0")
    expect(free_card).to_contain_text("/forever")
    expect(free_card.locator("form")).to_have_count(0)
    expect(free_card.locator('[data-testid="inline-register-card"]')).to_have_count(0)
    expect(free_card.locator("[data-auth-oauth-providers]")).to_have_count(0)

    join_free = free_card.get_by_role("link", name="Join free", exact=True)
    expect(join_free).to_have_attribute("href", "/#join-free")
    assert join_free.evaluate("el => el.getBoundingClientRect().height") >= 44
    join_free.focus()
    assert join_free.evaluate(
        "el => getComputedStyle(el).outlineStyle !== 'none'"
    )
    join_free.click()

    page.wait_for_url(f"{django_server}/#join-free")
    join_section = page.locator("#join-free")
    expect(join_section).to_be_visible()
    expect(join_section.locator("#register-form")).to_be_visible()
    assert join_section.evaluate("el => !el.closest('[data-tier-carousel]')")
    expect(page.get_by_text("By creating an account")).to_be_visible()
    page.locator("#register-email").fill(email)
    page.locator("#register-password").fill("Password123!")
    page.locator("#register-password-confirm").fill("Password123!")
    page.locator("#register-submit").click()

    page.wait_for_url(f"{django_server}/", timeout=10000)
    expect(page.locator('[data-testid="account-menu-trigger"]')).to_be_visible()
    expect(page.locator('[data-testid="header-join-free-link"]')).to_have_count(0)


def test_homepage_free_handoff_keeps_oauth_on_dedicated_page_only(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        ensure_site_config_tiers()
        SocialApp.objects.all().delete()
        app = SocialApp.objects.create(
            provider="google",
            name="Google",
            client_id="google-1222",
            secret="secret-1222",
        )
        app.sites.add(Site.objects.get_current())

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    free_card = page.locator('[data-tier-card="free"]')
    expect(free_card.get_by_role("link", name="Sign up with Google")).to_have_count(0)
    free_card.get_by_role("link", name="Join free", exact=True).click()
    page.wait_for_url(f"{django_server}/#join-free")
    join_section = page.locator("#join-free")
    expect(join_section.get_by_role("link", name="Sign up with Google")).to_be_visible()
    expect(page.get_by_role("link", name="Terms of Service").first).to_be_visible()
    expect(page.get_by_role("link", name="Privacy Policy").first).to_be_visible()

    expect(free_card.get_by_role("link", name="Sign up with Google")).to_have_count(0)


def test_homepage_billing_toggle_changes_paid_links_but_not_free(
    django_server, page, django_db_blocker
):
    _seed_homepage_tiers(django_db_blocker)
    page.goto(f"{django_server}/", wait_until="domcontentloaded")

    free_cta = page.locator('[data-testid="home-free-tier-cta"]')
    basic_cta = page.locator('[data-tier-card="basic"] .tier-cta-link')
    monthly_basic = basic_cta.get_attribute("data-link-monthly")
    annual_basic = basic_cta.get_attribute("data-link-annual")
    expect(basic_cta).to_have_attribute("href", monthly_basic)
    expect(free_cta).to_have_attribute("href", "/#join-free")

    page.locator("#billing-toggle").click()
    expect(basic_cta).to_have_attribute("href", annual_basic)
    expect(free_cta).to_have_attribute("href", "/#join-free")
    expect(page.locator('[data-tier-card="main"]')).to_contain_text("Most Popular")

    page.locator("#billing-toggle").click()
    expect(basic_cta).to_have_attribute("href", monthly_basic)
    expect(free_cta).to_have_attribute("href", "/#join-free")


def test_homepage_mobile_free_card_is_clean_and_all_tiers_reachable(
    django_server, page, django_db_blocker
):
    _seed_homepage_tiers(django_db_blocker)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    page.wait_for_load_state("load")

    carousel = page.locator('[data-testid="home-tier-carousel"]')
    expect(carousel.locator('[data-testid="home-tier-card"]')).to_have_count(4)
    assert carousel.evaluate("el => el.scrollWidth > el.clientWidth")
    assert page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    ) <= 1

    main = carousel.locator('[data-tier-card="main"]')
    main_delta = page.wait_for_function(
        """carousel => {
          const card = carousel.querySelector('[data-tier-card="main"]');
          const outer = carousel.getBoundingClientRect();
          const inner = card.getBoundingClientRect();
          const delta = Math.abs(
            (inner.left + inner.width / 2) - (outer.left + outer.width / 2)
          );
          return delta < 60 ? delta : false;
        }""",
        arg=carousel.element_handle(),
    )
    assert main_delta.json_value() < 60
    expect(main).to_contain_text("Most Popular")

    free_card = carousel.locator('[data-tier-card="free"]')
    free_card.scroll_into_view_if_needed()
    expect(free_card.locator("form")).to_have_count(0)
    expect(free_card.locator('[data-testid="inline-register-card"]')).to_have_count(0)
    join_free = free_card.get_by_role("link", name="Join free", exact=True)
    expect(join_free).to_be_visible()
    assert join_free.evaluate("el => el.getBoundingClientRect().height") >= 44
    join_free.click()
    page.wait_for_url(f"{django_server}/#join-free")
    join_section = page.locator("#join-free")
    expect(join_section).to_be_visible()
    expect(join_section.locator("#register-form")).to_be_visible()
    assert page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    ) <= 1


def test_homepage_sprint_story_links_to_active_sprint_detail(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        from plans.models import Sprint

        ensure_site_config_tiers()
        Sprint.objects.all().delete()
        sprint = Sprint.objects.create(
            name="July Sprint",
            slug="july-sprint-1162",
            start_date=timezone.localdate() - timedelta(days=7),
            duration_weeks=4,
            status="active",
            min_tier_level=20,
        )

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    section = page.locator('[data-testid="home-sprint-story-section"]')
    section.scroll_into_view_if_needed()
    expect(section).to_contain_text("Plan -> Sprint -> Ship")
    expect(section.locator('[data-testid="home-featured-sprint-name"]')).to_contain_text(
        "July Sprint"
    )
    section.locator('[data-testid="home-featured-sprint-link"]').click()
    page.wait_for_url(f"{django_server}{sprint.get_absolute_url()}", timeout=10000)
    expect(page.locator("main")).to_contain_text("July Sprint")


def test_homepage_upcoming_event_card_navigates_to_event_detail(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        from events.models import Event

        ensure_site_config_tiers()
        Event.objects.all().delete()
        event = Event.objects.create(
            title="Open Office Hours",
            slug="open-office-hours-1162",
            description="A live open session for guests.",
            start_datetime=timezone.now() + timedelta(days=2),
            end_datetime=timezone.now() + timedelta(days=2, hours=1),
            status="upcoming",
            published=True,
        )

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    section = page.locator('[data-testid="home-upcoming-events-section"]')
    section.scroll_into_view_if_needed()
    card = section.locator('[data-testid="home-upcoming-event-card"]').first
    expect(card).to_contain_text("Open Office Hours")
    card.locator('[data-testid="event-card-link"]').click()
    page.wait_for_url(f"{django_server}{event.get_absolute_url()}", timeout=10000)
    expect(page.locator("main")).to_contain_text("Open Office Hours")


def test_homepage_upcoming_events_empty_state_stays_discoverable(
    django_server, page, django_db_blocker
):
    with django_db_blocker.unblock():
        from events.models import Event

        ensure_site_config_tiers()
        Event.objects.all().delete()
        Event.objects.create(
            title="Draft Session",
            slug="draft-session-1162",
            description="Should not render on the homepage.",
            start_datetime=timezone.now() + timedelta(days=1),
            end_datetime=timezone.now() + timedelta(days=1, hours=1),
            status="draft",
            published=True,
        )
        Event.objects.create(
            title="Stale Session",
            slug="stale-session-1162",
            description="Already ended.",
            start_datetime=timezone.now() - timedelta(hours=2),
            end_datetime=timezone.now() - timedelta(minutes=1),
            status="upcoming",
            published=True,
        )

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    expect(page.locator('[data-testid="home-upcoming-events-section"]')).to_have_count(0)
    expect(page.locator('[data-testid="home-upcoming-events-empty"]')).to_have_count(0)


def test_authenticated_member_keeps_dashboard_path(
    django_server, browser, django_db_blocker
):
    with django_db_blocker.unblock():
        ensure_site_config_tiers()
        create_user("dashboard-1162@example.com", tier_slug="main")

    context = auth_context(browser, "dashboard-1162@example.com")
    page = context.new_page()
    try:
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        assert page.locator('#join-free').count() == 0
        assert page.locator('#register-form').count() == 0
        assert page.locator('[data-testid="home-upcoming-events-section"]').count() == 0
        assert page.locator('[data-testid="home-sprint-story-section"]').count() == 0
        expect(
            page.get_by_role("heading", name="Recent content", exact=True)
        ).to_be_visible()
    finally:
        context.close()
