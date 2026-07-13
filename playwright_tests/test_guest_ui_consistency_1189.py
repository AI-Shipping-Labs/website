"""Focused guest UI consistency checks for issue #1189."""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import expect

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _reset_guest_content():
    from django.db import connection

    from content.models import Download, Project, Workshop
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    Workshop.objects.all().delete()
    Download.objects.all().delete()
    Project.objects.all().delete()
    connection.close()


def _create_event(title, slug, *, required_level=0, **kwargs):
    from django.db import connection

    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        description=f"{title} description.",
        start_datetime=timezone.now() + datetime.timedelta(days=10),
        status="upcoming",
        required_level=required_level,
        **kwargs,
    )
    connection.close()
    return event


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_guest_events_use_canonical_tier_badges_and_gated_card(
    django_server, page,
):
    _reset_guest_content()
    _create_event("Free 1189 Event", "free-1189-event")
    _create_event("Basic 1189 Event", "basic-1189-event", required_level=10)
    main_event = _create_event(
        "Main 1189 Event", "main-1189-event", required_level=20,
    )
    _create_event(
        "Premium 1189 Event", "premium-1189-event", required_level=30,
    )
    _create_event(
        "External 1189 Event",
        "external-1189-event",
        external_host="Maven",
        zoom_join_url="https://example.com/maven-event",
    )

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")
    body = page.locator("body")
    expect(body).to_contain_text("Basic or above")
    expect(body).to_contain_text("Main or above")
    expect(body).to_contain_text("Premium")
    expect(body).not_to_contain_text("Membership:")
    expect(body).not_to_contain_text("Premium or above")
    expect(page.get_by_test_id("event-card-external-badge")).to_contain_text(
        "Hosted on Maven",
    )

    free_card = page.locator('article:has-text("Free 1189 Event")')
    expect(free_card.locator('[data-lucide="lock"]')).to_have_count(0)

    page.goto(
        f"{django_server}{main_event.get_absolute_url()}",
        wait_until="domcontentloaded",
    )
    expect(page.get_by_test_id("event-anonymous-email-form")).to_have_count(0)
    expect(page.get_by_test_id("event-anonymous-cta")).to_be_visible()
    expect(page.get_by_test_id("gated-required-tier")).to_contain_text(
        "Main or above required",
    )
    expect(page.get_by_test_id("event-anonymous-pricing-cta")).to_have_attribute(
        "href", "/pricing",
    )
    expect(page.get_by_test_id("event-anonymous-signin-cta")).to_have_attribute(
        "href", f"/accounts/login/?next={main_event.get_absolute_url()}",
    )


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_guest_download_empty_states_use_shared_component(django_server, page):
    from django.db import connection

    from content.models import Download

    _reset_guest_content()
    Download.objects.create(
        title="Visible 1189 Download",
        slug="visible-1189-download",
        file_url="https://example.com/visible.pdf",
        file_type="pdf",
        tags=["ai"],
        published=True,
    )
    connection.close()

    page.goto(
        f"{django_server}/downloads?tag=missing-topic",
        wait_until="domcontentloaded",
    )
    empty = page.get_by_test_id("member-empty-state")
    expect(empty).to_be_visible()
    expect(empty).to_have_attribute("data-empty-kind", "filter")
    expect(empty).to_contain_text("No downloads found")
    page.get_by_role("link", name="View all downloads").click()
    page.wait_for_url(f"{django_server}/downloads")
    expect(page.get_by_test_id("download-card")).to_have_count(1)

    Download.objects.all().delete()
    connection.close()
    page.goto(f"{django_server}/downloads", wait_until="domcontentloaded")
    empty = page.get_by_test_id("member-empty-state")
    expect(empty).to_be_visible()
    expect(empty).to_have_attribute("data-empty-kind", "fresh")
    expect(empty).to_contain_text("No downloads yet")
    expect(page.get_by_test_id("download-card")).to_have_count(0)


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_home_and_projects_share_project_card_language(django_server, page):
    from django.db import connection

    from content.models import Project

    _reset_guest_content()
    project = Project.objects.create(
        title="Shared 1189 Project",
        slug="shared-1189-project",
        description="A shared project card fixture.",
        date=datetime.date(2026, 7, 1),
        author="AI Shipping Labs",
        difficulty="intermediate",
        required_level=10,
        tags=["agents", "rag", "evaluation", "deployment", "python"],
        published=True,
    )
    free_project = Project.objects.create(
        title="Free Shared 1226 Project",
        slug="free-shared-1226-project",
        description="A free shared project card fixture.",
        date=datetime.date(2026, 7, 2),
        required_level=0,
        published=True,
    )
    connection.close()

    page.goto(f"{django_server}/", wait_until="domcontentloaded")
    home_card = page.get_by_test_id("home-project-card").filter(
        has_text=project.title,
    )
    expect(home_card).to_have_count(1)
    home_text = home_card.inner_text()
    assert "intermediate" in home_text
    assert "Basic or above" in home_text
    assert "Official" in home_text
    assert "+2" in home_text
    home_href = home_card.locator("a").first.get_attribute("href")
    home_paid_badge = home_card.get_by_test_id("project-tier-badge")
    expect(home_paid_badge).to_have_attribute("data-required-level", "10")
    expect(home_paid_badge.locator("svg.lucide-lock")).to_have_count(1)
    home_free_card = page.get_by_test_id("home-project-card").filter(
        has_text=free_project.title,
    )
    home_free_badge = home_free_card.get_by_test_id("project-free-badge")
    expect(home_free_badge).to_contain_text("Free")
    expect(home_free_badge).to_have_attribute("data-required-level", "0")
    expect(home_free_badge.locator("svg.lucide-badge-check")).to_have_count(1)

    page.goto(f"{django_server}/projects", wait_until="domcontentloaded")
    listing_card = page.locator('article:has-text("Shared 1189 Project")')
    expect(listing_card).to_have_count(1)
    listing_text = listing_card.inner_text()
    assert "intermediate" in listing_text
    assert "Basic or above" in listing_text
    assert "Official" in listing_text
    assert "+2" in listing_text
    assert listing_card.locator("a").first.get_attribute("href") == home_href
    assert "focus-visible:ring-2" in (
        listing_card.locator("a").first.get_attribute("class") or ""
    )
    listing_paid_badge = listing_card.get_by_test_id("project-tier-badge")
    expect(listing_paid_badge).to_have_attribute("data-required-level", "10")
    free_listing_card = page.locator(
        'article:has-text("Free Shared 1226 Project")'
    )
    listing_free_badge = free_listing_card.get_by_test_id("project-free-badge")
    expect(listing_free_badge).to_contain_text("Free")
    expect(listing_free_badge).to_have_attribute("data-required-level", "0")
