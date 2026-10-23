from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from django.test import TestCase
from freezegun import freeze_time

from accounts.models import User
from booking.models import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    Booking,
    BookingHost,
    BookingWeeklyWindow,
)
from questionnaires.models import Questionnaire, Response


def _next_monday() -> date:
    """Next Monday at least 2 days in the future (host window is weekday 0)."""
    today = datetime.now(timezone.utc).date()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead < 2:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


# Keep the incident clock after the former hard-coded Monday fixture.
@freeze_time("2026-10-22T07:00:00Z")
class BookingViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@example.com",
            password="password123",
            first_name="Test",
            last_name="User",
        )
        self.questionnaire, _ = Questionnaire.objects.get_or_create(
            slug="generic-onboarding",
            defaults={"title": "Generic Onboarding", "purpose": "onboarding"},
        )
        Response.objects.create(
            questionnaire=self.questionnaire,
            respondent=self.user,
            status="submitted",
        )
        self.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Test",
                "email": "alexey.test@example.com",
                "timezone": "UTC",
                "notice_hours": 0.0,
                "horizon_days": 30,
                "slot_duration_minutes": 30,
                "is_active": True,
            },
        )
        self.host.weekly_windows.all().delete()
        # Monday (0) 10:00 - 12:00
        BookingWeeklyWindow.objects.create(
            host=self.host,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(12, 0),
            is_active=True,
        )

    def test_booking_index_redirects_anonymous(self):
        resp = self.client.get("/book")
        self.assertRedirects(resp, "/accounts/login/?next=/book", fetch_redirect_response=False)

    def test_booking_index_redirects_authenticated(self):
        self.client.force_login(self.user)
        resp = self.client.get("/book")
        self.assertRedirects(resp, "/book/alexey", fetch_redirect_response=False)

    def test_host_booking_view(self):
        self.client.force_login(self.user)
        resp = self.client.get("/book/alexey")
        self.assertContains(resp, "Alexey Test")
        self.assertContains(resp, "booking-container")

    @patch("booking.services.booking_service.query_busy")
    def test_host_slots_api(self, mock_busy):
        mock_busy.return_value = []
        target_mon = _next_monday().isoformat()
        resp = self.client.get(
            "/book/alexey/slots",
            {
                "from": target_mon,
                "to": target_mon,
                "tz": "UTC",
            },
        )
        self.assertIn("application/json", resp.headers.get("Content-Type", ""))
        data = resp.json()
        self.assertIn("slots", data)
        self.assertEqual(len(data["slots"]), 4)

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_host_confirm_post(self, mock_busy, mock_gcal, mock_zoom):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-view", "join_url": "https://zoom.test/j/view"}
        mock_gcal.return_value = {"id": "gcal-view"}

        self.client.force_login(self.user)
        target_mon = _next_monday()
        resp = self.client.post(
            "/book/alexey/confirm",
            {
                "start_iso": f"{target_mon.isoformat()}T10:00:00+00:00",
                "invitee_name": "Test User",
                "invitee_email": "test@example.com",
                "invitee_timezone": "UTC",
                "discussion_topic": "AI Project Advice",
                "discussion_notes": "Want feedback on architecture",
                "company": "TestCorp",
            },
        )
        self.assertEqual(resp.status_code, 302)
        booking = Booking.objects.get(invitee_email="test@example.com")
        self.assertRedirects(resp, f"/book/confirmed/{booking.manage_token}")

    def test_booking_confirmed_view(self):
        booking = Booking.objects.create(
            host=self.host,
            invitee_name="Test User",
            invitee_email="test@example.com",
            start_datetime=datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc),
            zoom_join_url="https://zoom.test/j/view",
            status=STATUS_CONFIRMED,
        )
        resp = self.client.get(f"/book/confirmed/{booking.manage_token}")
        self.assertContains(resp, "Scheduled!")
        self.assertContains(resp, "https://zoom.test/j/view")
        self.assertContains(resp, f"/book/reschedule/{booking.manage_token}")
        self.assertContains(resp, f"/book/cancel/{booking.manage_token}")

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.update_booking_zoom_meeting")
    @patch("booking.services.booking_service.update_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_booking_reschedule_flow(self, mock_busy, mock_up_cal, mock_up_zoom, mock_gcal, mock_zoom):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-view", "join_url": "https://zoom.test/j/view"}
        mock_gcal.return_value = {"id": "gcal-view"}

        target_mon = _next_monday()
        booking = Booking.objects.create(
            host=self.host,
            invitee_name="Test User",
            invitee_email="test@example.com",
            start_datetime=datetime(
                target_mon.year, target_mon.month, target_mon.day, 10, 0, tzinfo=timezone.utc
            ),
            end_datetime=datetime(
                target_mon.year, target_mon.month, target_mon.day, 10, 30, tzinfo=timezone.utc
            ),
            status=STATUS_CONFIRMED,
        )
        # GET reschedule page
        resp_get = self.client.get(f"/book/reschedule/{booking.manage_token}")
        self.assertContains(resp_get, "Reschedule Meeting")

        # POST reschedule
        resp_post = self.client.post(
            f"/book/reschedule/{booking.manage_token}",
            {
                "start_iso": f"{target_mon.isoformat()}T10:30:00+00:00",
            },
        )
        self.assertRedirects(resp_post, f"/book/confirmed/{booking.manage_token}")
        booking.refresh_from_db()
        self.assertEqual(
            booking.start_datetime,
            datetime(target_mon.year, target_mon.month, target_mon.day, 10, 30, tzinfo=timezone.utc),
        )

    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    @patch("booking.services.booking_service.delete_calendar_event")
    def test_booking_cancel_flow(self, mock_del_cal, mock_del_zoom):
        booking = Booking.objects.create(
            host=self.host,
            invitee_name="Test User",
            invitee_email="test@example.com",
            start_datetime=datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc),
            status=STATUS_CONFIRMED,
        )
        # GET cancel page
        resp_get = self.client.get(f"/book/cancel/{booking.manage_token}")
        self.assertContains(resp_get, "Cancel Meeting?")

        # POST cancel
        resp_post = self.client.post(f"/book/cancel/{booking.manage_token}")
        self.assertRedirects(resp_post, f"/book/confirmed/{booking.manage_token}")
        booking.refresh_from_db()
        self.assertEqual(booking.status, STATUS_CANCELED)

    def test_booking_ics_download(self):
        booking = Booking.objects.create(
            host=self.host,
            invitee_name="Test User",
            invitee_email="test@example.com",
            start_datetime=datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc),
            status=STATUS_CONFIRMED,
        )
        resp = self.client.get(f"/book/ics/{booking.manage_token}")
        self.assertTrue(resp["Content-Type"].startswith("text/calendar"))
        self.assertIn("BEGIN:VCALENDAR", resp.content.decode("utf-8"))
