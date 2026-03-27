"""
Playwright E2E tests for Studio Event Date/Time Picker UX (Issue #107).

Tests cover all 4 BDD scenarios from the issue:
- Staff creates an event using the date/time picker and verifies saved data
- Staff edits an existing event and sees pre-populated date/time fields
- Staff leaves Duration blank and gets a 1-hour default
- Staff cannot accidentally save an ambiguous datetime from the old raw input

Usage:
    uv run pytest playwright_tests/test_studio_event_datetime.py -v
"""

import os
from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_events():
    """Delete all events and registrations to ensure clean state."""
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(title, slug, start_datetime=None, end_datetime=None, **kwargs):
    """Create an Event via ORM."""
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + timedelta(days=7)

    event = Event(
        title=title,
        slug=slug,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        **kwargs,
    )
    event.save()
    connection.close()
    return event


# ---------------------------------------------------------------
# Scenario 1: Staff creates an event using the date/time picker
#              and verifies saved data
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1StaffCreatesEventWithDateTimePicker:
    """Staff creates an event using the date/time picker and verifies saved data."""

    def test_create_event_with_separate_date_time_duration(
        self, django_server
    , browser):
        """Given: A staff user is logged in and navigates to /studio/events/new.
        1. Observe the form -- separate Date and Time fields are visible; no raw datetime-local input
        2. Click the Date field -- a calendar picker appears
        3. Enter a date, time, and duration
        4. Fill in a title and other required fields, then click 'Create Event'
        Then: Redirected to the edit page for the new event
        Then: The database has correct start_datetime and end_datetime."""
        _clear_events()
        _create_staff_user("staff-create@test.com")

        context = _auth_context(browser, "staff-create@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/events/new
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Separate Date and Time fields are visible
        date_field = page.locator('input[name="event_date"]')
        time_field = page.locator('input[name="event_time"]')
        duration_field = page.locator('input[name="duration_hours"]')

        assert date_field.count() == 1
        assert time_field.count() == 1
        assert duration_field.count() == 1

        # No raw datetime-local input
        datetime_local_inputs = page.locator('input[type="datetime-local"]')
        assert datetime_local_inputs.count() == 0

        # Step 2: Click the Date field -- calendar picker script is loaded
        assert "flatpickr" in body

        # Step 3: Enter date, time, and duration manually
        date_field.fill("15/03/2026")
        time_field.fill("14:30")
        duration_field.fill("2")

        # Step 4: Fill in title and required fields
        page.locator('input[name="title"]').fill("Picker Test Event")
        page.locator('input[name="slug"]').fill("picker-test-event")

        # Submit the form
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Redirected to the edit page (URL contains /edit)
        assert "/edit" in page.url
        assert "/studio/events/" in page.url

        # Then: Verify in DB
        from events.models import Event
        event = Event.objects.get(slug="picker-test-event")
        assert event.start_datetime.year == 2026
        assert event.start_datetime.month == 3
        assert event.start_datetime.day == 15
        assert event.start_datetime.hour == 14
        assert event.start_datetime.minute == 30
        assert event.end_datetime.hour == 16
        assert event.end_datetime.minute == 30
# ---------------------------------------------------------------
# Scenario 2: Staff edits an existing event and sees pre-populated
#              date/time fields
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2StaffEditsEventPrePopulated:
    """Staff edits an existing event and sees pre-populated date/time fields."""

    def test_edit_event_prepopulated_and_save(
        self, django_server
    , browser):
        """Given: A staff user is logged in; an event exists with
        start_datetime=2026-06-01 10:00 and end_datetime=2026-06-01 11:30.
        1. Navigate to /studio/events/<id>/edit
        Then: The Date field shows 01/06/2026, Time shows 10:00, Duration shows 1.5
        2. Change the time to 09:00 and the duration to 3, then save
        Then: start_datetime is 2026-06-01 09:00 and end_datetime is 2026-06-01 12:00."""
        _clear_events()
        _create_staff_user("staff-edit@test.com")

        event = _create_event(
            title="Pre-Populated Event",
            slug="pre-populated-event",
            start_datetime=datetime(2026, 6, 1, 10, 0),
            end_datetime=datetime(2026, 6, 1, 11, 30),
            status="draft",
        )

        context = _auth_context(browser, "staff-edit@test.com")
        page = context.new_page()
        # Step 1: Navigate to the edit page
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Then: Check pre-populated values
        date_field = page.locator('input[name="event_date"]')
        time_field = page.locator('input[name="event_time"]')
        duration_field = page.locator('input[name="duration_hours"]')

        date_value = date_field.input_value()
        time_value = time_field.input_value()
        duration_value = duration_field.input_value()

        assert date_value == "01/06/2026", f"Expected 01/06/2026, got {date_value}"
        assert time_value == "10:00", f"Expected 10:00, got {time_value}"
        assert duration_value == "1.5", f"Expected 1.5, got {duration_value}"

        # Step 2: Change time and duration
        time_field.fill("")
        time_field.fill("09:00")
        duration_field.fill("")
        duration_field.fill("3")

        # Click Save Changes
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Verify in DB
        from events.models import Event as EventModel
        event_db = EventModel.objects.get(slug="pre-populated-event")
        assert event_db.start_datetime.year == 2026
        assert event_db.start_datetime.month == 6
        assert event_db.start_datetime.day == 1
        assert event_db.start_datetime.hour == 9
        assert event_db.start_datetime.minute == 0
        assert event_db.end_datetime.hour == 12
        assert event_db.end_datetime.minute == 0
# ---------------------------------------------------------------
# Scenario 3: Staff leaves Duration blank and gets a 1-hour default
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3DurationDefaultsToOneHour:
    """Staff leaves Duration blank and gets a 1-hour default."""

    def test_blank_duration_defaults_to_one_hour(
        self, django_server
    , browser):
        """Given: A staff user is logged in and navigates to /studio/events/new.
        1. Enter a date and time but leave Duration blank
        2. Submit the form with a title and other required fields
        Then: The event is created with end_datetime = start_datetime + 1 hour."""
        _clear_events()
        _create_staff_user("staff-default@test.com")

        context = _auth_context(browser, "staff-default@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )

        # Fill in required fields
        page.locator('input[name="title"]').fill("Default Duration Event")
        page.locator('input[name="slug"]').fill("default-duration-event")
        page.locator('input[name="event_date"]').fill("20/06/2026")
        page.locator('input[name="event_time"]').fill("09:00")

        # Clear the duration field (leave it blank)
        duration_field = page.locator('input[name="duration_hours"]')
        duration_field.fill("")

        # Submit the form
        page.locator('button[type="submit"]').click()
        page.wait_for_load_state("domcontentloaded")

        # Then: Redirected to edit page
        assert "/edit" in page.url

        # Then: Verify in DB
        from events.models import Event
        event = Event.objects.get(slug="default-duration-event")
        assert event.start_datetime.hour == 9
        assert event.start_datetime.minute == 0
        assert event.end_datetime.hour == 10
        assert event.end_datetime.minute == 0
# ---------------------------------------------------------------
# Scenario 4: Staff cannot accidentally save an ambiguous datetime
#              from the old raw input
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4NoDatetimeLocalInput:
    """Staff cannot accidentally save an ambiguous datetime from the old raw input."""

    def test_no_datetime_local_input_on_create_form(
        self, django_server
    , browser):
        """Given: A staff user is logged in and navigates to /studio/events/new.
        Then: There is no datetime-local type input visible on the form.
        Then: Only the separate Date and Time text/picker fields are present."""
        _clear_events()
        _create_staff_user("staff-noraw@test.com")

        context = _auth_context(browser, "staff-noraw@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/new",
            wait_until="domcontentloaded",
        )

        # No datetime-local type input
        datetime_local_inputs = page.locator('input[type="datetime-local"]')
        assert datetime_local_inputs.count() == 0

        # The separate Date and Time fields are present
        date_field = page.locator('input[name="event_date"]')
        time_field = page.locator('input[name="event_time"]')
        assert date_field.count() == 1
        assert time_field.count() == 1

        # No old start_datetime or end_datetime fields
        old_start = page.locator('input[name="start_datetime"]')
        old_end = page.locator('input[name="end_datetime"]')
        assert old_start.count() == 0
        assert old_end.count() == 0
    def test_no_datetime_local_input_on_edit_form(
        self, django_server
    , browser):
        """Also verify the edit form has no datetime-local inputs."""
        _clear_events()
        _create_staff_user("staff-noraw-edit@test.com")

        event = _create_event(
            title="No Raw Event",
            slug="no-raw-event",
            start_datetime=datetime(2026, 3, 15, 14, 0),
            end_datetime=datetime(2026, 3, 15, 16, 0),
            status="draft",
        )

        context = _auth_context(browser, "staff-noraw-edit@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )

        # No datetime-local type input
        datetime_local_inputs = page.locator('input[type="datetime-local"]')
        assert datetime_local_inputs.count() == 0

        # Separate Date and Time fields are present
        date_field = page.locator('input[name="event_date"]')
        time_field = page.locator('input[name="event_time"]')
        assert date_field.count() == 1
        assert time_field.count() == 1