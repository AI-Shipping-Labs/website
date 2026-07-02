"""Public events pagination coverage for #1039."""

import os
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.utils import timezone

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

WEEKDAY_DATE_PATTERN = re.compile(
    r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun), [A-Z][a-z]{2} \d{1,2}, \d{4}"
)


def _clear_events():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_event(title, slug, *, start_delta, end_delta=None, **kwargs):
    from django.db import connection

    from events.models import Event

    now = timezone.now()
    start_datetime = now + start_delta
    end_datetime = now + (
        end_delta if end_delta is not None else start_delta + timedelta(hours=1)
    )
    defaults = {
        "title": title,
        "slug": slug,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "status": "upcoming" if start_delta.total_seconds() > 0 else "completed",
    }
    defaults.update(kwargs)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


def _seed_default_history():
    _clear_events()
    _create_event(
        "Upcoming Visible 1039",
        "upcoming-visible-1039",
        start_delta=timedelta(days=4),
    )
    for index in range(25):
        _create_event(
            f"History Event 1039 {index:02d}",
            f"history-event-1039-{index:02d}",
            start_delta=-timedelta(days=index + 1),
            end_delta=-timedelta(days=index + 1, hours=-1),
        )


def _seed_recording_history():
    _clear_events()
    for index in range(25):
        _create_event(
            f"Tagged AI Recording 1039 {index:02d}",
            f"tagged-ai-recording-1039-{index:02d}",
            start_delta=-timedelta(days=index + 1),
            end_delta=-timedelta(days=index + 1, hours=-1),
            recording_url="https://youtube.com/watch?v=ai1039",
            published=True,
            tags=["ai", "agents"],
        )
    _create_event(
        "Python Only Recording 1039",
        "python-only-recording-1039",
        start_delta=-timedelta(days=40),
        end_delta=-timedelta(days=40, hours=-1),
        recording_url="https://youtube.com/watch?v=py1039",
        published=True,
        tags=["python"],
    )


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_default_events_pages_past_without_hiding_upcoming(django_server, page):
    _seed_default_history()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    assert page.locator('[data-testid="events-upcoming-section"]').is_visible()
    assert page.get_by_text("Upcoming Visible 1039").is_visible()
    assert page.get_by_text("History Event 1039 00").is_visible()
    assert page.get_by_text("History Event 1039 20").count() == 0
    assert page.locator('[data-testid="events-past-pagination"]').is_visible()
    assert page.get_by_text("Page 1 of 2").is_visible()
    assert WEEKDAY_DATE_PATTERN.search(
        page.locator('[data-testid="events-upcoming-section"]').inner_text()
    )
    assert WEEKDAY_DATE_PATTERN.search(
        page.locator('[data-testid="events-past-section"]').inner_text()
    )

    page.locator('[data-testid="events-past-pagination-next"]').click()
    page.wait_for_url("**/events?page=2")

    assert page.locator('[data-testid="events-upcoming-section"]').is_visible()
    assert page.get_by_text("Upcoming Visible 1039").is_visible()
    assert page.get_by_text("History Event 1039 20").is_visible()
    assert page.get_by_text("History Event 1039 00").count() == 0
    assert page.get_by_text("Page 2 of 2").is_visible()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_past_recording_pager_preserves_repeated_tags_and_clamps_pages(
    django_server, page
):
    _seed_recording_history()

    page.goto(
        f"{django_server}/events?filter=past&tag=ai&tag=agents",
        wait_until="domcontentloaded",
    )

    assert page.get_by_text("Tagged AI Recording 1039 00").is_visible()
    assert page.get_by_text("Python Only Recording 1039").count() == 0

    page.locator('[data-testid="events-past-pagination-next"]').click()
    page.wait_for_load_state("domcontentloaded")
    params = parse_qs(urlparse(page.url).query)
    assert params["filter"] == ["past"]
    assert params["tag"] == ["ai", "agents"]
    assert params["page"] == ["2"]
    assert page.get_by_text("Tagged AI Recording 1039 20").is_visible()
    assert page.get_by_text("Python Only Recording 1039").count() == 0
    assert WEEKDAY_DATE_PATTERN.search(
        page.locator('[data-testid="events-past-section"]').inner_text()
    )

    page.goto(
        f"{django_server}/events?filter=past&tag=ai&page=9999",
        wait_until="domcontentloaded",
    )
    assert page.get_by_text("Page 2 of 2").is_visible()

    page.goto(f"{django_server}/events?page=not-a-number", wait_until="domcontentloaded")
    assert page.get_by_text("Page 1 of 2").is_visible()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_upcoming_filter_keeps_series_grouping_and_hides_history(django_server, page):
    from django.db import connection

    from events.models import EventSeries

    _clear_events()
    series = EventSeries.objects.create(
        name="Weekly Builds 1039",
        start_time=timezone.now().time(),
    )
    _create_event(
        "Series Session 1039 A",
        "series-session-1039-a",
        start_delta=timedelta(days=2),
        event_series=series,
    )
    _create_event(
        "Series Session 1039 B",
        "series-session-1039-b",
        start_delta=timedelta(days=9),
        event_series=series,
    )
    _create_event(
        "Past Hidden 1039",
        "past-hidden-1039",
        start_delta=-timedelta(days=2),
        end_delta=-timedelta(days=2, hours=-1),
    )
    connection.close()

    page.goto(f"{django_server}/events?filter=upcoming", wait_until="domcontentloaded")

    assert page.locator('[data-testid="events-upcoming-section"]').is_visible()
    assert page.locator('[data-testid="event-series-card"]').is_visible()
    assert WEEKDAY_DATE_PATTERN.search(
        page.locator('[data-testid="series-card-date"]').inner_text()
    )
    series_meta = page.locator('[data-testid="series-card-meta"]').inner_text()
    assert len(WEEKDAY_DATE_PATTERN.findall(series_meta)) == 2
    assert page.locator('[data-testid="events-past-section"]').count() == 0
    assert page.locator('[data-testid="events-past-pagination"]').count() == 0
    assert page.get_by_text("Past Hidden 1039").count() == 0


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
def test_mobile_default_events_pager_has_no_horizontal_overflow(django_server, page):
    _seed_default_history()
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    assert page.locator('[data-testid="events-past-pagination"]').is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= page.evaluate(
        "document.documentElement.clientWidth"
    )
    assert page.get_by_text("Page 1 of 2").is_visible()
