"""Tests for 1:1 Calendly slot parity, submit-time re-check, and invite alerting."""

import json
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from urllib.error import URLError

from django.core import mail
from django.test import TestCase
from freezegun import freeze_time

from booking.models import STATUS_CONFIRMED, Booking, BookingHost
from booking.services.booking_service import (
    SlotUnavailableError,
    create_booking,
    get_available_slots,
    reschedule_booking,
)
from booking.services.calendar import create_calendar_event
from booking.services.calendly_slots import (
    DEFAULT_ALEXEY_CALENDLY_URL,
    clear_calendly_slots_cache,
    resolve_calendly_event_url,
)
from community.models import CallHost


def _mock_urlopen_responses(lookup_payload: dict, range_payload: dict):
    """Build a urlopen side_effect returning lookup_payload then range_payload."""

    def _side_effect(req, timeout=15):
        url = req.full_url
        body = range_payload
        if "/lookup?" in url:
            body = lookup_payload
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode("utf-8")
        cm = MagicMock()
        cm.__enter__.return_value = resp
        cm.__exit__.return_value = False
        return cm

    return _side_effect


@freeze_time("2026-10-29T01:00:00Z")
class CalendlySlotParityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host, _ = BookingHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "email": "alexey@datatalks.club",
                "timezone": "Europe/Berlin",
                "notice_hours": 12.0,
                "horizon_days": 60,
                "slot_duration_minutes": 30,
                "is_active": True,
            },
        )
        cls.call_host, _ = CallHost.objects.update_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "booking_url": DEFAULT_ALEXEY_CALENDLY_URL,
                "is_active": True,
            },
        )

    def setUp(self):
        clear_calendly_slots_cache()
        self.addCleanup(clear_calendly_slots_cache)

    def test_resolve_calendly_event_url_returns_alexey_calendly(self):
        self.assertEqual(
            resolve_calendly_event_url(self.host),
            DEFAULT_ALEXEY_CALENDLY_URL,
        )

    @patch("booking.services.calendly_slots.urlopen")
    def test_slots_match_calendly_one_for_one_and_exclude_busy_day(self, mock_urlopen):
        lookup = {"uuid": "evt-uuid-1", "duration": 30, "availability_timezone": "Europe/Berlin"}
        cal_range = {
            "days": [
                {"date": "2026-10-29", "status": "unavailable", "spots": []},
                {
                    "date": "2026-11-02",
                    "status": "available",
                    "spots": [{"status": "available", "start_time": "2026-11-02T10:30:00Z"}],
                },
                {
                    "date": "2026-11-03",
                    "status": "available",
                    "spots": [
                        {"status": "available", "start_time": "2026-11-03T13:00:00Z"},
                        {"status": "available", "start_time": "2026-11-03T13:30:00Z"},
                    ],
                },
            ]
        }
        mock_urlopen.side_effect = _mock_urlopen_responses(lookup, cal_range)
        slots = get_available_slots(
            self.host,
            from_date=date(2026, 10, 29),
            to_date=date(2026, 11, 4),
            viewer_timezone="UTC",
        )
        starts = [s["start_iso"] for s in slots]
        self.assertEqual(
            starts,
            [
                "2026-11-02T10:30:00+00:00",
                "2026-11-03T13:00:00+00:00",
                "2026-11-03T13:30:00+00:00",
            ],
        )

    @patch("booking.services.calendly_slots.urlopen")
    def test_slots_fail_closed_when_calendly_unreachable(self, mock_urlopen):
        mock_urlopen.side_effect = URLError("Calendly down")
        slots = get_available_slots(
            self.host,
            from_date=date(2026, 10, 29),
            to_date=date(2026, 11, 4),
            viewer_timezone="UTC",
        )
        self.assertEqual(slots, [])

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.calendly_slots.urlopen")
    def test_submit_recheck_rejects_unavailable_oct_29_slot(
        self, mock_urlopen, mock_gcal, mock_zoom
    ):
        lookup = {"uuid": "evt-uuid-1", "duration": 30, "availability_timezone": "Europe/Berlin"}
        cal_range = {"days": [{"date": "2026-10-29", "status": "unavailable", "spots": []}]}
        mock_urlopen.side_effect = _mock_urlopen_responses(lookup, cal_range)
        with self.assertRaises(SlotUnavailableError):
            create_booking(
                host=self.host,
                invitee_name="Member",
                invitee_email="member@example.com",
                start_datetime=datetime(2026, 10, 29, 16, 30, tzinfo=timezone.utc),
            )
        mock_zoom.assert_not_called()
        mock_gcal.assert_not_called()

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.calendly_slots.urlopen")
    def test_reschedule_recheck_rejects_unavailable_slot(
        self, mock_urlopen, mock_gcal, mock_zoom
    ):
        booking = Booking.objects.create(
            host=self.host,
            invitee_name="Member",
            invitee_email="member@example.com",
            start_datetime=datetime(2026, 11, 2, 10, 30, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc),
            status=STATUS_CONFIRMED,
        )
        lookup = {"uuid": "evt-uuid-1", "duration": 30}
        cal_range = {"days": [{"date": "2026-10-29", "status": "unavailable", "spots": []}]}
        mock_urlopen.side_effect = _mock_urlopen_responses(lookup, cal_range)
        with self.assertRaises(SlotUnavailableError):
            reschedule_booking(booking, datetime(2026, 10, 29, 16, 30, tzinfo=timezone.utc))

    @patch("booking.services.calendar._google_request")
    def test_create_calendar_event_invites_both_attendee_and_host(self, mock_google):
        mock_google.return_value = {"id": "gcal-evt-99"}
        res = create_calendar_event(
            summary="30-min meeting",
            description="Topic: Architecture",
            start=datetime(2026, 11, 2, 10, 30, tzinfo=timezone.utc),
            end=datetime(2026, 11, 2, 11, 0, tzinfo=timezone.utc),
            attendee_email="member@example.com",
            host_email="alexey@datatalks.club",
            location="https://zoom.us/j/123",
        )
        self.assertEqual(res["id"], "gcal-evt-99")
        call_kwargs = mock_google.call_args.kwargs
        self.assertEqual(call_kwargs["params"], {"sendUpdates": "all"})
        self.assertEqual(
            call_kwargs["payload"]["attendees"],
            [{"email": "member@example.com"}, {"email": "alexey@datatalks.club"}],
        )

    @patch("booking.services.booking_service.create_booking_zoom_meeting")
    @patch("booking.services.booking_service.create_calendar_event")
    @patch("booking.services.calendly_slots.urlopen")
    def test_calendar_failure_sends_alert_email_with_ics_to_alexey(
        self, mock_urlopen, mock_gcal, mock_zoom
    ):
        mail.outbox = []
        lookup = {"uuid": "evt-uuid-1", "duration": 30}
        cal_range = {
            "days": [
                {
                    "date": "2026-11-02",
                    "status": "available",
                    "spots": [{"status": "available", "start_time": "2026-11-02T10:30:00Z"}],
                }
            ]
        }
        mock_urlopen.side_effect = _mock_urlopen_responses(lookup, cal_range)
        mock_zoom.return_value = {"meeting_id": "zm-99", "join_url": "https://zoom.test/j/99"}
        mock_gcal.return_value = {}

        booking = create_booking(
            host=self.host,
            invitee_name="Alert Member",
            invitee_email="alert-member@example.com",
            start_datetime=datetime(2026, 11, 2, 10, 30, tzinfo=timezone.utc),
            discussion_topic="AI Project Architecture & Feedback",
        )
        self.assertEqual(booking.google_event_id, "")
        self.assertEqual(len(mail.outbox), 1)
        alert_msg = mail.outbox[0]
        self.assertEqual(alert_msg.to, ["alexey@datatalks.club"])
        self.assertIn("[Alert]", alert_msg.subject)
        self.assertIn("Alert Member", alert_msg.body)
        self.assertEqual(len(alert_msg.attachments), 1)
        filename, content, mimetype = alert_msg.attachments[0]
        self.assertEqual(filename, "booking.ics")
        self.assertIn("METHOD:REQUEST", content)
        self.assertIn("mailto:alexey@datatalks.club", content)
