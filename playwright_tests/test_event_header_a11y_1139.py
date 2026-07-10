"""A11y / consistency coverage for event + workshop headers (issue #1139).

Verifies the three grouped fixes:
- Event-title headings converge to <h3> (no <h2> nested under a section <h2>).
- The List/Calendar view toggle meets the 44px tap target and the
  rounded-full pill shape of the filter control beneath it.
- Event detail and workshop detail h1s cap at sm:text-4xl (no lg:text-5xl).

These touch shared event templates, so the tests also confirm the events
listing, an event detail page, and a workshop detail page still render.

Usage:
    uv run pytest playwright_tests/test_event_header_a11y_1139.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

SECTION_HEADINGS = {"Upcoming", "Past events", "Past event recordings"}


def _clear():
    from django.db import connection

    from content.models import Workshop, WorkshopPage
    from events.models import Event, EventRegistration, EventSeries

    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_event(title, slug, *, start_delta, status, **kwargs):
    from django.db import connection

    from events.models import Event

    now = timezone.now()
    defaults = {
        "title": title,
        "slug": slug,
        "start_datetime": now + start_delta,
        "status": status,
        "published": True,
    }
    defaults.update(kwargs)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


def _seed_listing():
    """One upcoming event, one past recording, one gated past recording."""
    _clear()
    upcoming = _create_event(
        "Upcoming Build Session 1139",
        "upcoming-build-1139",
        start_delta=datetime.timedelta(days=4),
        status="upcoming",
    )
    past = _create_event(
        "Past Retro Recording 1139",
        "past-retro-1139",
        start_delta=-datetime.timedelta(days=3),
        status="completed",
        recording_url="https://youtube.com/watch?v=retro1139",
    )
    gated = _create_event(
        "Gated Members Recording 1139",
        "gated-members-1139",
        start_delta=-datetime.timedelta(days=5),
        status="completed",
        recording_url="https://youtube.com/watch?v=gated1139",
        required_level=10,
    )
    return upcoming, past, gated


def _create_workshop():
    from django.db import connection
    from django.utils.text import slugify

    from content.models import (
        Instructor,
        Workshop,
        WorkshopInstructor,
        WorkshopPage,
    )
    from events.models import Event

    event = Event.objects.create(
        slug="header-workshop-1139-event",
        title="Header Scale Workshop 1139",
        start_datetime=timezone.now() - datetime.timedelta(hours=2),
        status="completed",
        kind="workshop",
        recording_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        published=True,
    )
    workshop = Workshop.objects.create(
        slug="header-workshop-1139",
        title="Header Scale Workshop 1139",
        date=datetime.date(2026, 4, 21),
        status="published",
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=0,
        description="Workshop description body.",
        code_repo_url="https://github.com/example/repo",
        event=event,
    )
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify("Alexey")[:200] or "test-instructor",
        defaults={"name": "Alexey", "status": "published"},
    )
    WorkshopInstructor.objects.get_or_create(
        workshop=workshop, instructor=instructor, defaults={"position": 0}
    )
    WorkshopPage.objects.create(
        workshop=workshop,
        slug="intro",
        title="Introduction",
        sort_order=1,
        body="# Welcome\n\nThis is the intro.",
    )
    connection.close()
    return workshop


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_events_listing_heading_outline_uses_h3_for_titles(django_server, page):
    """Every <h2> on /events is a section header; event titles are <h3>."""
    _seed_listing()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    h2_texts = [
        t.strip() for t in page.locator("main h2").all_inner_texts() if t.strip()
    ]
    assert h2_texts, "expected at least one section <h2>"
    for text in h2_texts:
        assert text in SECTION_HEADINGS, (
            f"<h2> should only be a section header, found event-title-like {text!r}"
        )

    # The upcoming and past event titles render as <h3>, not <h2>.
    assert (
        page.get_by_role("heading", level=3, name="Upcoming Build Session 1139").count()
        == 1
    )
    assert (
        page.get_by_role("heading", level=3, name="Past Retro Recording 1139").count()
        == 1
    )
    # No event title leaked into an <h2>.
    assert (
        page.get_by_role("heading", level=2, name="Upcoming Build Session 1139").count()
        == 0
    )
    assert (
        page.get_by_role("heading", level=2, name="Past Retro Recording 1139").count()
        == 0
    )


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_past_recordings_view_single_heading_level_and_lock(django_server, page):
    """/events?filter=past keeps one section <h2>; card titles are <h3>."""
    _seed_listing()

    page.goto(
        f"{django_server}/events?filter=past", wait_until="domcontentloaded"
    )

    past_section = page.locator('[data-testid="events-past-section"]')
    past_h2 = [
        t.strip() for t in past_section.locator("h2").all_inner_texts() if t.strip()
    ]
    assert past_h2 == ["Past event recordings"], (
        f"past section should have exactly the section <h2>, got {past_h2!r}"
    )

    # Rich recording card titles are <h3>.
    assert (
        past_section.get_by_role(
            "heading", level=3, name="Past Retro Recording 1139"
        ).count()
        == 1
    )
    gated_title = past_section.get_by_role(
        "heading", level=3, name="Gated Members Recording 1139"
    )
    assert gated_title.count() == 1
    # The gated recording still shows its lock indicator alongside the title.
    # lucide.createIcons() swaps the <i data-lucide="lock"> placeholder for an
    # <svg class="lucide lucide-lock">, so wait for the rendered icon.
    lock_icon = gated_title.locator('svg.lucide-lock, i[data-lucide="lock"]')
    lock_icon.first.wait_for()
    assert lock_icon.count() == 1


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_toggle_tap_target_and_shape(django_server, page):
    """List/Calendar toggle is >=44px tall, rounded-full, and navigates."""
    _seed_listing()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    toggle_row = page.locator('[data-testid="events-view-toggle-row"]')
    list_link = toggle_row.get_by_role("link", name="List", exact=True)
    calendar_link = toggle_row.get_by_role("link", name="Calendar", exact=True)

    for link in (list_link, calendar_link):
        box = link.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"toggle height {box['height']} < 44px"
        classes = link.get_attribute("class")
        assert "rounded-full" in classes
        assert "min-h-[44px]" in classes

    # Active/inactive state colors are preserved.
    assert "bg-accent" in list_link.get_attribute("class")
    assert "bg-secondary" in calendar_link.get_attribute("class")

    calendar_link.click()
    page.wait_for_url("**/events/calendar")

    page.get_by_role("link", name="List", exact=True).first.click()
    page.wait_for_url("**/events")


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_event_detail_h1_caps_at_sm_text_4xl(django_server, page):
    """Event detail h1 renders and its class has no lg:text-5xl."""
    _, past, _ = _seed_listing()

    from events.models import Event

    url = Event.objects.get(pk=past.pk).get_absolute_url()
    page.goto(f"{django_server}{url}", wait_until="domcontentloaded")

    h1 = page.locator("main h1").first
    assert h1.inner_text().strip() == "Past Retro Recording 1139"
    classes = h1.get_attribute("class")
    assert "sm:text-4xl" in classes
    assert "lg:text-5xl" not in classes


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_workshop_detail_h1_caps_at_sm_text_4xl(django_server, browser):
    """Workshop detail h1 (data-testid) has no lg:text-5xl and still renders."""
    _clear()
    _create_workshop()
    _create_user("main@test.com", tier_slug="main")

    ctx = _auth_context(browser, "main@test.com")
    page = ctx.new_page()
    page.goto(
        f"{django_server}/workshops/header-workshop-1139",
        wait_until="domcontentloaded",
    )

    title = page.locator('[data-testid="workshop-title"]')
    assert title.count() == 1
    classes = title.get_attribute("class")
    assert "sm:text-4xl" in classes
    assert "lg:text-5xl" not in classes
    # Tier/access badge still renders alongside the title.
    assert page.locator(
        '[data-testid="workshop-free-badge"], [data-testid="workshop-tier-badge"]'
    ).count() >= 1
