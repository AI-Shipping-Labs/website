"""Playwright E2E tests for Issue #1953: Booking: one call per member after onboarding and request-a-call workflow.

Covers all 10 BDD scenarios:
1. Anonymous visitor redirected to login
2. Non-onboarded member blocked by onboarding gate
3. Onboarded member with allowance sees calendar
4. Member completes booking and allowance decrements
5. Member with active booking sees banner and is gated from booking another
6. Member reschedules active booking
7. Member cancels active booking, restoring allowance
8. Member with 0 allowance sees request-a-call form
9. Member submits call request, sees pending review banner, preventing duplicates
10. Staff reviews and approves call request in Studio, granting +1 allowance
"""

import os
from datetime import time, timedelta

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context,
    create_staff_user,
    create_user,
    ensure_tiers,
)
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

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


@pytest.mark.django_db(transaction=True)
class TestBookingOneCall1953:
    # -------------------------------------------------------------
    # Scenario 1: Anonymous visitor redirected to login
    # -------------------------------------------------------------
    @browser_journey
    def test_01_anonymous_access_redirects_to_login(self, django_server, browser):
        _setup_host_and_windows()
        context = browser.new_context()
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        assert "/accounts/login/" in page.url
        assert "next=" in page.url

    # -------------------------------------------------------------
    # Scenario 2: Non-onboarded member blocked by onboarding gate
    # -------------------------------------------------------------
    @browser_journey
    def test_02_non_onboarded_member_sees_onboarding_gate(self, django_server, browser):
        _setup_host_and_windows()
        email = "unonboarded-1953@example.com"
        create_user(email, tier_slug="basic", first_name="PendingOnboarding")

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="request-call-onboarding-gate"]').wait_for(state="visible", timeout=10000)
        assert page.locator('[data-testid="request-call-onboarding-cta"]').is_visible()
        assert not page.locator('[data-testid="booking-container"]').is_visible()

    # -------------------------------------------------------------
    # Scenario 3: Onboarded member with allowance sees calendar
    # -------------------------------------------------------------
    @browser_journey
    def test_03_onboarded_member_sees_calendar(self, django_server, browser):
        _setup_host_and_windows()
        email = "onboarded-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="OnboardedUser")
        _mark_onboarded(user)

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="booking-container"]').wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="calendar-day-available"]').first.wait_for(state="visible", timeout=10000)
        assert not page.locator('[data-testid="request-call-form"]').is_visible()

    # -------------------------------------------------------------
    # Scenario 4: Member completes booking and allowance decrements
    # -------------------------------------------------------------
    @browser_journey
    def test_04_member_completes_booking_and_allowance_consumed(self, django_server, browser):
        _setup_host_and_windows()
        email = "booker-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="Booker")
        _mark_onboarded(user)

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="booking-container"]').wait_for(state="visible", timeout=10000)

        # Pick first available day and slot
        page.locator('[data-testid="calendar-day-available"]').first.click()
        page.locator('[data-testid="slot-option-button"]').first.wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="slot-option-button"]').first.click()

        # Fill and submit form
        page.locator('[data-testid="booking-form"]').wait_for(state="visible")
        page.locator('[data-testid="input-invitee-name"]').fill("Booker User")
        page.locator('[data-testid="input-invitee-email"]').fill(email)
        page.locator('[data-testid="input-company"]').fill("TestLab")
        page.locator('[data-testid="input-discussion-notes"]').fill("Architecture discussion")
        page.locator('[data-testid="submit-booking-button"]').click()

        # Land on confirmation
        page.locator('[data-testid="booking-confirmed-card"]').wait_for(state="visible", timeout=10000)
        assert "Scheduled" in page.content()

        # Verify DB state: booking created with member associated
        from booking.models import STATUS_CONFIRMED, Booking
        from booking.services.allowance import get_member_allowance

        booking = Booking.objects.get(member=user, status=STATUS_CONFIRMED)
        assert booking.invitee_email == email

        allowance = get_member_allowance(user)
        assert allowance.remaining_allowance == 0
        assert allowance.has_active_booking

    # -------------------------------------------------------------
    # Scenario 5: Member with active booking sees banner and is gated
    # -------------------------------------------------------------
    @browser_journey
    def test_05_member_with_active_booking_sees_banner_and_gated(self, django_server, browser):
        host = _setup_host_and_windows()
        email = "active-banner-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="ActiveBanner")
        _mark_onboarded(user)

        from booking.models import STATUS_CONFIRMED, Booking

        now = timezone.now()
        Booking.objects.create(
            host=host,
            member=user,
            invitee_name="Active Banner User",
            invitee_email=email,
            start_datetime=now + timedelta(days=5),
            end_datetime=now + timedelta(days=5, minutes=30),
            status=STATUS_CONFIRMED,
            zoom_join_url="https://zoom.test/j/active1953",
        )

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="active-booking-banner"]').wait_for(state="visible", timeout=10000)
        assert page.locator('[data-testid="active-booking-reschedule"]').is_visible()
        assert page.locator('[data-testid="active-booking-cancel"]').is_visible()
        assert not page.locator('[data-testid="booking-container"]').is_visible()

    # -------------------------------------------------------------
    # Scenario 6: Member reschedules active booking
    # -------------------------------------------------------------
    @browser_journey
    def test_06_member_reschedules_active_booking(self, django_server, browser):
        host = _setup_host_and_windows()
        email = "reschedule-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="Rescheduler")
        _mark_onboarded(user)

        from booking.models import STATUS_CONFIRMED, Booking

        now = timezone.now()
        Booking.objects.create(
            host=host,
            member=user,
            invitee_name="Rescheduler User",
            invitee_email=email,
            start_datetime=now + timedelta(days=5),
            end_datetime=now + timedelta(days=5, minutes=30),
            status=STATUS_CONFIRMED,
        )

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="active-booking-reschedule"]').click()

        # Reschedule page loads
        page.locator('[data-testid="reschedule-container"]').wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="calendar-day-available"]').first.click()
        page.locator('[data-testid="slot-option-button"]').first.wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="slot-option-button"]').first.click()

        page.locator('[data-testid="confirm-reschedule-button"]').click()
        page.locator('[data-testid="booking-confirmed-card"]').wait_for(state="visible", timeout=10000)

        # Still exactly 1 confirmed booking for member
        assert Booking.objects.filter(member=user, status=STATUS_CONFIRMED).count() == 1

    # -------------------------------------------------------------
    # Scenario 7: Member cancels active booking, restoring allowance
    # -------------------------------------------------------------
    @browser_journey
    def test_07_member_cancels_active_booking_and_allowance_restores(self, django_server, browser):
        host = _setup_host_and_windows()
        email = "cancel-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="Canceler")
        _mark_onboarded(user)

        from booking.models import STATUS_CANCELED, STATUS_CONFIRMED, Booking
        from booking.services.allowance import get_member_allowance

        now = timezone.now()
        booking = Booking.objects.create(
            host=host,
            member=user,
            invitee_name="Canceler User",
            invitee_email=email,
            start_datetime=now + timedelta(days=6),
            end_datetime=now + timedelta(days=6, minutes=30),
            status=STATUS_CONFIRMED,
        )

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="active-booking-cancel"]').click()

        # Cancel confirmation view
        page.locator('[data-testid="cancel-container"]').wait_for(state="visible", timeout=10000)
        page.locator('[data-testid="confirm-cancel-button"]').click()

        booking.refresh_from_db()
        assert booking.status == STATUS_CANCELED

        allowance = get_member_allowance(user)
        assert allowance.remaining_allowance == 1
        assert not allowance.has_active_booking

        # Visiting booking page now renders booking container again
        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="booking-container"]').wait_for(state="visible", timeout=10000)

    # -------------------------------------------------------------
    # Scenario 8: Member with 0 allowance sees request-a-call form
    # -------------------------------------------------------------
    @browser_journey
    def test_08_member_with_zero_allowance_sees_request_form(self, django_server, browser):
        host = _setup_host_and_windows()
        email = "zero-allowance-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="ZeroAllowance")
        _mark_onboarded(user)

        from booking.models import STATUS_CONFIRMED, Booking

        now = timezone.now()
        Booking.objects.create(
            host=host,
            member=user,
            invitee_name="Past User",
            invitee_email=email,
            start_datetime=now - timedelta(days=5),
            end_datetime=now - timedelta(days=5, minutes=-30),
            status=STATUS_CONFIRMED,
        )

        context = auth_context(browser, email)
        page = context.new_page()

        # /book/alexey renders request form
        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="request-call-form"]').wait_for(state="visible", timeout=10000)
        assert not page.locator('[data-testid="booking-container"]').is_visible()

        # /request-a-call also renders request form
        page.goto(f"{django_server}/request-a-call", wait_until="domcontentloaded")
        page.locator('[data-testid="request-call-form"]').wait_for(state="visible", timeout=10000)

    # -------------------------------------------------------------
    # Scenario 9: Member submits call request, sees pending review banner
    # -------------------------------------------------------------
    @browser_journey
    def test_09_member_submits_call_request_and_sees_pending_banner(self, django_server, browser):
        host = _setup_host_and_windows()
        email = "requester-1953@example.com"
        user = create_user(email, tier_slug="basic", first_name="Requester")
        _mark_onboarded(user)

        from booking.models import STATUS_CONFIRMED, STATUS_PENDING, Booking, CallRequest

        now = timezone.now()
        Booking.objects.create(
            host=host,
            member=user,
            invitee_name="Past Requester",
            invitee_email=email,
            start_datetime=now - timedelta(days=3),
            end_datetime=now - timedelta(days=3, minutes=-30),
            status=STATUS_CONFIRMED,
        )

        context = auth_context(browser, email)
        page = context.new_page()

        page.goto(f"{django_server}/request-a-call", wait_until="domcontentloaded")
        page.locator('[data-testid="request-call-form"]').wait_for(state="visible", timeout=10000)

        page.locator('[data-testid="request-call-topic"]').select_option(index=1)
        page.locator('[data-testid="request-call-message"]').fill("I need advice on project scaling architecture.")
        page.locator('[data-testid="request-call-submit"]').click()

        # Pending banner is now shown
        page.locator('[data-testid="pending-call-request-banner"]').wait_for(state="visible", timeout=10000)
        assert not page.locator('[data-testid="request-call-form"]').is_visible()

        # Also shown on /book/alexey
        page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        page.locator('[data-testid="pending-call-request-banner"]').wait_for(state="visible", timeout=10000)

        # CallRequest in DB
        cr = CallRequest.objects.get(member=user)
        assert cr.status == STATUS_PENDING
        assert "project scaling architecture" in cr.message

    # -------------------------------------------------------------
    # Scenario 10: Staff reviews and approves call request in Studio
    # -------------------------------------------------------------
    @browser_journey
    def test_10_staff_reviews_and_approves_call_request_in_studio(self, django_server, browser):
        host = _setup_host_and_windows()
        member_email = "member-studio-review-1953@example.com"
        member = create_user(member_email, tier_slug="basic", first_name="MemberReviewee")
        _mark_onboarded(member)

        from booking.models import STATUS_APPROVED, STATUS_CONFIRMED, STATUS_PENDING, Booking, CallRequest
        from booking.services.allowance import get_member_allowance

        now = timezone.now()
        Booking.objects.create(
            host=host,
            member=member,
            invitee_name="Past Member",
            invitee_email=member_email,
            start_datetime=now - timedelta(days=4),
            end_datetime=now - timedelta(days=4, minutes=-30),
            status=STATUS_CONFIRMED,
        )

        call_req = CallRequest.objects.create(
            member=member,
            host=host,
            topic="Scaling Transformer Models",
            message="We are having issues with batch inference scaling.",
            status=STATUS_PENDING,
        )

        staff_email = "staff-reviewer-1953@example.com"
        create_staff_user(staff_email)

        # Staff logs in and views Studio
        staff_context = auth_context(browser, staff_email)
        staff_page = staff_context.new_page()

        staff_page.goto(f"{django_server}/studio/call-requests/", wait_until="domcontentloaded")
        staff_page.locator('[data-testid="call-request-row"]').first.wait_for(state="visible", timeout=10000)
        assert "Scaling Transformer Models" in staff_page.content()

        # Open request detail
        staff_page.goto(f"{django_server}/studio/call-requests/{call_req.id}/", wait_until="domcontentloaded")
        staff_page.locator('[data-testid="review-notes-input"]').wait_for(state="visible", timeout=10000)
        staff_page.locator('[data-testid="review-notes-input"]').fill("Approved for transformer scaling discussion.")
        staff_page.locator('[data-testid="approve-request-button"]').click()

        # Review completed
        staff_page.wait_for_load_state("domcontentloaded")
        assert "Approved" in staff_page.content()

        call_req.refresh_from_db()
        assert call_req.status == STATUS_APPROVED
        assert "Approved for transformer scaling" in call_req.review_notes

        # Member's allowance is now restored to 1!
        allowance = get_member_allowance(member)
        assert allowance.remaining_allowance == 1
        assert allowance.can_book

        # Member now sees the booking calendar on /book/alexey
        member_context = auth_context(browser, member_email)
        member_page = member_context.new_page()
        member_page.goto(f"{django_server}/book/alexey", wait_until="domcontentloaded")
        member_page.locator('[data-testid="booking-container"]').wait_for(state="visible", timeout=10000)
        assert not member_page.locator('[data-testid="pending-call-request-banner"]').is_visible()
