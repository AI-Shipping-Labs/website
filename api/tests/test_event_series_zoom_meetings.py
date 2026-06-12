"""Tests for the bulk "create Zoom meetings for a series" API (issue #932).

Contract:
- ``POST /api/event-series/<id>/zoom-meetings`` enqueues the SAME idempotent
  background job the Studio button uses (#859). It never adds a second Zoom
  or eligibility code path.
- Auth is the staff token (``@token_required``); no/invalid token -> 401.
- POST-only (``@require_methods("POST")``); GET / PATCH / PUT / DELETE -> 405.
  There is no delete/remove counterpart (no-deletes-via-API policy).
- ``dry_run`` previews the eligible count without enqueuing or calling Zoom.
- Eligible occurrence(s) -> 202 ``status="enqueued"`` with ``eligible_count``
  and ``task_id``. Nothing eligible -> 200 ``status="noop"``.
- Missing series -> 404; non-object body -> 422; unknown keys ignored.
- The run result is surfaced read-only via ``zoom_meetings_last_run`` on the
  series detail endpoint.
"""

import json
from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()

ENQUEUE_PATH = (
    "api.views.event_series.enqueue_create_series_zoom_meetings"
)
TASK_CREATE_MEETING_PATH = (
    "events.tasks.create_series_zoom_meetings.create_meeting"
)


class ZoomMeetingsApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-zoom@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-zoom@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="zoom")
        cls.non_staff_token = Token(
            key="non-staff-zoom-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.future = timezone.now().replace(microsecond=0) + timedelta(days=7)
        cls.past = timezone.now().replace(microsecond=0) - timedelta(days=7)

    def setUp(self):
        # Each test gets a fresh series so eligibility mutations from one
        # test do not leak into another.
        self.series = EventSeries.objects.create(
            name="AI Eval Sprint",
            slug=f"ai-eval-sprint-{self._testMethodName}",
            description="Weekly live sessions",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
        )

    def _occurrence(self, *, position, start, status="upcoming",
                    platform="zoom", zoom_meeting_id="", series=None):
        series = series or self.series
        return Event.objects.create(
            title=f"{series.name} — Session {position}",
            slug=f"{series.slug}-session-{position}",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status=status,
            platform=platform,
            zoom_meeting_id=zoom_meeting_id,
            origin="studio",
            event_series=series,
            series_position=position,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, path, payload=None, *, token=None, raw=None):
        kwargs = self._auth(token)
        if raw is not None:
            return self.client.post(
                path, data=raw, content_type="application/json", **kwargs,
            )
        if payload is None:
            return self.client.post(path, content_type="application/json",
                                     **kwargs)
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **kwargs,
        )

    def _url(self, series=None):
        series = series or self.series
        return f"/api/event-series/{series.pk}/zoom-meetings"


class ZoomMeetingsEnqueueTest(ZoomMeetingsApiTestBase):
    def test_eligible_occurrences_enqueue_returns_202(self):
        # 3 future Zoom occurrences with no meeting id + 1 past occurrence.
        self._occurrence(position=1, start=self.future + timedelta(days=0))
        self._occurrence(position=2, start=self.future + timedelta(days=7))
        self._occurrence(position=3, start=self.future + timedelta(days=14))
        self._occurrence(position=4, start=self.past)

        with patch(ENQUEUE_PATH, return_value="task-abc123") as enqueue:
            response = self._post(self._url())

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["status"], "enqueued")
        self.assertEqual(body["eligible_count"], 3)
        self.assertEqual(body["task_id"], "task-abc123")
        self.assertEqual(body["series_id"], self.series.pk)
        enqueue.assert_called_once_with(self.series.pk)

    def test_unknown_extra_keys_are_ignored(self):
        self._occurrence(position=1, start=self.future)

        with patch(ENQUEUE_PATH, return_value="task-xyz") as enqueue:
            response = self._post(
                self._url(),
                {"dry_run": False, "unexpected_key": 1},
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["eligible_count"], 1)
        enqueue.assert_called_once_with(self.series.pk)

    def test_empty_body_enqueues(self):
        self._occurrence(position=1, start=self.future)
        with patch(ENQUEUE_PATH, return_value="t") as enqueue:
            response = self._post(self._url())
        self.assertEqual(response.status_code, 202)
        enqueue.assert_called_once_with(self.series.pk)

    def test_run_result_surfaced_via_series_detail(self):
        # Reuse the real task to produce the persisted last-run summary,
        # then assert the API detail endpoint exposes it read-only.
        occ1 = self._occurrence(position=1, start=self.future)
        occ2 = self._occurrence(position=2, start=self.future + timedelta(days=7))
        self._occurrence(position=3, start=self.past)  # past -> ineligible

        from events.tasks.create_series_zoom_meetings import (
            create_series_zoom_meetings,
        )

        def fake_create(event):
            return {
                "meeting_id": f"zoom-{event.pk}",
                "join_url": f"https://zoom.us/j/{event.pk}",
            }

        with patch(TASK_CREATE_MEETING_PATH, side_effect=fake_create):
            create_series_zoom_meetings(self.series.pk)

        response = self.client.get(
            f"/api/event-series/{self.series.pk}", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        last_run = response.json()["zoom_meetings_last_run"]
        self.assertIsNotNone(last_run)
        self.assertCountEqual(last_run["created"], [occ1.pk, occ2.pk])
        self.assertEqual(last_run["failed"], [])


class ZoomMeetingsDryRunTest(ZoomMeetingsApiTestBase):
    def test_dry_run_returns_count_without_enqueue(self):
        self._occurrence(position=1, start=self.future)
        self._occurrence(position=2, start=self.future + timedelta(days=7))

        with patch(ENQUEUE_PATH) as enqueue:
            response = self._post(self._url(), {"dry_run": True})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["eligible_count"], 2)
        self.assertEqual(body["series_id"], self.series.pk)
        enqueue.assert_not_called()

    def test_dry_run_non_boolean_returns_422(self):
        response = self._post(self._url(), {"dry_run": "yes"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_type")


class ZoomMeetingsNoopTest(ZoomMeetingsApiTestBase):
    def test_no_eligible_returns_noop_without_enqueue(self):
        # Every future Zoom occurrence already has a meeting id.
        self._occurrence(position=1, start=self.future,
                         zoom_meeting_id="zoom-1")
        self._occurrence(position=2, start=self.future + timedelta(days=7),
                         zoom_meeting_id="zoom-2")

        with patch(ENQUEUE_PATH) as enqueue:
            response = self._post(self._url())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "noop")
        self.assertEqual(body["eligible_count"], 0)
        enqueue.assert_not_called()

    def test_idempotent_second_post_returns_noop(self):
        occ = self._occurrence(position=1, start=self.future)

        # First POST enqueues (mocked); simulate the job assigning a meeting
        # id so the occurrence is no longer eligible.
        with patch(ENQUEUE_PATH, return_value="t") as enqueue:
            first = self._post(self._url())
        self.assertEqual(first.status_code, 202)
        enqueue.assert_called_once_with(self.series.pk)

        occ.zoom_meeting_id = "zoom-assigned"
        occ.save(update_fields=["zoom_meeting_id"])

        # Second POST: nothing eligible -> noop, no enqueue, id unchanged.
        with patch(ENQUEUE_PATH) as enqueue2:
            second = self._post(self._url())
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "noop")
        enqueue2.assert_not_called()
        occ.refresh_from_db()
        self.assertEqual(occ.zoom_meeting_id, "zoom-assigned")


class ZoomMeetingsPartialFailureTest(ZoomMeetingsApiTestBase):
    def test_single_zoom_failure_does_not_abort_batch(self):
        occ1 = self._occurrence(position=1, start=self.future)
        occ2 = self._occurrence(position=2, start=self.future + timedelta(days=7))
        occ3 = self._occurrence(position=3, start=self.future + timedelta(days=14))

        from events.tasks.create_series_zoom_meetings import (
            create_series_zoom_meetings,
        )

        def flaky_create(event):
            if event.pk == occ2.pk:
                raise RuntimeError("Zoom 429 rate limited")
            return {
                "meeting_id": f"zoom-{event.pk}",
                "join_url": f"https://zoom.us/j/{event.pk}",
            }

        with patch(TASK_CREATE_MEETING_PATH, side_effect=flaky_create):
            create_series_zoom_meetings(self.series.pk)

        response = self.client.get(
            f"/api/event-series/{self.series.pk}", **self._auth(),
        )
        last_run = response.json()["zoom_meetings_last_run"]
        self.assertCountEqual(last_run["created"], [occ1.pk, occ3.pk])
        self.assertEqual(len(last_run["failed"]), 1)
        self.assertEqual(last_run["failed"][0]["event_id"], occ2.pk)
        self.assertIn("429", last_run["failed"][0]["error"])


class ZoomMeetingsAuthTest(ZoomMeetingsApiTestBase):
    def test_no_token_returns_401(self):
        with patch(ENQUEUE_PATH) as enqueue:
            response = self.client.post(
                self._url(), content_type="application/json",
            )
        self.assertEqual(response.status_code, 401)
        enqueue.assert_not_called()

    def test_non_staff_token_returns_401(self):
        with patch(ENQUEUE_PATH) as enqueue:
            response = self._post(self._url(), token=self.non_staff_token)
        self.assertEqual(response.status_code, 401)
        enqueue.assert_not_called()


class ZoomMeetingsNotFoundTest(ZoomMeetingsApiTestBase):
    def test_missing_series_returns_404(self):
        response = self._post("/api/event-series/999999/zoom-meetings")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["code"], "unknown_series")
        self.assertIn("error", body)


class ZoomMeetingsMethodTest(ZoomMeetingsApiTestBase):
    def test_get_returns_405(self):
        response = self.client.get(self._url(), **self._auth())
        self.assertEqual(response.status_code, 405)

    def test_delete_returns_405(self):
        response = self.client.delete(self._url(), **self._auth())
        self.assertEqual(response.status_code, 405)

    def test_put_returns_405(self):
        response = self.client.put(
            self._url(), content_type="application/json", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)

    def test_patch_returns_405(self):
        response = self.client.patch(
            self._url(), content_type="application/json", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)


class ZoomMeetingsBadBodyTest(ZoomMeetingsApiTestBase):
    def test_json_array_body_returns_422(self):
        with patch(ENQUEUE_PATH) as enqueue:
            response = self._post(
                self._url(), raw=json.dumps(["not", "an", "object"]),
            )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "invalid_type")
        self.assertIn("JSON object", body["error"])
        enqueue.assert_not_called()

    def test_json_string_body_returns_422(self):
        with patch(ENQUEUE_PATH) as enqueue:
            response = self._post(self._url(), raw=json.dumps("hello"))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_type")
        enqueue.assert_not_called()

    def test_invalid_json_returns_400(self):
        with patch(ENQUEUE_PATH) as enqueue:
            response = self._post(self._url(), raw="{not json")
        self.assertEqual(response.status_code, 400)
        enqueue.assert_not_called()
