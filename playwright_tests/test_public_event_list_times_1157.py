"""Public event-list card time coverage for issue #1157."""

import os
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from events.services.timeline import event_local_datetime, format_time_label
from playwright_tests.conftest import VIEWPORT
from playwright_tests.conftest import create_session_for_user as _create_session

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

def _berlin_summer_start():
    now = timezone.now().date()
    year = now.year
    target = date(year, 7, 21)
    if now >= target:
        year += 1
    return datetime(year, 7, 21, 16, 0, tzinfo=UTC)


def _future_start(*, days=12, hour=16):
    value = timezone.now() + timedelta(days=days)
    return value.astimezone(UTC).replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )


def _past_start(*, days=12, hour=16):
    value = timezone.now() - timedelta(days=days)
    return value.astimezone(UTC).replace(
        hour=hour,
        minute=0,
        second=0,
        microsecond=0,
    )


def _clear_events_and_users():
    from django.db import connection

    from accounts.models import User
    from events.models import (
        Event,
        EventRegistration,
        EventSeries,
        SeriesRegistration,
    )

    EventRegistration.objects.all().delete()
    SeriesRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    User.objects.filter(email__endswith="@listtime.test").delete()
    connection.close()


def _close_connection():
    from django.db import connection

    connection.close()


def _create_event(title, slug, *, start_datetime, **overrides):
    from events.models import Event

    defaults = {
        "title": title,
        "slug": slug,
        "start_datetime": start_datetime,
        "end_datetime": start_datetime + timedelta(hours=1),
        "status": "upcoming",
        "timezone": "Europe/Berlin",
        "location": "Zoom",
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _create_series(name, slug):
    from events.models import EventSeries

    return EventSeries.objects.create(
        name=name,
        slug=slug,
        start_time=time(18, 0),
    )


def _create_user(email, *, preferred_timezone):
    from accounts.models import User

    user = User.objects.create_user(
        email=email,
        password="TestPass123!",
        preferred_timezone=preferred_timezone,
        email_verified=True,
    )
    return user


def _auth_context(browser, email, django_db_blocker, *, viewport=VIEWPORT):
    with django_db_blocker.unblock():
        session_key = _create_session(email)
    context = browser.new_context(viewport=viewport)
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "event-list-time-csrf-token",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _timeline_labels(event, viewer_timezone=None):
    viewer_tz = ZoneInfo(viewer_timezone) if viewer_timezone else None
    local = event_local_datetime(
        event.start_datetime,
        event.timezone,
        viewer_tz,
    )
    return (
        f"{local.strftime('%b')} {local.day}",
        local.strftime("%A"),
        format_time_label(local),
    )


def _assert_timeline_card_datetime(card, labels):
    date_label, weekday_label, time_label = labels
    expect(card.get_by_test_id("event-card-time")).to_have_text(time_label)
    day = card.locator("xpath=ancestor::*[@data-testid='events-timeline-day']")
    expect(day.get_by_test_id("timeline-day-date")).to_have_text(date_label)
    expect(day.get_by_text(weekday_label, exact=True)).to_be_visible()


@pytest.mark.django_db(transaction=True)
def test_anonymous_event_list_cards_show_times_and_match_series_format(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_and_users()
        standalone = _create_event(
            "Mock Interviews for AI Engineering Roles",
            "mock-interviews-ai-engineering-roles-1157",
            start_datetime=_berlin_summer_start(),
        )
        grouped_series = _create_series(
            "Weekly Builds 1157",
            "weekly-builds-1157",
        )
        grouped_first = _create_event(
            "Weekly Builds 1157 Session 1",
            "weekly-builds-1157-session-1",
            start_datetime=_future_start(days=9),
            event_series=grouped_series,
        )
        _create_event(
            "Weekly Builds 1157 Session 2",
            "weekly-builds-1157-session-2",
            start_datetime=_future_start(days=16),
            event_series=grouped_series,
        )
        single_series = _create_series(
            "One-Off Series 1157",
            "one-off-series-1157",
        )
        single_occurrence = _create_event(
            "One-Off Series 1157 Session",
            "one-off-series-1157-session",
            start_datetime=_future_start(days=20),
            event_series=single_series,
        )
        expected_standalone = _timeline_labels(standalone)
        expected_grouped = _timeline_labels(grouped_first)
        expected_single = _timeline_labels(single_occurrence)
        _close_connection()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    standalone_card = page.get_by_test_id("upcoming-event-card").filter(
        has_text=standalone.title
    )
    grouped_card = page.get_by_test_id("event-series-card")
    single_card = page.get_by_test_id("upcoming-event-card").filter(
        has_text=single_occurrence.title
    )
    _assert_timeline_card_datetime(standalone_card, expected_standalone)
    _assert_timeline_card_datetime(grouped_card, expected_grouped)
    _assert_timeline_card_datetime(single_card, expected_single)
    expect(grouped_card.get_by_test_id("series-card-badge")).to_have_text("Series")
    expect(grouped_card.get_by_test_id("series-card-sessions")).to_have_text(
        "2 upcoming sessions"
    )
    expect(single_card.get_by_test_id("series-cadence-line")).to_contain_text(
        "part of One-Off Series 1157"
    )


@pytest.mark.django_db(transaction=True)
def test_member_event_list_cards_use_saved_timezone_and_utc_fallback(
    django_server, django_db_blocker, browser
):
    with django_db_blocker.unblock():
        _clear_events_and_users()
        event = _create_event(
            "Member Timezone List Card 1157",
            "member-timezone-list-card-1157",
            start_datetime=_berlin_summer_start(),
        )
        ny_user = _create_user(
            "ny@listtime.test",
            preferred_timezone="America/New_York",
        )
        _create_user(
            "invalid-tz@listtime.test",
            preferred_timezone="Not/AZone",
        )
        expected_ny = _timeline_labels(event, ny_user.preferred_timezone)
        expected_utc = _timeline_labels(event, "UTC")
        _close_connection()

    context = _auth_context(browser, "ny@listtime.test", django_db_blocker)
    page = context.new_page()
    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    card = page.get_by_test_id("upcoming-event-card")
    _assert_timeline_card_datetime(card, expected_ny)
    expect(page.get_by_test_id("events-timezone-note")).to_contain_text(
        "America/New_York"
    )
    context.close()

    context = _auth_context(browser, "invalid-tz@listtime.test", django_db_blocker)
    page = context.new_page()
    page.goto(f"{django_server}/events?filter=upcoming", wait_until="domcontentloaded")

    card = page.get_by_test_id("upcoming-event-card")
    _assert_timeline_card_datetime(card, expected_utc)
    expect(page.get_by_test_id("events-timezone-note")).to_contain_text(
        "your timezone: UTC"
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_past_event_cards_keep_destinations_and_show_start_times(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_and_users()
        upcoming = _create_event(
            "Upcoming Click Through 1157",
            "upcoming-click-through-1157",
            start_datetime=_future_start(days=7),
        )
        past = _create_event(
            "Past Recording Click Through 1157",
            "past-recording-click-through-1157",
            start_datetime=_past_start(days=4),
            end_datetime=_past_start(days=4, hour=17),
            status="completed",
            recording_url="https://youtube.com/watch?v=events1157",
            published=True,
            location="",
        )
        upcoming_url = upcoming.get_absolute_url()
        past_url = past.get_absolute_url()
        expected_past = _timeline_labels(past)
        _close_connection()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    expect(page.get_by_text(past.title)).to_have_count(0)
    page.get_by_test_id("event-card-link").filter(
        has_text="Upcoming Click Through 1157",
    ).click()
    page.wait_for_url(re.compile(rf".*{re.escape(upcoming_url)}$"))

    page.go_back(wait_until="domcontentloaded")
    page.get_by_test_id("events-filter-past").click()
    page.wait_for_url("**/events?filter=past")
    past_card = page.get_by_test_id("past-recording-card").filter(
        has_text=past.title
    )
    _assert_timeline_card_datetime(past_card, expected_past)
    expect(past_card.get_by_test_id("past-card-recording-cta")).to_have_attribute(
        "href", past_url
    )
    past_card.get_by_test_id("past-card-event-link").click()
    page.wait_for_url(re.compile(rf".*{re.escape(past_url)}$"))


@pytest.mark.django_db(transaction=True)
def test_mobile_event_list_times_do_not_create_horizontal_overflow(
    django_server, django_db_blocker, page
):
    with django_db_blocker.unblock():
        _clear_events_and_users()
        standalone = _create_event(
            "Mobile Standalone Time 1157",
            "mobile-standalone-time-1157",
            start_datetime=_future_start(days=6),
            location="Zoom",
            tags=["mock-interviews", "career"],
        )
        series = _create_series("Mobile Series 1157", "mobile-series-1157")
        grouped = _create_event(
            "Mobile Series 1157 Session 1",
            "mobile-series-1157-session-1",
            start_datetime=_future_start(days=8),
            event_series=series,
        )
        _create_event(
            "Mobile Series 1157 Session 2",
            "mobile-series-1157-session-2",
            start_datetime=_future_start(days=15),
            event_series=series,
        )
        past = _create_event(
            "Mobile Past Time 1157",
            "mobile-past-time-1157",
            start_datetime=_past_start(days=4),
            end_datetime=_past_start(days=4, hour=17),
            status="completed",
            recording_url="https://video.test/mobile-past-1157",
            published=True,
            tags=["mock-interviews", "career"],
        )
        expected_standalone = _timeline_labels(standalone)
        expected_grouped = _timeline_labels(grouped)
        expected_past = _timeline_labels(past)
        _close_connection()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    standalone_card = page.get_by_test_id("upcoming-event-card").filter(
        has_text="Mobile Standalone Time 1157",
    )
    grouped_card = page.get_by_test_id("event-series-card")
    _assert_timeline_card_datetime(standalone_card, expected_standalone)
    _assert_timeline_card_datetime(grouped_card, expected_grouped)
    expect(page.get_by_text(past.title)).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth") <= page.evaluate(
        "document.documentElement.clientWidth",
    )

    page.get_by_test_id("events-filter-past").click()
    page.wait_for_url("**/events?filter=past")
    past_card = page.get_by_test_id("past-recording-card")
    _assert_timeline_card_datetime(past_card, expected_past)
    expect(past_card.get_by_text("mock-interviews", exact=True)).to_be_visible()
    expect(past_card.get_by_text("career", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= page.evaluate(
        "document.documentElement.clientWidth",
    )
