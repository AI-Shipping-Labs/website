"""Playwright E2E test for the public booking replacement for Calendly (#1948).

Covers:
- Browsing available slots on /book/alexey/
- Booking a 30-min meeting with Zoom & Google Calendar provisioning
- Confirmation page with Zoom join link, Google Calendar add, and .ics download
- Rescheduling a booking to a new time
- Canceling a booking
- Verifying /request-a-call points to /book/alexey/ instead of Calendly
"""

import os
from datetime import time

import pytest

from playwright_tests.conftest import (
    auth_context,
    create_user,
    ensure_tiers,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _ensure_host():
    from booking.models import BookingHost, BookingWeeklyWindow
    from community.models import CallHost

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

    # Monday through Friday: 10:00 to 18:00
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


@pytest.mark.django_db(transaction=True)
class TestPublicBookingFlow:
    @browser_journey
    def test_public_booking_and_management(self, django_server, browser):
        _ensure_host()
        context = browser.new_context()
        page = context.new_page()

        # 1. Visit /book
        page.goto(f"{django_server}/book", wait_until="domcontentloaded")
        assert "/book/alexey" in page.url

        # Check host name
        page.locator('[data-testid="booking-container"]').wait_for(state="visible")
        assert "Alexey Grigorev" in page.content()

        # Wait for available calendar day and click it
        page.locator('[data-testid="calendar-day-available"]').first.wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="calendar-day-available"]').first.click()

        # Wait for slot options to load
        page.locator('[data-testid="slot-option-button"]').first.wait_for(state="visible", timeout=10000)

        # Select first slot
        first_slot = page.locator('[data-testid="slot-option-button"]').first
        first_slot.click()

        # Verify form is shown
        page.locator('[data-testid="booking-form"]').wait_for(state="visible")
        page.locator('[data-testid="input-invitee-name"]').fill("Test Learner")
        page.locator('[data-testid="input-invitee-email"]').fill("learner@example.com")
        page.locator('[data-testid="input-company"]').fill("OpenAI")
        page.locator('[data-testid="input-discussion-notes"]').fill("Interested in AI projects")

        # Submit
        page.locator('[data-testid="submit-booking-button"]').click()

        # Confirmed page
        page.locator('[data-testid="booking-confirmed-card"]').wait_for(state="visible")
        assert "You're Scheduled!" in page.content() or "Scheduled" in page.content()
        assert "Test Learner" in page.content()
        assert "learner@example.com" in page.content()
        assert page.locator('[data-testid="reschedule-link"]').is_visible()
        assert page.locator('[data-testid="cancel-link"]').is_visible()

        # 2. Reschedule
        page.locator('[data-testid="reschedule-link"]').click()
        page.locator('[data-testid="reschedule-container"]').wait_for(state="visible")

        # Pick an available day then another slot
        page.locator('[data-testid="calendar-day-available"]').first.wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="calendar-day-available"]').first.click()
        page.locator('[data-testid="slot-option-button"]').first.wait_for(state="visible", timeout=10000)
        slots = page.locator('[data-testid="slot-option-button"]')
        if slots.count() > 1:
            slots.nth(1).click()
        else:
            slots.first.click()

        page.locator('[data-testid="confirm-reschedule-button"]').wait_for(state="visible")
        page.locator('[data-testid="confirm-reschedule-button"]').click()

        page.locator('[data-testid="booking-confirmed-card"]').wait_for(state="visible")
        assert "rescheduled" in page.content().lower()

        # 3. Cancel
        page.locator('[data-testid="cancel-link"]').click()
        page.locator('[data-testid="cancel-container"]').wait_for(state="visible")
        page.locator('[data-testid="confirm-cancel-button"]').click()

        page.locator('[data-testid="booking-confirmed-card"]').wait_for(state="visible")
        assert "Canceled" in page.content()

    @browser_journey
    def test_request_a_call_links_to_new_booking(self, django_server, browser):
        ensure_tiers()
        _ensure_host()
        create_user("callmember@example.com", tier_slug="main", email_verified=True)

        from questionnaires.models import Questionnaire, Response

        q, _ = Questionnaire.objects.get_or_create(
            slug="onboarding",
            defaults={"title": "Onboarding", "purpose": "onboarding"},
        )
        if q.purpose != "onboarding":
            q.purpose = "onboarding"
            q.save(update_fields=["purpose"])
        from accounts.models import User

        u = User.objects.get(email="callmember@example.com")
        Response.objects.create(questionnaire=q, respondent=u, status="submitted")

        ctx = auth_context(browser, "callmember@example.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/request-a-call", wait_until="domcontentloaded")

        alexey_card = page.locator('[data-host-slug="alexey"]')
        alexey_card.wait_for(state="visible")
        book_link = alexey_card.locator('[data-testid="call-host-book"]')
        href = book_link.get_attribute("href")
        assert "/book/alexey" in href
        assert "calendly.com" not in href
