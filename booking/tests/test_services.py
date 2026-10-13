from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from django.test import TestCase

from booking.models import (
    STATUS_CANCELED,
    STATUS_CONFIRMED,
    Booking,
    BookingHost,
    BookingWeeklyWindow,
)
from booking.services.booking_service import (
    SlotUnavailableError,
    cancel_booking,
    create_booking,
    generate_ics,
    get_available_slots,
    reschedule_booking,
)


class BookingServicesTest(TestCase):
    def setUp(self):
        self.host = BookingHost.objects.create(
            name="Alexey Test",
            slug="alexey-svc",
            email="alexey.test@example.com",
            timezone="UTC",
            notice_hours=0.0,
            horizon_days=30,
            slot_duration_minutes=30,
        )
        # Monday (0) 10:00 - 12:00
        BookingWeeklyWindow.objects.create(
            host=self.host,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(12, 0),
            is_active=True,
        )

    @patch("booking.services.booking_service.query_busy")
    def test_get_available_slots(self, mock_busy):
        mock_busy.return_value = []
        target_mon = date(2026, 10, 19)  # Monday
        slots = get_available_slots(
            self.host,
            from_date=target_mon,
            to_date=target_mon,
            viewer_timezone="UTC",
        )
        self.assertEqual(len(slots), 4)
        times = [s["time_display"] for s in slots]
        self.assertEqual(times, ["10:00", "10:30", "11:00", "11:30"])

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_create_booking_success(self, mock_busy, mock_gcal, mock_zoom):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-123", "join_url": "https://zoom.test/j/123"}
        mock_gcal.return_value = {"id": "gcal-123"}

        slot_start = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        booking = create_booking(
            host=self.host,
            invitee_name="Invitee One",
            invitee_email="one@example.com",
            start_datetime=slot_start,
            discussion_topic="AI Project Feedback",
            discussion_notes="Looking forward to chat",
            company="DTC",
        )

        self.assertEqual(booking.status, STATUS_CONFIRMED)
        self.assertEqual(booking.zoom_meeting_id, "zm-123")
        self.assertEqual(booking.zoom_join_url, "https://zoom.test/j/123")
        self.assertEqual(booking.google_event_id, "gcal-123")
        self.assertEqual(booking.end_datetime, slot_start + timedelta(minutes=30))

        # Check that subsequent call for the same slot fails as unavailable
        with self.assertRaises(SlotUnavailableError):
            create_booking(
                host=self.host,
                invitee_name="Invitee Two",
                invitee_email="two@example.com",
                start_datetime=slot_start,
            )

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.update_booking_zoom_meeting")
    @patch("booking.services.booking_service.update_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_reschedule_booking(self, mock_busy, mock_up_cal, mock_up_zoom, mock_gcal, mock_zoom):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-123", "join_url": "https://zoom.test/j/123"}
        mock_gcal.return_value = {"id": "gcal-123"}

        slot1 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        booking = create_booking(
            host=self.host,
            invitee_name="Invitee One",
            invitee_email="one@example.com",
            start_datetime=slot1,
        )

        slot2 = datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc)
        rescheduled = reschedule_booking(booking, slot2)
        self.assertEqual(rescheduled.start_datetime, slot2)
        self.assertEqual(rescheduled.end_datetime, slot2 + timedelta(minutes=30))
        mock_up_zoom.assert_called_once()
        mock_up_cal.assert_called_once()

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    @patch("booking.services.booking_service.delete_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_cancel_booking(self, mock_busy, mock_del_cal, mock_del_zoom, mock_gcal, mock_zoom):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-123", "join_url": "https://zoom.test/j/123"}
        mock_gcal.return_value = {"id": "gcal-123"}

        slot1 = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
        booking = create_booking(
            host=self.host,
            invitee_name="Invitee One",
            invitee_email="one@example.com",
            start_datetime=slot1,
        )

        canceled = cancel_booking(booking)
        self.assertEqual(canceled.status, STATUS_CANCELED)
        self.assertIsNotNone(canceled.canceled_at)
        mock_del_zoom.assert_called_once_with("zm-123")
        mock_del_cal.assert_called_once_with("gcal-123")

    def test_generate_ics(self):
        booking = Booking(
            host=self.host,
            invitee_name="Jane Doe",
            invitee_email="jane@example.com",
            start_datetime=datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc),
            manage_token="test-token-123",
            zoom_join_url="https://zoom.test/j/123",
            discussion_topic="Sprint Help",
        )
        ics = generate_ics(booking)
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertIn("DTSTART:20261019T100000Z", ics)
        self.assertIn("DTEND:20261019T103000Z", ics)
        self.assertIn("Jane Doe", ics)
        self.assertIn("https://zoom.test/j/123", ics)
        self.assertIn("END:VCALENDAR", ics)
