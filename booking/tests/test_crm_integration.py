from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from booking.models import BookingHost, BookingWeeklyWindow
from booking.services.booking_service import cancel_booking, create_booking, reschedule_booking
from community.models import BookedCall, CallHost

User = get_user_model()


def _next_monday() -> date:
    """Next Monday at least 2 days in the future (host window is weekday 0)."""
    today = datetime.now(timezone.utc).date()
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead < 2:
        days_ahead += 7
    return today + timedelta(days=days_ahead)


def _monday_at(target: date, hour: int, minute: int = 0) -> datetime:
    return datetime(target.year, target.month, target.day, hour, minute, tzinfo=timezone.utc)


class BookingCrmIntegrationTest(TestCase):
    def setUp(self):
        self.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "email": "alexey@datatalks.club",
                "timezone": "UTC",
                "notice_hours": 0.0,
                "horizon_days": 30,
                "slot_duration_minutes": 30,
                "is_active": True,
            },
        )
        self.host.weekly_windows.all().delete()
        BookingWeeklyWindow.objects.create(
            host=self.host,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(12, 0),
            is_active=True,
        )
        self.call_host, _ = CallHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "booking_url": "/book/alexey",
                "is_active": True,
            },
        )
        self.user = User.objects.create_user(
            email="member@example.com",
            first_name="Member",
            last_name="User",
        )

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_booking_creates_crm_booked_call(self, mock_busy, mock_gcal, mock_zoom):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-crm", "join_url": "https://zoom.test/j/crm"}
        mock_gcal.return_value = {"id": "gcal-crm"}

        slot_start = _monday_at(_next_monday(), 10, 0)
        booking = create_booking(
            host=self.host,
            invitee_name="Member User",
            invitee_email="member@example.com",
            start_datetime=slot_start,
            discussion_topic="Sprint Plan Discussion",
        )

        self.assertEqual(booking.member, self.user)
        self.assertIsNotNone(booking.booked_call)

        booked_call = BookedCall.objects.get(calendly_event_uri=f"aisl:booking:{booking.manage_token}")
        self.assertEqual(booked_call.host, self.call_host)
        self.assertEqual(booked_call.member, self.user)
        self.assertEqual(booked_call.invitee_email, "member@example.com")
        self.assertEqual(booked_call.status, "booked")
        self.assertEqual(booked_call.scheduled_at, slot_start)
        self.assertIn(f"/book/reschedule/{booking.manage_token}", booked_call.reschedule_url)
        self.assertIn(f"/book/cancel/{booking.manage_token}", booked_call.cancel_url)

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.update_booking_zoom_meeting")
    @patch("booking.services.booking_service.update_calendar_event")
    @patch("booking.services.booking_service.delete_booking_zoom_meeting")
    @patch("booking.services.booking_service.delete_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_reschedule_and_cancel_updates_crm_call(
        self, mock_busy, mock_del_cal, mock_del_zoom, mock_up_cal, mock_up_zoom, mock_gcal, mock_zoom
    ):
        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm-crm", "join_url": "https://zoom.test/j/crm"}
        mock_gcal.return_value = {"id": "gcal-crm"}

        target_mon = _next_monday()
        slot_start = _monday_at(target_mon, 10, 0)
        booking = create_booking(
            host=self.host,
            invitee_name="Member User",
            invitee_email="member@example.com",
            start_datetime=slot_start,
        )

        # Reschedule
        new_slot = _monday_at(target_mon, 10, 30)
        reschedule_booking(booking, new_slot)
        booked_call = BookedCall.objects.get(calendly_event_uri=f"aisl:booking:{booking.manage_token}")
        self.assertEqual(booked_call.scheduled_at, new_slot)
        self.assertEqual(booked_call.status, "booked")

        # Cancel
        cancel_booking(booking)
        booked_call.refresh_from_db()
        self.assertEqual(booked_call.status, "canceled")
        self.assertIsNotNone(booked_call.canceled_at)


class BookingCrmCallRecordTest(TestCase):
    """A confirmed booking records a crm.Call through record_call (#1949)."""

    def setUp(self):
        self.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "email": "alexey@datatalks.club",
                "timezone": "UTC",
                "notice_hours": 0.0,
                "horizon_days": 30,
                "slot_duration_minutes": 30,
                "is_active": True,
            },
        )
        self.host.weekly_windows.all().delete()
        BookingWeeklyWindow.objects.create(
            host=self.host,
            weekday=0,
            start_time=time(10, 0),
            end_time=time(12, 0),
            is_active=True,
        )
        CallHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "booking_url": "/book/alexey",
                "is_active": True,
            },
        )
        self.user = User.objects.create_user(
            email="member@example.com",
            first_name="Member",
            last_name="User",
        )

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_confirmed_booking_creates_crm_call(self, mock_busy, mock_gcal, mock_zoom):
        from crm.models import Call

        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm", "join_url": "https://zoom.test/j/zm"}
        mock_gcal.return_value = {"id": "gcal-1"}

        slot_start = _monday_at(_next_monday(), 10, 0)
        booking = create_booking(
            host=self.host,
            invitee_name="Member User",
            invitee_email="member@example.com",
            start_datetime=slot_start,
            discussion_topic="Sprint Plan Discussion",
        )

        call = Call.objects.get(source="booking", source_ref=str(booking.id))
        self.assertEqual(call.email, "member@example.com")
        self.assertEqual(call.occurred_at, slot_start)
        self.assertIn("Sprint Plan Discussion", call.summary)
        # No CRMRecord exists for the member: the call stores the raw
        # email with contact=NULL and never auto-creates a record.
        self.assertIsNone(call.contact)

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.booking_service.query_busy")
    def test_booking_call_links_tracked_contact(self, mock_busy, mock_gcal, mock_zoom):
        from crm.models import Call, CRMRecord

        mock_busy.return_value = []
        mock_zoom.return_value = {"meeting_id": "zm", "join_url": "https://zoom.test/j/zm"}
        mock_gcal.return_value = {"id": "gcal-1"}

        CRMRecord.objects.create(user=self.user)
        slot_start = _monday_at(_next_monday(), 10, 0)
        booking = create_booking(
            host=self.host,
            invitee_name="Member User",
            invitee_email="MEMBER@example.com",
            start_datetime=slot_start,
        )

        call = Call.objects.get(source="booking", source_ref=str(booking.id))
        self.assertEqual(call.contact, CRMRecord.objects.get(user=self.user))
        # create_booking stores invitee_email lowercased; the
        # case-insensitive contact match above is what matters.
        self.assertEqual(call.email, "member@example.com")
