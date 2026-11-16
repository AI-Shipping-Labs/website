"""Regression tests for Google Calendar sync on event reschedule."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event
from events.services.google_calendar import google_calendar_event_id_from_uid

User = get_user_model()


class GoogleCalendarUidEncodingTest(TestCase):
    """Verify deterministic Google Calendar event ID derivation from UID."""

    def test_derives_expected_google_ids_for_events_74_and_75(self):
        self.assertEqual(
            google_calendar_event_id_from_uid(
                "event-freestyle-workshop-january-4-2027@aishippinglabs.com",
            ),
            "_clr6arjk5lj74pb5edq7ir355lrmusjbedk6us1dd9gmstb1e9siqd1d68o34dq0c5kn6q39e1o6irj7dhgm4specdnmq",
        )
        self.assertEqual(
            google_calendar_event_id_from_uid(
                "event-mcp-workshop@aishippinglabs.com",
            ),
            "_clr6arjk5lmm6s1detnn4qrjd1nn0g31d5pmgqbge1kmsprcc5h76bj3dtmg",
        )


class EventGoogleCalendarSyncTest(TestCase):
    """Regression tests for PATCH /api/events/<slug> and sync-calendar."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="gcal-sync-staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="gcal-sync")

    def setUp(self):
        self.start = (timezone.now() + timedelta(days=30)).replace(
            minute=0, second=0, microsecond=0,
        )
        self.event = Event.objects.create(
            title="MCP",
            slug="mcp-workshop-test",
            start_datetime=self.start,
            end_datetime=self.start + timedelta(hours=2),
            status="upcoming",
            timezone="Europe/Berlin",
            origin="studio",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    @patch("booking.services.calendar._google_request")
    def test_api_patch_reschedule_patches_google_calendar_with_send_updates_none(
        self, mock_google_request,
    ):
        mock_google_request.return_value = {"id": "patched"}
        new_start = self.start + timedelta(days=13)
        new_end = new_start + timedelta(hours=2)

        resp = self.client.patch(
            f"/api/events/{self.event.slug}",
            data=json.dumps({
                "start_datetime": new_start.isoformat(),
                "end_datetime": new_end.isoformat(),
            }),
            content_type="application/json",
            **self._auth(),
        )

        body = resp.json()
        self.assertEqual(body["slug"], self.event.slug)
        expected_id = google_calendar_event_id_from_uid(self.event.calendar_uid)
        self.assertEqual(mock_google_request.call_count, 1)
        method, path = mock_google_request.call_args.args
        kwargs = mock_google_request.call_args.kwargs
        self.assertEqual(method, "PATCH")
        self.assertEqual(path, f"/calendars/primary/events/{expected_id}")
        self.assertEqual(kwargs["params"], {"sendUpdates": "none"})
        self.assertEqual(kwargs["payload"]["start"]["timeZone"], "Europe/Berlin")
        self.assertEqual(kwargs["payload"]["end"]["timeZone"], "Europe/Berlin")

    @patch("booking.services.calendar._google_request")
    def test_api_patch_title_only_does_not_patch_google_calendar(
        self, mock_google_request,
    ):
        resp = self.client.patch(
            f"/api/events/{self.event.slug}",
            data=json.dumps({"title": "MCP Updated"}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(resp.json()["title"], "MCP Updated")
        mock_google_request.assert_not_called()

    @patch("events.tasks.notify_reschedule.enqueue_reschedule_notice")
    @patch("booking.services.calendar._google_request")
    def test_sync_calendar_endpoint_patches_without_sending_emails(
        self, mock_google_request, mock_reschedule,
    ):
        expected_id = google_calendar_event_id_from_uid(self.event.calendar_uid)
        mock_google_request.return_value = {
            "id": expected_id,
            "start": {"dateTime": self.start.isoformat()},
            "end": {"dateTime": (self.start + timedelta(hours=2)).isoformat()},
        }

        resp = self.client.post(
            f"/api/events/{self.event.slug}/sync-calendar",
            **self._auth(),
        )

        body = resp.json()
        self.assertEqual(body["calendar_sync_status"], "synced")
        self.assertEqual(body["google_event_id"], expected_id)
        mock_reschedule.assert_not_called()
        self.assertEqual(
            mock_google_request.call_args.kwargs["params"],
            {"sendUpdates": "none"},
        )

    def test_sync_calendar_rejects_cancelled_and_missing_events(self):
        missing = self.client.post(
            "/api/events/no-such-event/sync-calendar",
            **self._auth(),
        )
        self.assertEqual(missing.status_code, 404)

        self.event.status = "cancelled"
        self.event.save(update_fields=["status"])
        cancelled = self.client.post(
            f"/api/events/{self.event.slug}/sync-calendar",
            **self._auth(),
        )
        self.assertEqual(cancelled.status_code, 422)
