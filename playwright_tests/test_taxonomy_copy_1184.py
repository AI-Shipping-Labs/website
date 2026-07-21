"""Playwright taxonomy journeys for issue #1184."""

import datetime
import os
import re

import pytest
from django.utils import timezone
from playwright.sync_api import expect

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.local_only]


def _clear_taxonomy_fixtures():
    from django.db import connection

    from content.models import CuratedLink, SiteConfig, Workshop
    from events.models import Event, EventRegistration

    Workshop.objects.filter(slug__contains="taxonomy-1184").delete()
    EventRegistration.objects.filter(
        event__slug__contains="taxonomy-1184",
    ).delete()
    Event.objects.filter(slug__contains="taxonomy-1184").delete()
    CuratedLink.objects.filter(item_id__contains="taxonomy-1184").delete()
    SiteConfig.objects.filter(key="tiers").delete()
    connection.close()


def _seed_taxonomy_fixtures():
    from django.db import connection

    from content.models import CuratedLink, SiteConfig, Workshop
    from events.models import Event

    _clear_taxonomy_fixtures()

    SiteConfig.objects.create(
        key="tiers",
        data=[
            {
                "name": "Basic",
                "stripe_key": "basic",
                "activities": [
                    {
                        "icon": "book-open",
                        "title": "Self-serve learning 1184",
                        "description": "Curated content and guided practice.",
                        "features": [],
                    }
                ],
            },
            {
                "name": "Main",
                "stripe_key": "main",
                "activities": [
                    {
                        "icon": "users",
                        "title": "Community accountability 1184",
                        "description": "Sprints, events, and Slack participation.",
                        "features": [],
                    }
                ],
            },
            {
                "name": "Premium",
                "stripe_key": "premium",
                "activities": [
                    {
                        "icon": "star",
                        "title": "Career feedback 1184",
                        "description": "Feedback and premium learning paths.",
                        "features": [],
                    }
                ],
            },
        ],
    )

    now = timezone.now()
    Event.objects.create(
        title="Live Taxonomy Session 1184",
        slug="live-taxonomy-1184",
        start_datetime=now + datetime.timedelta(days=5),
        end_datetime=now + datetime.timedelta(days=5, hours=1),
        status="upcoming",
        published=True,
    )
    Event.objects.create(
        title="Standalone Taxonomy Recording 1184",
        slug="standalone-taxonomy-1184",
        start_datetime=now - datetime.timedelta(days=5),
        end_datetime=now - datetime.timedelta(days=5, hours=-1),
        status="completed",
        published=True,
        recording_url="https://video.example.test/standalone-1184",
        tags=["taxonomy"],
    )
    workshop_event = Event.objects.create(
        title="Linked Taxonomy Workshop 1184",
        slug="linked-taxonomy-1184-event",
        start_datetime=now - datetime.timedelta(days=6),
        end_datetime=now - datetime.timedelta(days=6, hours=-1),
        status="completed",
        published=True,
        kind="workshop",
        recording_url="https://video.example.test/workshop-1184",
        tags=["workshop"],
    )
    Workshop.objects.create(
        slug="linked-taxonomy-1184-workshop",
        title="Linked Taxonomy Workshop 1184",
        date=datetime.date(2026, 7, 1),
        status="published",
        description="Hands-on workshop artifact for taxonomy.",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=20,
        event=workshop_event,
        core_tools=["Python"],
        tags=["workshop"],
    )
    CuratedLink.objects.create(
        item_id="taxonomy-1184-curated-link",
        title="Taxonomy Reference 1184",
        description="A focused external reference.",
        url="https://example.com/taxonomy-1184",
        category="articles",
        published=True,
    )
    connection.close()


@pytest.mark.core
def test_desktop_visitor_taxonomy_journey(django_server, page):
    _seed_taxonomy_fixtures()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")
    expect(
        page.get_by_role("heading", name="Live community events")
    ).to_be_visible()
    past_filter = page.locator('[data-testid="events-filter-past"]')
    expect(past_filter).to_contain_text("Past event recordings")
    expect(page.locator("main")).not_to_contain_text("Community Events & Workshops")

    past_filter.click()
    page.wait_for_load_state("domcontentloaded")
    assert "filter=past" in page.url
    expect(
        page.get_by_role("heading", name="Past event recordings")
    ).to_be_visible()
    expect(page.locator("main")).to_contain_text("Catch up on live sessions you missed")
    standalone_card = page.locator('[data-testid="past-card-event-link"]')
    workshop_card = page.locator('[data-testid="past-card-workshop-link"]')
    expect(standalone_card).to_have_attribute(
        "href",
        re.compile(r"/events/\d+/standalone-taxonomy-1184"),
    )
    expect(workshop_card).to_have_attribute(
        "href",
        "/workshops/linked-taxonomy-1184-workshop",
    )
    standalone_badge = standalone_card.get_by_test_id("past-card-recording-tier")
    expect(standalone_badge).to_have_attribute("data-required-level", "0")
    expect(standalone_badge).to_contain_text("Free")
    expect(
        standalone_badge.locator(
            'svg.lucide-badge-check, i[data-lucide="badge-check"]'
        )
    ).to_have_count(1)
    workshop_badge = workshop_card.get_by_test_id("past-card-recording-tier")
    expect(workshop_badge).to_have_attribute("data-required-level", "20")
    expect(workshop_badge).to_contain_text("Main or above")
    expect(
        workshop_badge.locator('svg.lucide-lock, i[data-lucide="lock"]')
    ).to_have_count(1)

    page.goto(f"{django_server}/workshops", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Hands-on AI workshops")).to_be_visible()
    expect(page.locator("main")).to_contain_text(
        "Workshops start as live sessions on the events calendar"
    )

    page.goto(f"{django_server}/resources", wait_until="domcontentloaded")
    expect(
        page.get_by_role("heading", name="Curated links for AI builders")
    ).to_be_visible()
    expect(page.locator("main")).not_to_contain_text("community activities")

    page.goto(f"{django_server}/activities#access-by-tier", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Membership benefits by tier")).to_be_visible()
    anchor_nav = page.locator('[data-testid="activities-anchor-nav"]')
    for label in ["Community sprints", "Live events", "Workshops"]:
        expect(anchor_nav.get_by_role("link", name=label)).to_be_visible()
    expect(page.get_by_test_id("activities-pricing-cta")).to_be_visible()

    page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Community Sprints")).to_be_visible()


@pytest.mark.core
def test_mobile_taxonomy_navigation_and_past_recordings(django_server, browser):
    _seed_taxonomy_fixtures()
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    page.locator("#mobile-menu-btn").click()
    page.wait_for_selector("#mobile-menu:not(.hidden)", timeout=2000)
    page.locator("#mobile-community-toggle").click()
    page.locator("#mobile-resources-toggle").click()

    community_menu = page.locator('[data-testid="mobile-nav-community-menu"]')
    resources_menu = page.locator('[data-testid="mobile-nav-resources-menu"]')
    for label in [
        "Membership",
        "Activities",
        "Community Sprints",
        "Events",
        "Past Recordings",
    ]:
        expect(community_menu.get_by_text(label, exact=True)).to_be_visible()
    expect(community_menu.get_by_text("Overview", exact=True)).to_have_count(0)
    for label in ["Blog", "Courses", "Workshops", "Curated Links"]:
        expect(resources_menu.get_by_text(label, exact=True)).to_be_visible()

    page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
    expect(
        page.get_by_role("heading", name="Past event recordings")
    ).to_be_visible()
    expect(page.locator("main")).to_contain_text("Catch up on live sessions you missed")

    page.goto(f"{django_server}/resources", wait_until="domcontentloaded")
    expect(
        page.get_by_role("heading", name="Curated links for AI builders")
    ).to_be_visible()

    context.close()
