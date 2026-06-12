"""Playwright scenarios for browser-timezone defaulting (issue #855).

Scenarios:
  1. Organizer with a saved timezone still sees their own zone (regression
     guard on #665 behavior).
  2. Organizer with no saved timezone gets the browser zone pre-selected
     (not UTC), and a "Set your default timezone" link is visible and
     navigates to the account page.
  3. Adding an occurrence to an existing series keeps the series zone.
  4. The event edit form's resolved line is unambiguous (UTC + local zone).
  5. UTC stays selectable.
"""

import os
import re
from datetime import UTC, datetime

import pytest

from playwright_tests.conftest import (
    create_session_for_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds DB rows and injects session cookies.
pytestmark = pytest.mark.local_only


def _reset_event_state():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_user(email, preferred_timezone):
    from django.db import connection

    from accounts.models import User
    from playwright_tests.conftest import DEFAULT_PASSWORD, ensure_tiers

    ensure_tiers()
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={
            "email_verified": True,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    user.set_password(DEFAULT_PASSWORD)
    user.is_staff = True
    user.is_superuser = True
    user.email_verified = True
    user.preferred_timezone = preferred_timezone
    user.save()
    connection.close()
    return user


def _auth_context_with_tz(browser, email, timezone_id):
    """Authed context pinned to a specific browser timezone."""
    session_key = create_session_for_user(email)
    context = browser.new_context(
        viewport={"width": 1280, "height": 720},
        timezone_id=timezone_id,
    )
    context.add_cookies([
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
    return context


@pytest.mark.django_db(transaction=True)
class TestSavedTimezoneWins:
    """A saved preference is not overridden by browser detection."""

    def test_saved_tz_preselected(self, django_server, browser):
        _reset_event_state()
        _create_user("saved-tz-855@test.com", "Europe/Berlin")
        # Browser is in New York, but the saved preference must win.
        ctx = _auth_context_with_tz(
            browser, "saved-tz-855@test.com", "America/New_York",
        )
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )
        tz_select = page.locator('[data-testid="dtp-series-tz"]')
        assert tz_select.input_value() == "Europe/Berlin"
        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestNoSavedTimezoneUsesBrowserZone:
    """No saved preference => browser zone pre-selected, settings link works."""

    def test_browser_zone_preselected_and_link_navigates(
        self, django_server, browser,
    ):
        _reset_event_state()
        _create_user("no-tz-855@test.com", "")
        ctx = _auth_context_with_tz(
            browser, "no-tz-855@test.com", "Europe/Berlin",
        )
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )

        tz_select = page.locator('[data-testid="dtp-series-tz"]')
        # JS auto-detection runs on DOMContentLoaded; wait for it to apply.
        page.wait_for_function(
            "document.querySelector('[data-testid=\"dtp-series-tz\"]')"
            ".value === 'Europe/Berlin'",
        )
        assert tz_select.input_value() == "Europe/Berlin"

        link = page.locator(
            '[data-testid="dtp-series-tz-settings-link"] a',
        )
        assert link.is_visible()
        link.click()
        page.wait_for_url(re.compile(r".*/account/.*"))
        assert page.locator(
            '[data-testid="account-timezone-input"]',
        ).is_visible()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestExistingSeriesKeepsItsTimezone:
    """Add-occurrence picker keeps the series zone, ignores browser zone."""

    def test_add_occurrence_uses_series_tz(self, django_server, browser):
        from django.db import connection

        from events.models import EventSeries

        _reset_event_state()
        _create_user("series-tz-855@test.com", "")
        series = EventSeries.objects.create(
            name="Berlin Series",
            slug="berlin-series-855",
            cadence="weekly",
            day_of_week=1,
            start_time=datetime(2000, 1, 1, 14, 30).time(),
            timezone="Europe/Berlin",
        )
        connection.close()

        # Browser in New York; series zone must still win.
        ctx = _auth_context_with_tz(
            browser, "series-tz-855@test.com", "America/New_York",
        )
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        tz_select = page.locator('[data-testid="dtp-add-tz"]')
        assert tz_select.input_value() == "Europe/Berlin"
        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestResolvedLineIsUnambiguous:
    """The edit form resolved line labels UTC and the event's zone."""

    def test_resolved_line_shows_utc_and_local(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_user("resolved-855@test.com", "Europe/Berlin")
        event = Event.objects.create(
            title="Resolved Line Event",
            slug="resolved-line-855",
            # 14:00 UTC == 16:00 Europe/Berlin (summer).
            start_datetime=datetime(2026, 6, 15, 14, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 15, 15, 0, tzinfo=UTC),
            timezone="Europe/Berlin",
            origin="studio",
        )
        connection.close()

        ctx = _auth_context_with_tz(
            browser, "resolved-855@test.com", "Europe/Berlin",
        )
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        utc_line = page.locator('[data-testid="event-resolved-utc"]')
        assert "Resolved (UTC):" in utc_line.inner_text()
        assert "14:00" in utc_line.inner_text()

        local_line = page.locator('[data-testid="event-resolved-local"]')
        local_text = local_line.inner_text()
        assert "Europe/Berlin" in local_text
        assert "16:00" in local_text
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestUtcStaysSelectable:
    """UTC remains a usable option on the event form."""

    def test_utc_selectable_and_persists(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_user("utc-pick-855@test.com", "")
        ctx = _auth_context_with_tz(
            browser, "utc-pick-855@test.com", "Europe/Berlin",
        )
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="title"]', "UTC Event 855")
        page.fill('input[name="slug"]', "utc-event-855-pw")
        page.fill('input[name="event_date"]', "15/06/2027")
        page.fill('input[name="event_time"]', "14:30")
        page.fill('input[name="duration_hours"]', "1")
        page.select_option('[data-testid="dtp-event-tz"]', "UTC")
        # Issue #860: link-less Zoom event — accept the "no meeting link"
        # confirm on submit.
        page.on("dialog", lambda d: d.accept())
        page.locator('[data-testid="event-create-submit"]').click()
        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))

        event = Event.objects.get(slug="utc-event-855-pw")
        assert event.timezone == "UTC"
        assert event.start_datetime == datetime(
            2027, 6, 15, 14, 30, tzinfo=UTC,
        )
        connection.close()
        ctx.close()
