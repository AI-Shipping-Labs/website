"""API tests for attaching events to series + cadence-less collections (#1358).

Covers the two gaps the Book Club prototype hit:
- ``POST/PATCH /api/events`` accept ``event_series`` (pk/slug/null) and echo an
  ``event_series`` key, without renumbering the series or renaming the event.
- ``POST /api/event-series`` accepts ``cadence=none`` with no day/time, while a
  weekly series still requires them.
"""

import json
import uuid
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()


class EventSeriesAttachApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-1358@test.com", password="pw", is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="t1358")

        cls.collection = EventSeries.objects.create(
            name="Inference Engineering Book Club",
            slug="inference-engineering-book-club",
            cadence="none",
            day_of_week=None,
            start_time=None,
            timezone="Europe/Berlin",
            required_level=0,
        )
        cls.other_series = EventSeries.objects.create(
            name="Weekly Office Hours",
            slug="weekly-office-hours",
            cadence="weekly",
            day_of_week=1,
            start_time=time(17, 0),
            timezone="Europe/Berlin",
            required_level=0,
        )

        start = timezone.now() + timedelta(days=7)
        cls.studio_event = Event.objects.create(
            title="Book Club Kickoff",
            slug="book-club-kickoff",
            description="Kickoff",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="upcoming",
            origin="studio",
        )
        cls.github_event = Event.objects.create(
            title="Synced Event",
            slug="github-synced-1358",
            description="Synced",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="upcoming",
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/x.md",
            content_id=uuid.uuid4(),
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.staff_token.key}"}

    def _patch(self, slug, payload):
        return self.client.patch(
            f"/api/events/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _get(self, slug):
        return self.client.get(f"/api/events/{slug}", **self._auth())

    # --- serialize -------------------------------------------------------

    def test_unattached_event_serializes_event_series_null(self):
        body = self._get("book-club-kickoff").json()
        self.assertIn("event_series", body)
        self.assertIsNone(body["event_series"])

    def test_list_includes_event_series_key(self):
        self.studio_event.event_series = self.collection
        self.studio_event.save(update_fields=["event_series"])
        resp = self.client.get("/api/events", **self._auth())
        rows = {row["slug"]: row for row in resp.json()["events"]}
        self.assertEqual(
            rows["book-club-kickoff"]["event_series"],
            {
                "id": self.collection.id,
                "slug": self.collection.slug,
                "name": self.collection.name,
            },
        )

    # --- attach / detach -------------------------------------------------

    def test_attach_by_pk_returns_series_ref_and_null_position(self):
        resp = self._patch(
            "book-club-kickoff", {"event_series": self.collection.pk},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            body["event_series"],
            {
                "id": self.collection.id,
                "slug": self.collection.slug,
                "name": self.collection.name,
            },
        )
        self.assertIsNone(body["series_position"])
        self.studio_event.refresh_from_db()
        self.assertEqual(self.studio_event.event_series_id, self.collection.id)
        self.assertIsNone(self.studio_event.series_position)

    def test_attach_by_slug_string(self):
        resp = self._patch(
            "book-club-kickoff",
            {"event_series": "inference-engineering-book-club"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["event_series"]["slug"],
            "inference-engineering-book-club",
        )
        self.studio_event.refresh_from_db()
        self.assertEqual(self.studio_event.event_series_id, self.collection.id)

    def test_detach_via_null(self):
        self.studio_event.event_series = self.collection
        self.studio_event.save(update_fields=["event_series"])
        resp = self._patch("book-club-kickoff", {"event_series": None})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["event_series"])
        self.studio_event.refresh_from_db()
        self.assertIsNone(self.studio_event.event_series_id)

    def test_create_time_attach(self):
        start = timezone.now() + timedelta(days=3)
        resp = self.client.post(
            "/api/events",
            data=json.dumps({
                "title": "New Attached Event",
                "start_datetime": start.isoformat(),
                "event_series": self.collection.pk,
            }),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["event_series"]["id"], self.collection.id)
        self.assertIsNone(body["series_position"])

    def test_unknown_series_pk_returns_422_field_error_and_no_change(self):
        resp = self._patch("book-club-kickoff", {"event_series": 999999})
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("event_series", body["details"])
        self.studio_event.refresh_from_db()
        self.assertIsNone(self.studio_event.event_series_id)

    def test_unknown_series_slug_returns_422(self):
        resp = self._patch("book-club-kickoff", {"event_series": "no-such-slug"})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("event_series", resp.json()["details"])

    def test_attach_does_not_renumber_or_rewrite_title(self):
        original_title = self.studio_event.title
        original_auto = self.studio_event.title_is_auto
        resp = self._patch(
            "book-club-kickoff", {"event_series": self.collection.pk},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], original_title)
        self.studio_event.refresh_from_db()
        self.assertEqual(self.studio_event.title, original_title)
        self.assertIsNone(self.studio_event.series_position)
        self.assertEqual(self.studio_event.title_is_auto, original_auto)

    def test_synced_event_refuses_series_attach_with_409(self):
        resp = self._patch(
            "github-synced-1358", {"event_series": self.collection.pk},
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "synced_event_read_only")
        self.github_event.refresh_from_db()
        self.assertIsNone(self.github_event.event_series_id)


class EventSeriesCadenceApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-cadence-1358@test.com", password="pw", is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="tc1358")

    def _post(self, payload):
        return self.client.post(
            "/api/event-series",
            data=json.dumps(payload),
            content_type="application/json",
            **{"HTTP_AUTHORIZATION": f"Token {self.staff_token.key}"},
        )

    def test_create_cadence_none_without_day_or_time(self):
        resp = self._post({"name": "Book Club", "cadence": "none"})
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["cadence"], "none")
        self.assertIsNone(body["day_of_week"])
        self.assertIsNone(body["start_time"])
        series = EventSeries.objects.get(pk=body["id"])
        self.assertEqual(series.cadence, "none")
        self.assertIsNone(series.day_of_week)
        self.assertIsNone(series.start_time)

    def test_weekly_series_without_day_or_time_still_422(self):
        resp = self._post({"name": "Weekly OH", "cadence": "weekly"})
        self.assertEqual(resp.status_code, 422)
        details = resp.json()["details"]
        self.assertIn("day_of_week", details)
        self.assertIn("start_time", details)

    def test_default_cadence_still_requires_day_and_time(self):
        # No cadence supplied defaults to weekly, so day/time stay required.
        resp = self._post({"name": "Implicit Weekly"})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("day_of_week", resp.json()["details"])
