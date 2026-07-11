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


def test_homepage_free_card_registers_and_redirects_home_authenticated(
    django_server, page, django_db_blocker
):
    _seed_homepage_tiers(django_db_blocker)
    email = _email("home-1162")

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    free_card = page.locator('[data-tier-card="free"]')
    free_card.scroll_into_view_if_needed()
    expect(free_card.locator('[data-testid="inline-register-card"]')).to_be_visible()
    free_card.locator("#register-email").fill(email)
    free_card.locator("#register-password").fill("Password123!")
    free_card.locator("#register-password-confirm").fill("Password123!")
    free_card.locator("#register-submit").click()

    page.wait_for_url(f"{django_server}/", timeout=10000)
    expect(page.locator('[data-testid="account-menu-trigger"]')).to_be_visible()
    expect(page.locator('[data-testid="header-join-free-link"]')).to_have_count(0)


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
    section = page.locator('[data-testid="home-upcoming-events-section"]')
    section.scroll_into_view_if_needed()
    empty = section.locator('[data-testid="home-upcoming-events-empty"]')
    expect(empty).to_be_visible()
    expect(empty).to_contain_text("No live events are scheduled right now")
    empty.get_by_role("link", name="Browse events").click()
    page.wait_for_url(f"{django_server}/events", timeout=10000)


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
        assert page.locator('[data-testid="home-free-tier-register"]').count() == 0
        assert page.locator('[data-testid="home-upcoming-events-section"]').count() == 0
        assert page.locator('[data-testid="home-sprint-story-section"]').count() == 0
        expect(
            page.get_by_role("heading", name="Recent content", exact=True)
        ).to_be_visible()
    finally:
        context.close()
