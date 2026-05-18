"""Playwright round-trip test for Studio time pickers (issue #665).

Scenarios:
  1. New York admin schedules an event in their local time. The UTC
     instant stored in the DB matches 14:30 NYC -> 18:30 UTC.
  2. Editing an event preserves the event's original timezone for the
     round trip: a Berlin admin sees 14:30 New_York (not 20:30 Berlin
     or 18:30 UTC).
  3. Admin with no profile TZ falls back to UTC.
"""

import os
import re
from datetime import UTC, datetime

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _reset_event_state():
    """Delete event/series/registration rows for a clean slate."""
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_user(email, preferred_timezone):
    """Create a staff user with a fixed preferred_timezone."""
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


@pytest.mark.django_db(transaction=True)
class TestNyAdminCreatesEventInLocalTime:
    """NY admin sees New_York selected and the stored UTC matches."""

    def test_ny_admin_creates_in_local_time(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_user("ny-admin-tz665@test.com", "America/New_York")
        ctx = _auth_context(browser, "ny-admin-tz665@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )

        # The TZ <select> in the shared partial picks the admin's TZ.
        tz_select = page.locator('[data-testid="dtp-event-tz"]')
        assert tz_select.is_visible()
        assert tz_select.input_value() == "America/New_York"

        page.fill('input[name="title"]', "NY Office Hours 2027")
        page.fill('input[name="slug"]', "ny-office-hours-2027")
        page.fill('input[name="event_date"]', "15/06/2027")
        page.fill('input[name="event_time"]', "14:30")
        page.fill('input[name="duration_hours"]', "1")
        page.locator('[data-testid="event-create-submit"]').click()

        page.wait_for_url(re.compile(r".*/studio/events/\d+/edit$"))

        event = Event.objects.get(slug="ny-office-hours-2027")
        assert event.timezone == "America/New_York"
        # 14:30 in America/New_York on 2027-06-15 is 18:30 UTC.
        assert event.start_datetime == datetime(
            2027, 6, 15, 18, 30, tzinfo=UTC,
        )
        connection.close()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestEditEventPreservesEventTimezone:
    """A Berlin admin editing a NY event sees the NY wall clock, not Berlin."""

    def test_edit_preserves_event_tz_in_picker(self, django_server, browser):
        from django.db import connection

        from events.models import Event

        _reset_event_state()
        _create_user("berlin-admin-tz665@test.com", "Europe/Berlin")
        event = Event.objects.create(
            title="NY-stored Event",
            slug="ny-stored-event-665",
            start_datetime=datetime(2027, 6, 15, 18, 30, tzinfo=UTC),
            end_datetime=datetime(2027, 6, 15, 19, 30, tzinfo=UTC),
            timezone="America/New_York",
            origin="studio",
        )
        connection.close()

        ctx = _auth_context(browser, "berlin-admin-tz665@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        tz_select = page.locator('[data-testid="dtp-event-tz"]')
        assert tz_select.input_value() == "America/New_York"

        # The wall-clock time shown is 14:30 NYC, not 20:30 Berlin or
        # 18:30 UTC.
        time_value = page.locator('input[name="event_time"]').input_value()
        assert time_value == "14:30", time_value

        # No-op save: refresh the form and submit unchanged. The stored
        # instant must not drift.
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(
            re.compile(rf".*/studio/events/{event.pk}/edit$"),
        )

        event.refresh_from_db()
        assert event.timezone == "America/New_York"
        assert event.start_datetime == datetime(
            2027, 6, 15, 18, 30, tzinfo=UTC,
        )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestAdminWithoutTimezoneFallsBackToUtc:
    """Admins without preferred_timezone see UTC as the default."""

    def test_no_tz_admin_sees_utc_default(self, django_server, browser):
        from django.db import connection

        _reset_event_state()
        _create_user("no-tz-admin-tz665@test.com", "")
        ctx = _auth_context(browser, "no-tz-admin-tz665@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )

        tz_select = page.locator('[data-testid="dtp-event-tz"]')
        assert tz_select.input_value() == "UTC"

        # The label next to the select reads 'UTC' (offset label is
        # 'GMT+00:00 UTC' from build_timezone_options()).
        tz_label = page.locator('[data-testid="dtp-event-tz-label"]')
        label_text = tz_label.inner_text()
        assert "UTC" in label_text, label_text

        connection.close()
        ctx.close()
