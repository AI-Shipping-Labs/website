"""Playwright coverage for the Studio events table redesign (#985)."""

import os
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _reset_event_state():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _event_row_date_label(value, timezone_name):
    local_value = value.astimezone(ZoneInfo(timezone_name))
    return f"{local_value:%a, %b} {local_value.day}, {local_value:%Y, %H:%M} {timezone_name}"


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_studio_events_redesigned_list_and_past_pagination(django_server, browser):
    from django.db import connection

    from events.models import Event, EventSeries

    _reset_event_state()
    staff = _create_staff_user("staff-events-985@test.com")
    staff.preferred_timezone = "Europe/Berlin"
    staff.save(update_fields=["preferred_timezone"])

    now = timezone.now()
    series = EventSeries.objects.create(
        name="Friday Builds",
        start_time=now.time(),
    )
    github_event = Event.objects.create(
        title="GitHub Workshop",
        slug="github-workshop-985",
        start_datetime=now + timedelta(days=5),
        end_datetime=now + timedelta(days=5, hours=1),
        kind="workshop",
        platform="zoom",
        origin="github",
        source_repo="AI-Shipping-Labs/content",
        event_series=series,
    )
    studio_event = Event.objects.create(
        title="Studio Meetup",
        slug="studio-meetup-985",
        start_datetime=now + timedelta(days=6),
        end_datetime=now + timedelta(days=6, hours=1),
        kind="meetup",
        platform="custom",
        origin="studio",
    )
    berlin_start = (
        (now + timedelta(days=7))
        .astimezone(ZoneInfo("UTC"))
        .replace(hour=12, minute=0, second=0, microsecond=0)
    )
    berlin_event = Event.objects.create(
        title="Berlin Noon",
        slug="berlin-noon-985",
        start_datetime=berlin_start,
        end_datetime=berlin_start + timedelta(hours=1),
        kind="standard",
        platform="zoom",
    )
    for index in range(30):
        Event.objects.create(
            title=f"Retro Kickoff {index:02d}",
            slug=f"retro-kickoff-985-{index:02d}",
            start_datetime=now - timedelta(days=index + 1),
            end_datetime=now - timedelta(days=index + 1, hours=-1),
            status="completed",
        )
    connection.close()

    context = _auth_context(browser, "staff-events-985@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    headers = page.locator("thead th").all_inner_texts()
    assert "Status" not in headers
    assert "Origin" not in headers
    assert "Capacity" not in headers
    assert page.locator('[data-testid="event-section-past"]').count() == 0
    assert page.locator('[data-testid="event-past-link"]').is_visible()

    github_row = page.locator(
        f'tr:has(a[href="/studio/events/{github_event.pk}/edit"])'
    ).first
    assert github_row.locator('[data-testid="origin-github-icon"]').get_attribute(
        "aria-label"
    ) == "Synced from GitHub"
    assert github_row.locator('[data-testid="event-kind-icon"]').get_attribute(
        "aria-label"
    ) == "Workshop"
    assert github_row.locator('[data-testid="event-platform-icon"]').get_attribute(
        "aria-label"
    ) == "Zoom"
    assert github_row.locator('[data-testid="event-series-link"]').get_attribute(
        "title"
    ) == "Friday Builds"
    assert "Friday Builds" not in github_row.inner_text()

    studio_row = page.locator(
        f'tr:has(a[href="/studio/events/{studio_event.pk}/edit"])'
    ).first
    assert studio_row.locator('[data-testid="origin-github-icon"]').count() == 0
    assert studio_row.locator('[data-testid="event-kind-icon"]').get_attribute(
        "aria-label"
    ) == "Meetup"
    assert studio_row.locator('[data-testid="event-platform-icon"]').get_attribute(
        "aria-label"
    ) == "Custom URL"
    assert "Meetup" not in studio_row.locator('[data-label="Kind"]').inner_text()
    assert "Custom URL" not in studio_row.locator(
        '[data-label="Platform"]'
    ).inner_text()

    berlin_row = page.locator(
        f'tr:has(a[href="/studio/events/{berlin_event.pk}/edit"])'
    ).first
    assert _event_row_date_label(berlin_start, "Europe/Berlin") in berlin_row.locator(
        '[data-testid="event-row-date"]'
    ).inner_text()

    github_row.locator('[data-testid="event-series-link"]').click()
    page.wait_for_url(f"**/studio/event-series/{series.pk}/")

    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    page.locator('[data-testid="event-past-link"]').click()
    page.wait_for_url("**/studio/events/past/")
    assert page.locator('[data-testid="event-section-past"]').is_visible()
    assert page.locator("tbody tr").count() == 25
    assert page.get_by_text("GitHub Workshop").count() == 0

    page.locator('[data-testid="event-past-list-pager-next"]').click()
    page.wait_for_url("**/studio/events/past/?page=2")
    assert page.locator("tbody tr").count() == 5

    page.goto(f"{django_server}/studio/events/past/?q=Retro+Kickoff+29")
    assert page.locator("tbody tr").count() == 1
    assert page.get_by_text("Retro Kickoff 29").is_visible()
    context.close()
