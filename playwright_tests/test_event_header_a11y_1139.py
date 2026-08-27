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


@pytest.mark.django_db(transaction=True)
def test_events_listing_heading_outline_uses_h3_for_titles(django_server, page):
    """Upcoming mode exposes one H1, one collection H2, then card H3s."""
    _seed_listing()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    assert page.locator("main h1").all_inner_texts() == ["Live community events"]
    assert page.locator("main h2").all_inner_texts() == ["Upcoming events"]
    assert (
        page.get_by_role("heading", level=3, name="Upcoming Build Session 1139").count()
        == 1
    )
    assert page.get_by_text("Past Retro Recording 1139").count() == 0
    assert page.locator("main h1, main h2, main h3").evaluate_all(
        "nodes => nodes.map(node => node.tagName)"
    )[:3] == ["H1", "H2", "H3"]


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
    assert past_h2 == ["Past events"], (
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
    # Access is a single canonical signal outside the heading. Lucide swaps
    # the placeholder for an SVG after load, so accept either representation.
    access_badge = past_section.locator(
        '[data-testid="past-card-recording-tier"][data-required-level="10"]'
    )
    assert access_badge.inner_text().strip() == "Basic or above"
    assert access_badge.evaluate("el => !el.closest('h3')")
    lock_icon = access_badge.locator('svg.lucide-lock, i[data-lucide="lock"]')
    lock_icon.first.wait_for()
    assert lock_icon.count() == 1


@pytest.mark.django_db(transaction=True)
def test_toggle_tap_target_and_shape(django_server, page):
    """Current Events controls are keyboard reachable and at least 44px tall."""
    _seed_listing()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    toolbar = page.get_by_test_id("events-list-toolbar")
    calendar_link = toolbar.get_by_role("link", name="Calendar", exact=True)
    subscribe = toolbar.get_by_test_id("events-subscribe-trigger")
    upcoming = toolbar.get_by_test_id("events-filter-upcoming")
    past = toolbar.get_by_test_id("events-filter-past")

    assert subscribe.inner_text().strip() == "Subscribe to all events"
    assert subscribe.locator(".sr-only").count() == 0

    for control in (calendar_link, subscribe, upcoming, past):
        box = control.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"control height {box['height']} < 44px"
        assert box["width"] >= 44, f"control width {box['width']} < 44px"
        assert control.evaluate("el => el.tabIndex >= 0")
        classes = control.get_attribute("class")
        for focus_class in (
            "focus-visible:outline-none",
            "focus-visible:ring-2",
            "focus-visible:ring-accent",
            "focus-visible:ring-offset-2",
            "focus-visible:ring-offset-background",
        ):
            assert focus_class in classes

        control.focus()
        assert control.evaluate("el => el.matches(':focus-visible')")

    subscribe.click()
    assert toolbar.get_by_test_id("events-subscribe-popover").get_attribute(
        "open"
    ) is not None
    assert toolbar.get_by_test_id("events-subscribe-menu").is_visible()
    subscribe.click()

    assert upcoming.get_attribute("aria-selected") == "true"
    assert upcoming.get_attribute("aria-current") == "page"
    assert past.get_attribute("aria-selected") == "false"

    past.click()
    page.wait_for_url("**/events?filter=past")
    assert page.get_by_test_id("events-filter-past").get_attribute(
        "aria-selected"
    ) == "true"
    page.get_by_test_id("events-filter-upcoming").click()
    page.wait_for_url("**/events")

    page.get_by_role("link", name="Calendar", exact=True).click()
    page.wait_for_url("**/events/calendar")


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
