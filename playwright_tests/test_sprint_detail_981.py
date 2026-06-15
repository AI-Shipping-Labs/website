"""Playwright coverage for the redesigned sprint detail page (#981)."""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_session_for_user,
    ensure_site_config_tiers,
    ensure_tiers,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _clear_sprint_detail_data():
    from django.db import connection

    from events.models import Event, EventSeries
    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_member(email="main@test.com", *, preferred_timezone=""):
    from django.db import connection

    user = _create_user(email, tier_slug="main")
    if preferred_timezone:
        user.preferred_timezone = preferred_timezone
        user.save(update_fields=["preferred_timezone"])
    connection.close()
    return user


def _create_series(slug="issue-981-calls"):
    from django.db import connection

    from events.models import EventSeries

    series = EventSeries.objects.create(
        name="Sprint calls",
        slug=slug,
        cadence="weekly",
        day_of_week=2,
        start_time=datetime.time(18, 0),
        timezone="Europe/Berlin",
    )
    connection.close()
    return series


def _create_sprint(slug="issue-981-sprint", *, series=None, min_tier_level=20):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name="Issue 981 Sprint",
        slug=slug,
        start_date=datetime.date.today() - datetime.timedelta(days=7),
        duration_weeks=6,
        status="active",
        min_tier_level=min_tier_level,
        event_series=series,
    )
    connection.close()
    return sprint


def _enroll(email, sprint):
    from django.db import connection

    from accounts.models import User
    from plans.models import SprintEnrollment

    SprintEnrollment.objects.get_or_create(
        sprint=sprint,
        user=User.objects.get(email=email),
    )
    connection.close()


def _create_event(series, *, title, slug, start, end=None, zoom_url=""):
    from django.db import connection

    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        description="Sprint call",
        kind="standard",
        platform="zoom",
        start_datetime=start,
        end_datetime=end,
        timezone="Europe/Berlin",
        status="upcoming",
        origin="studio",
        event_series=series,
        location="Zoom",
        zoom_join_url=zoom_url,
        published=True,
    )
    connection.close()
    return event


@pytest.mark.django_db(transaction=True)
class TestSprintDetailRedesign981:
    def test_member_opens_upcoming_call_detail_and_sees_past_call_muted(
        self, django_server, browser, django_db_blocker
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Past sprint call",
                slug="past-sprint-call",
                start=now - datetime.timedelta(hours=2),
                end=now - datetime.timedelta(hours=1),
                zoom_url="https://zoom.example.com/past",
            )
            upcoming = _create_event(
                series,
                title="Upcoming sprint call",
                slug="upcoming-sprint-call",
                start=now + datetime.timedelta(days=1),
                zoom_url="https://zoom.example.com/future",
            )
            sprint_slug = sprint.slug
            upcoming_url = upcoming.get_absolute_url()

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        rows = page.locator('[data-testid="sprint-call-entry"]')
        assert rows.count() == 2
        upcoming_row = rows.filter(has_text="Upcoming sprint call")
        past_row = rows.filter(has_text="Past sprint call")
        assert "next call" in upcoming_row.locator('[data-testid="sprint-call-status"]').inner_text().lower()
        assert "past" in past_row.locator('[data-testid="sprint-call-status"]').inner_text().lower()
        assert past_row.locator('[data-testid="sprint-call-join"]').count() == 0

        upcoming_row.locator('[data-testid="sprint-call-entry-link"]').click()
        page.wait_for_url(f"**{upcoming_url}")
        ctx.close()

    def test_call_starting_soon_uses_tracked_join_link(
        self, django_server, browser, django_db_blocker
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Live sprint call",
                slug="live-sprint-call",
                start=now + datetime.timedelta(minutes=10),
                zoom_url="https://zoom.example.com/raw-live-link",
            )
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        join = page.locator('[data-testid="sprint-call-join"]')
        assert join.get_attribute("href").endswith("/events/live-sprint-call/join")
        assert join.get_attribute("target") == "_blank"
        assert "zoom.example.com/raw-live-link" not in page.content()
        ctx.close()

    def test_not_open_call_and_empty_calls_states(
        self, django_server, browser, django_db_blocker
    ):
        now = datetime.datetime.now(datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Future sprint call",
                slug="future-sprint-call",
                start=now + datetime.timedelta(days=2),
                zoom_url="https://zoom.example.com/raw-future-link",
            )
            empty_sprint = _create_sprint(slug="issue-981-empty")
            _enroll("main@test.com", empty_sprint)
            sprint_slug = sprint.slug
            empty_slug = empty_sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")
        assert page.locator('[data-testid="sprint-call-join-not-open"]').is_visible()
        assert "Join link opens ~15 min before start" in page.locator(
            '[data-testid="sprint-call-join-not-open"]'
        ).inner_text()
        assert page.locator('[data-testid="sprint-call-join"]').count() == 0
        assert page.locator('[data-testid="sprint-call-details"]').get_attribute("href")

        page.goto(f"{django_server}/sprints/{empty_slug}", wait_until="domcontentloaded")
        assert page.locator('[data-testid="sprint-calls-empty"]').is_visible()
        assert page.locator('[data-testid="sprint-cta-enrolled"]').is_visible()
        ctx.close()

    def test_saved_timezone_payload_uses_event_time_display_partial(
        self, django_server, browser, django_db_blocker
    ):
        start = datetime.datetime(2026, 6, 17, 18, 0, tzinfo=datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member(preferred_timezone="America/New_York")
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Timezone sprint call",
                slug="timezone-sprint-call",
                start=start,
                end=start + datetime.timedelta(hours=1),
            )
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        time_display = page.locator('[data-event-time-display]')
        assert time_display.get_attribute("data-start-utc") == "2026-06-17T18:00:00Z"
        assert time_display.get_attribute("data-default-timezone") == "America/New_York"
        assert time_display.get_attribute("data-browser-timezone-enabled") == "false"
        assert "America/New_York" in time_display.inner_text()
        ctx.close()

    def test_browser_timezone_localizes_call_time_without_saved_timezone(
        self, django_server, browser, django_db_blocker
    ):
        start = datetime.datetime(2026, 6, 17, 18, 0, tzinfo=datetime.timezone.utc)
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            series = _create_series()
            sprint = _create_sprint(series=series)
            _enroll("main@test.com", sprint)
            _create_event(
                series,
                title="Browser timezone first call",
                slug="browser-timezone-first-call",
                start=start,
                end=start + datetime.timedelta(hours=1),
            )
            _create_event(
                series,
                title="Browser timezone second call",
                slug="browser-timezone-second-call",
                start=start + datetime.timedelta(days=1),
                end=start + datetime.timedelta(days=1, hours=1),
            )
            sprint_slug = sprint.slug
            session_key = create_session_for_user("main@test.com")

        ctx = browser.new_context(
            timezone_id="America/Los_Angeles",
            viewport={"width": 1280, "height": 720},
        )
        ctx.add_cookies([
            {
                "name": "sessionid",
                "value": session_key,
                "domain": "127.0.0.1",
                "path": "/",
            },
            {
                "name": "csrftoken",
                "value": "e2e-test-csrf-token-value",
                "domain": "127.0.0.1",
                "path": "/",
            },
        ])
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="networkidle")

        time_displays = page.locator('[data-event-time-display]')
        assert time_displays.count() == 2
        for index in range(2):
            time_display = time_displays.nth(index)
            assert time_display.get_attribute("data-browser-timezone-enabled") == "true"
            assert time_display.get_attribute("data-default-timezone") == "Europe/Berlin"
            assert "America/Los_Angeles" in time_display.inner_text()
            assert "Europe/Berlin" not in time_display.inner_text()
        ctx.close()

    def test_eligible_member_can_join_near_top(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprint_detail_data()
            _create_member()
            sprint = _create_sprint()
            sprint_slug = sprint.slug

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")

        action_panel = page.locator('[data-testid="sprint-primary-action"]')
        assert action_panel.locator('[data-testid="sprint-cta-join"]').is_visible()
        action_panel.locator('[data-testid="sprint-cta-join"]').click()
        page.wait_for_url(f"**/sprints/{sprint_slug}/board")
        page.goto(f"{django_server}/sprints/{sprint_slug}", wait_until="domcontentloaded")
        assert page.locator('[data-testid="sprint-cta-enrolled"]').is_visible()
        assert page.locator('[data-testid="sprint-calls-empty"]').is_visible()
        ctx.close()
