"""Playwright E2E regression test: booking calendar must skip empty months.

Root cause of the 2026-10-31 scheduled-suite red (run 41233089205): the
booking page and the reschedule page initialize the calendar to the current
month only. On the last day of a month, once the host's weekly windows have
all passed (fixture: 10:00-18:00 UTC), the visible month contains no bookable
slots, every day renders as ``calendar-day-unavailable``, and members see a
fully disabled calendar unless they click the tiny next-month chevron.

The fix auto-advances the initial view to the next month that has
availability (bounded, skipped once the member navigates manually). This
test freezes both the Django server clock (freezegun) and the browser clock
(Playwright Clock API) at 20:00 UTC on the LAST day of whatever month the
suite runs in, so it reproduces the month-end dead end on any date, in any
month, without depending on the wall calendar.
"""

from datetime import date, datetime, time, timedelta, timezone

import pytest
from django.utils import timezone as django_timezone
from freezegun import freeze_time

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
)
from scripts.browser_journey_policy import browser_journey

pytestmark = pytest.mark.local_only


def _setup_host_and_windows():
    from django.db import connection

    from booking.models import BookingHost, BookingWeeklyWindow
    from community.models import CallHost

    ensure_tiers()
    host, _ = BookingHost.objects.update_or_create(
        slug="alexey",
        defaults={
            "name": "Alexey Grigorev",
            "email": "alexey@datatalks.club",
            "timezone": "UTC",
            "is_active": True,
            "notice_hours": 0.0,
            "horizon_days": 60,
            "slot_duration_minutes": 30,
        },
    )

    for day in range(7):
        BookingWeeklyWindow.objects.get_or_create(
            host=host,
            weekday=day,
            defaults={
                "start_time": time(10, 0),
                "end_time": time(18, 0),
                "is_active": True,
            },
        )

    CallHost.objects.update_or_create(
        slug="alexey",
        defaults={
            "name": "Alexey Grigorev",
            "booking_url": "/book/alexey",
            "is_active": True,
        },
    )
    connection.close()
    return host


def _mark_onboarded(user):
    from django.db import connection

    from questionnaires.models import Questionnaire, Response

    q, _ = Questionnaire.objects.get_or_create(
        slug="generic-onboarding",
        defaults={"title": "Generic Onboarding", "purpose": "onboarding"},
    )
    Response.objects.get_or_create(
        questionnaire=q,
        respondent=user,
        defaults={"status": "submitted"},
    )
    connection.close()


def _first_of_next_month(year: int, month: int) -> date:
    return date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)


def _month_end_instant_utc() -> datetime:
    """20:00 UTC on the last day of the current real month.

    At that instant the month-end month is guaranteed empty for a host whose
    daily windows close at 18:00 UTC: every window of the month is in the
    past, and no future day remains in the month.
    """
    today = django_timezone.now().astimezone(timezone.utc).date()
    month_end = _first_of_next_month(today.year, today.month) - timedelta(days=1)
    return datetime(month_end.year, month_end.month, month_end.day, 20, 0, tzinfo=timezone.utc)


def _expected_next_month_label(frozen: datetime) -> str:
    """The month label the calendar must land on, e.g. "November 2026"."""
    return _first_of_next_month(frozen.year, frozen.month).strftime("%B %Y")


@pytest.mark.django_db(transaction=True)
class TestBookingCalendarSkipsEmptyMonths:
    @browser_journey
    def test_booking_page_lands_on_next_month_with_availability(self, django_server, browser):
        frozen = _month_end_instant_utc()
        expected_label = _expected_next_month_label(frozen)

        _setup_host_and_windows()
        email = "monthend-booker@example.com"
        user = create_user(email, tier_slug="basic", first_name="MonthEndBooker")
        _mark_onboarded(user)

        with freeze_time(frozen, tick=True):
            # Create the session inside the frozen clock so it is not already
            # expired relative to the frozen server time.
            context = auth_context(browser, email)
            page = context.new_page()
            page.clock.set_fixed_time(frozen.isoformat())
            page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
            page.locator('[data-testid="booking-container"]').wait_for(state="visible", timeout=15000)
            # The month-end month has no slots; the calendar must advance to
            # the next month instead of rendering a fully disabled month.
            page.locator('[data-testid="calendar-day-available"]').first.wait_for(
                state="visible", timeout=15000
            )
            assert page.locator("#month-label").inner_text() == expected_label

            # Selecting the first available day still yields bookable slots.
            page.locator('[data-testid="calendar-day-available"]').first.click()
            page.locator('[data-testid="slot-option-button"]').first.wait_for(state="visible", timeout=10000)

    @browser_journey
    def test_reschedule_page_lands_on_next_month_with_availability(self, django_server, browser):
        frozen = _month_end_instant_utc()
        expected_label = _expected_next_month_label(frozen)

        host = _setup_host_and_windows()
        email = "monthend-rescheduler@example.com"
        user = create_user(email, tier_slug="basic", first_name="MonthEndRescheduler")
        _mark_onboarded(user)

        from booking.models import STATUS_CONFIRMED, Booking

        Booking.objects.create(
            host=host,
            member=user,
            invitee_name="MonthEnd Rescheduler",
            invitee_email=email,
            start_datetime=frozen + timedelta(days=5),
            end_datetime=frozen + timedelta(days=5, minutes=30),
            status=STATUS_CONFIRMED,
        )

        with freeze_time(frozen, tick=True):
            # Create the session inside the frozen clock so it is not already
            # expired relative to the frozen server time.
            context = auth_context(browser, email)
            page = context.new_page()
            page.clock.set_fixed_time(frozen.isoformat())
            page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
            page.locator('[data-testid="active-booking-reschedule"]').click()
            page.locator('[data-testid="reschedule-container"]').wait_for(state="visible", timeout=15000)
            page.locator('[data-testid="calendar-day-available"]').first.wait_for(
                state="visible", timeout=15000
            )
            assert page.locator("#month-label").inner_text() == expected_label
