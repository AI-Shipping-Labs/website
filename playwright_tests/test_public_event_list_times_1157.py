"""Public event-list card time coverage for issue #1157."""

import os
import re
from datetime import UTC, date, datetime, time, timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from accounts.services.timezones import format_user_datetime
from accounts.templatetags.date_formatting import event_source_short_datetime
from playwright_tests.conftest import VIEWPORT
from playwright_tests.conftest import create_session_for_user as _create_session

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only

LIST_CARD_USER_FORMAT = "%a, %b %d, %Y, %H:%M"


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


@pytest.mark.core
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
        expected_standalone = event_source_short_datetime(standalone)
        expected_grouped = event_source_short_datetime(grouped_first)
        expected_single = event_source_short_datetime(single_occurrence)
        standalone_date_only = expected_standalone.split(" · ")[0]
        _close_connection()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    expect(page.get_by_text(expected_standalone)).to_be_visible()
    assert page.get_by_text(standalone_date_only, exact=True).count() == 0

    page.goto(f"{django_server}/events?filter=upcoming", wait_until="domcontentloaded")

    expect(
        page.locator('[data-testid="event-card-date"]').filter(
            has_text=expected_standalone,
        )
    ).to_be_visible()
    expect(
        page.locator('[data-testid="series-card-date"]').filter(
            has_text=expected_grouped,
        )
    ).to_be_visible()
    expect(page.get_by_test_id("series-card-badge")).to_be_visible()
    expect(page.get_by_test_id("series-card-meta")).to_contain_text(
        "2 upcoming sessions",
    )
    expect(page.get_by_test_id("series-card-see-more")).to_be_visible()
    expect(
        page.locator('[data-testid="event-card-series-link"]').filter(
            has_text="Series: One-Off Series 1157",
        )
    ).to_be_visible()
    expect(
        page.locator('[data-testid="event-card-date"]').filter(
            has_text=expected_single,
        )
    ).to_be_visible()


@pytest.mark.core
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
        utc_user = _create_user(
            "invalid-tz@listtime.test",
            preferred_timezone="Not/AZone",
        )
        expected_ny = format_user_datetime(
            event.start_datetime,
            ny_user,
            fmt=LIST_CARD_USER_FORMAT,
        )
        expected_utc = format_user_datetime(
            event.start_datetime,
            utc_user,
            fmt=LIST_CARD_USER_FORMAT,
        )
        anonymous_text = event_source_short_datetime(event)
        _close_connection()

    context = _auth_context(browser, "ny@listtime.test", django_db_blocker)
    page = context.new_page()
    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    date_text = page.locator('[data-testid="event-card-date"]').inner_text()
    assert expected_ny in date_text
    assert "America/New_York" in date_text
    assert anonymous_text not in date_text
    context.close()

    context = _auth_context(browser, "invalid-tz@listtime.test", django_db_blocker)
    page = context.new_page()
    page.goto(f"{django_server}/events?filter=upcoming", wait_until="domcontentloaded")

    date_text = page.locator('[data-testid="event-card-date"]').inner_text()
    assert expected_utc in date_text
    assert " UTC" in date_text
    context.close()


@pytest.mark.core
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
        expected_past = event_source_short_datetime(past)
        _close_connection()

    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    expect(page.get_by_text(expected_past)).to_be_visible()
    page.get_by_test_id("event-card-link").filter(
        has_text="Upcoming Click Through 1157",
    ).click()
    page.wait_for_url(re.compile(rf".*{re.escape(upcoming_url)}$"))

    page.go_back(wait_until="domcontentloaded")
    page.get_by_role(
        "link",
        name=re.compile("Past Recording Click Through 1157"),
    ).click()
    page.wait_for_url(re.compile(rf".*{re.escape(past_url)}$"))

    page.goto(f"{django_server}/events?filter=past", wait_until="domcontentloaded")
    expect(page.get_by_text(expected_past)).to_be_visible()


@pytest.mark.core
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
        )
        expected_standalone = event_source_short_datetime(standalone)
        expected_grouped = event_source_short_datetime(grouped)
        expected_past = event_source_short_datetime(past)
        _close_connection()

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{django_server}/events", wait_until="domcontentloaded")

    expect(page.get_by_text(expected_standalone)).to_be_visible()
    expect(page.get_by_text(expected_grouped)).to_be_visible()
    expect(page.get_by_text(expected_past)).to_be_visible()
    expect(page.get_by_text("Mobile Standalone Time 1157")).to_be_visible()
    standalone_card = page.get_by_test_id("event-card-link").filter(
        has_text="Mobile Standalone Time 1157",
    )
    expect(standalone_card.get_by_text("Zoom")).to_be_visible()
    expect(page.get_by_text("mock-interviews")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth") <= page.evaluate(
        "document.documentElement.clientWidth",
    )
