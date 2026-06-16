"""Tests for the EventSeries + bulk occurrences API (issue #678).

Contract:
- Series can be listed, created, fetched, patched. ``is_active`` flips
  visibility from the public listing.
- Occurrences are ``Event`` rows. Bulk POST is atomic; in-batch dupes
  return 422, DB-existing dupes are silently skipped so resubmits are
  idempotent. Cancel an occurrence via ``status='cancelled'``.
- NO ``DELETE`` is exposed on series or occurrences.
- Cancellation does NOT trigger any email side-effect in v1.
"""

import datetime as dt
import json
from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()


class EventSeriesApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-series@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-series@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="series")
        cls.non_staff_token = Token(
            key="non-staff-series-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.start = timezone.now().replace(
            microsecond=0,
        ) + timedelta(days=7)

        cls.series = EventSeries.objects.create(
            name="AI Eval Sprint",
            slug="ai-eval-sprint",
            description="Weekly live sessions",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
        )
        cls.hidden_series = EventSeries.objects.create(
            name="Archived Series",
            slug="archived-series",
            description="Old",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
            is_active=False,
        )
        # Two occurrences belonging to the active series for detail tests.
        cls.occurrence_1 = Event.objects.create(
            title="AI Eval Sprint — Session 1",
            slug="ai-eval-sprint-session-1",
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            status="upcoming",
            origin="studio",
            event_series=cls.series,
            series_position=1,
        )
        cls.occurrence_2 = Event.objects.create(
            title="AI Eval Sprint — Session 2",
            slug="ai-eval-sprint-session-2",
            start_datetime=cls.start + timedelta(days=7),
            end_datetime=cls.start + timedelta(days=7, hours=1),
            status="upcoming",
            origin="studio",
            event_series=cls.series,
            series_position=2,
        )
        # Independent event belonging to a different series (used in the
        # cross-series-404 test).
        cls.other_series = EventSeries.objects.create(
            name="Other Series",
            slug="other-series",
            cadence="weekly",
            day_of_week=3,
            start_time=time(17, 0),
            timezone="Europe/Berlin",
        )
        cls.other_occurrence = Event.objects.create(
            title="Other — Session 1",
            slug="other-session-1",
            start_datetime=cls.start + timedelta(days=1),
            end_datetime=cls.start + timedelta(days=1, hours=1),
            status="upcoming",
            origin="studio",
            event_series=cls.other_series,
            series_position=1,
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _get(self, path, *, token=None):
        return self.client.get(path, **self._auth(token))

    def _post(self, path, payload, *, token=None):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, path, payload, *, token=None):
        return self.client.patch(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )


class EventSeriesAuthTest(EventSeriesApiTestBase):
    def test_list_requires_token(self):
        response = self.client.get("/api/event-series")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(), {"error": "Authentication token required"},
        )

    def test_non_staff_token_returns_401(self):
        response = self.client.get(
            "/api/event-series",
            **self._auth(self.non_staff_token),
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_unsupported_method_returns_405(self):
        response = self.client.put(
            "/api/event-series",
            data=json.dumps({"name": "X"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)

    def test_bulk_requires_token(self):
        response = self.client.post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            data=json.dumps({"occurrences": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


class EventSeriesListTest(EventSeriesApiTestBase):
    def test_list_returns_canonical_shape(self):
        response = self._get("/api/event-series")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"event_series"})
        by_slug = {row["slug"]: row for row in body["event_series"]}
        self.assertIn("ai-eval-sprint", by_slug)
        self.assertIn("archived-series", by_slug)
        active = by_slug["ai-eval-sprint"]
        self.assertEqual(
            set(active.keys()),
            {
                "id",
                "name",
                "slug",
                "description",
                "cadence",
                "day_of_week",
                "start_time",
                "timezone",
                "required_level",
                "is_active",
                "event_count",
                "published_event_count",
                "zoom_meetings_last_run",
                "created_at",
                "updated_at",
            },
        )
        self.assertTrue(active["is_active"])
        self.assertEqual(active["event_count"], 2)

    def test_list_filters_by_is_active(self):
        response_true = self._get("/api/event-series?is_active=true")
        slugs_true = {r["slug"] for r in response_true.json()["event_series"]}
        self.assertIn("ai-eval-sprint", slugs_true)
        self.assertNotIn("archived-series", slugs_true)

        response_false = self._get("/api/event-series?is_active=false")
        slugs_false = {
            r["slug"] for r in response_false.json()["event_series"]
        }
        self.assertNotIn("ai-eval-sprint", slugs_false)
        self.assertIn("archived-series", slugs_false)

    def test_list_is_active_filter_accepts_tolerant_boolean_values(self):
        cases = (
            (" TRUE ", {"ai-eval-sprint", "other-series"}),
            ("1", {"ai-eval-sprint", "other-series"}),
            ("yes", {"ai-eval-sprint", "other-series"}),
            ("on", {"ai-eval-sprint", "other-series"}),
            (" FALSE ", {"archived-series"}),
            ("0", {"archived-series"}),
            ("no", {"archived-series"}),
            ("off", {"archived-series"}),
        )
        for value, expected_slugs in cases:
            with self.subTest(value=value):
                response = self.client.get(
                    "/api/event-series",
                    {"is_active": value},
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 200)
                slugs = {
                    r["slug"] for r in response.json()["event_series"]
                }
                self.assertEqual(slugs, expected_slugs)

    def test_list_filters_by_q(self):
        response = self._get("/api/event-series?q=Eval")
        slugs = {r["slug"] for r in response.json()["event_series"]}
        self.assertEqual(slugs, {"ai-eval-sprint"})


class EventSeriesCreateTest(EventSeriesApiTestBase):
    def test_create_series_happy_path(self):
        before = EventSeries.objects.count()
        response = self._post(
            "/api/event-series",
            {
                "name": "New Cohort",
                "description": "Weekly",
                "day_of_week": 1,
                "start_time": "19:30",
                "timezone": "UTC",
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "New Cohort")
        self.assertEqual(body["slug"], "new-cohort")
        self.assertEqual(body["day_of_week"], 1)
        self.assertEqual(body["start_time"], "19:30:00")
        self.assertEqual(body["timezone"], "UTC")
        self.assertTrue(body["is_active"])
        self.assertEqual(body["cadence"], "weekly")
        self.assertEqual(EventSeries.objects.count(), before + 1)

    def test_create_series_duplicate_slug(self):
        before = EventSeries.objects.count()
        response = self._post(
            "/api/event-series",
            {
                "name": "Conflict",
                "slug": "ai-eval-sprint",
                "day_of_week": 2,
                "start_time": "18:00",
            },
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("slug", body["details"])
        self.assertEqual(EventSeries.objects.count(), before)

    def test_create_series_validation_errors(self):
        response = self._post(
            "/api/event-series",
            {
                "name": "",
                "day_of_week": 99,
                "start_time": "bad",
                "cadence": "monthly",
            },
        )
        self.assertEqual(response.status_code, 422)
        details = response.json()["details"]
        for field in ("name", "day_of_week", "start_time", "cadence"):
            self.assertIn(field, details)

    def test_create_series_returns_400_on_non_object_body(self):
        response = self.client.post(
            "/api/event-series",
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")
        self.assertEqual(
            response.json()["details"],
            {"field": "body", "expected": "object"},
        )


class EventSeriesDetailTest(EventSeriesApiTestBase):
    def test_detail_inlines_occurrences_in_position_order(self):
        response = self._get(f"/api/event-series/{self.series.pk}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["slug"], "ai-eval-sprint")
        self.assertEqual(len(body["occurrences"]), 2)
        # serialize_event shape parity check on one row.
        first = body["occurrences"][0]
        self.assertEqual(first["slug"], "ai-eval-sprint-session-1")
        self.assertIn("editable", first)
        self.assertIn("origin", first)
        self.assertIn("start_datetime", first)
        # Position order: session 1 before session 2.
        self.assertEqual(
            [o["slug"] for o in body["occurrences"]],
            ["ai-eval-sprint-session-1", "ai-eval-sprint-session-2"],
        )

    def test_detail_unknown_series_returns_404(self):
        response = self._get("/api/event-series/999999")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_series")


class EventSeriesPatchTest(EventSeriesApiTestBase):
    def test_patch_hides_series_via_is_active(self):
        # Pre-state: occurrences exist and series is active.
        self.assertTrue(self.series.is_active)
        before_occurrence_count = self.series.events.count()
        before_occurrence_statuses = list(
            self.series.events.values_list("status", flat=True),
        )

        response = self._patch(
            f"/api/event-series/{self.series.pk}",
            {"is_active": False},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])
        self.series.refresh_from_db()
        self.assertFalse(self.series.is_active)
        # Occurrences untouched.
        self.assertEqual(self.series.events.count(), before_occurrence_count)
        self.assertEqual(
            list(self.series.events.values_list("status", flat=True)),
            before_occurrence_statuses,
        )

    def test_patch_updates_name_and_description(self):
        response = self._patch(
            f"/api/event-series/{self.series.pk}",
            {"name": "Renamed Series", "description": "Updated"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "Renamed Series")
        self.assertEqual(body["description"], "Updated")

    def test_patch_invalid_is_active_returns_422(self):
        response = self._patch(
            f"/api/event-series/{self.series.pk}",
            {"is_active": "yes"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("is_active", response.json()["details"])

    def test_patch_returns_400_on_non_object_body(self):
        response = self.client.patch(
            f"/api/event-series/{self.series.pk}",
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")
        self.assertEqual(
            response.json()["details"],
            {"field": "body", "expected": "object"},
        )


class EventSeriesDeleteTest(EventSeriesApiTestBase):
    def test_delete_series_returns_405(self):
        before = EventSeries.objects.count()
        response = self.client.delete(
            f"/api/event-series/{self.series.pk}",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json()["code"], "series_delete_not_available",
        )
        self.assertEqual(EventSeries.objects.count(), before)

    def test_delete_collection_returns_405(self):
        response = self.client.delete(
            "/api/event-series",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json()["code"], "series_delete_not_available",
        )


class EventSeriesBulkOccurrencesTest(EventSeriesApiTestBase):
    def _make_payload(self, count, *, base_offset_days=30):
        """Build N occurrences spaced 7 days apart, well past existing ones."""
        base = self.start + timedelta(days=base_offset_days)
        rows = []
        for i in range(count):
            start_dt = base + timedelta(days=7 * i)
            rows.append(
                {
                    "start_datetime": start_dt.isoformat(),
                    "end_datetime": (start_dt + timedelta(hours=1)).isoformat(),
                },
            )
        return {"occurrences": rows}

    def test_bulk_create_links_events_and_assigns_position(self):
        before = Event.objects.filter(event_series=self.series).count()
        payload = self._make_payload(3)
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            payload,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["created"], 3)
        self.assertEqual(body["skipped_existing"], 0)
        self.assertEqual(len(body["occurrence_ids"]), 3)

        created_events = Event.objects.filter(
            id__in=body["occurrence_ids"],
        ).order_by("series_position")
        self.assertEqual(
            list(created_events.values_list("event_series_id", flat=True)),
            [self.series.pk] * 3,
        )
        # series_position continues past the existing max (2 -> 3, 4, 5).
        self.assertEqual(
            list(created_events.values_list("series_position", flat=True)),
            [3, 4, 5],
        )
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(),
            before + 3,
        )

    def test_bulk_irregular_dates_create_three_positioned_occurrences(self):
        # Issue #854 Part C regression: an arbitrary irregular batch (no
        # fixed weekly cadence — different gaps and weekdays) produces three
        # occurrences at the next sequential positions n, n+1, n+2. Existing
        # max position is 2, so the new ones are 3, 4, 5.
        d1 = self.start + timedelta(days=40)  # arbitrary
        d2 = self.start + timedelta(days=43)  # 3-day gap, different weekday
        d3 = self.start + timedelta(days=58)  # 15-day gap
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            {
                "occurrences": [
                    {"start_datetime": d1.isoformat()},
                    {"start_datetime": d2.isoformat()},
                    {"start_datetime": d3.isoformat()},
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["created"], 3)
        created = list(
            Event.objects.filter(id__in=body["occurrence_ids"])
            .order_by("series_position"),
        )
        self.assertEqual(
            [e.series_position for e in created], [3, 4, 5],
        )
        # The irregular start datetimes survive unchanged (sorted by position).
        self.assertEqual(
            [e.start_datetime for e in created], [d1, d2, d3],
        )

    def test_bulk_atomic_rollback_on_per_row_error(self):
        before = Event.objects.filter(event_series=self.series).count()
        good_start = self.start + timedelta(days=60)
        another_good = self.start + timedelta(days=67)
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            {
                "occurrences": [
                    {"start_datetime": good_start.isoformat()},
                    {"start_datetime": "definitely-not-a-datetime"},
                    {"start_datetime": another_good.isoformat()},
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["index"], 1)
        # Atomic: zero rows created, including row 0 which would have
        # succeeded on its own.
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(),
            before,
        )

    def test_bulk_idempotent_against_db(self):
        payload = self._make_payload(3, base_offset_days=90)
        first = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            payload,
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(first.json()["created"], 3)
        before_second = Event.objects.filter(event_series=self.series).count()

        second = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            payload,
        )
        self.assertEqual(second.status_code, 201)
        body = second.json()
        self.assertEqual(body["created"], 0)
        self.assertEqual(body["skipped_existing"], 3)
        self.assertEqual(body["occurrence_ids"], [])
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(),
            before_second,
        )

    def test_bulk_duplicate_in_batch(self):
        before = Event.objects.filter(event_series=self.series).count()
        # Two rows that round to the same minute.
        same_minute_a = datetime(
            2030, 1, 1, 18, 0, 5, tzinfo=dt.timezone.utc,
        )
        same_minute_b = datetime(
            2030, 1, 1, 18, 0, 42, tzinfo=dt.timezone.utc,
        )
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            {
                "occurrences": [
                    {"start_datetime": same_minute_a.isoformat()},
                    {"start_datetime": same_minute_b.isoformat()},
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "duplicate_in_batch")
        self.assertEqual(body["details"]["indexes"], [0, 1])
        # No rows created.
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(),
            before,
        )

    def test_bulk_unknown_series_returns_404(self):
        response = self._post(
            "/api/event-series/999999/occurrences/bulk",
            {"occurrences": []},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_series")

    def test_bulk_missing_occurrences_key_returns_400(self):
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            {},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "missing_field")
        self.assertEqual(
            response.json()["details"]["field"], "occurrences",
        )


class EventSeriesOccurrencePatchTest(EventSeriesApiTestBase):
    def test_patch_occurrence_cancel_does_not_send_email(self):
        path = (
            f"/api/event-series/{self.series.pk}"
            f"/occurrences/{self.occurrence_1.pk}"
        )
        # Cover any service module the events app might gain in the
        # future for cancellation emails. The current contract is
        # "no side-effect"; we use ANY module path to make sure a
        # future side-effect can't be added silently.
        with patch(
            "django.core.mail.send_mail",
        ) as send_mail_mock:
            response = self._patch(path, {"status": "cancelled"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.occurrence_1.refresh_from_db()
        self.assertEqual(self.occurrence_1.status, "cancelled")
        self.assertEqual(send_mail_mock.call_count, 0)

    def test_patch_occurrence_404_when_not_in_series(self):
        path = (
            f"/api/event-series/{self.series.pk}"
            f"/occurrences/{self.other_occurrence.pk}"
        )
        response = self._patch(path, {"status": "cancelled"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_event")
        # Cross-series row not mutated.
        self.other_occurrence.refresh_from_db()
        self.assertEqual(self.other_occurrence.status, "upcoming")

    def test_patch_occurrence_updates_writable_fields(self):
        path = (
            f"/api/event-series/{self.series.pk}"
            f"/occurrences/{self.occurrence_1.pk}"
        )
        # Issue #958: ``required_level`` is now guarded against the series
        # level, so a writable-field PATCH must keep it equal to the series
        # level (the base series defaults to 0). A differing value is
        # rejected — that boundary is covered in the level-guardrail tests.
        response = self._patch(
            path,
            {"title": "Renamed Occurrence", "required_level": 0},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "Renamed Occurrence")
        self.assertEqual(body["required_level"], 0)

    def test_delete_occurrence_returns_405(self):
        path = (
            f"/api/event-series/{self.series.pk}"
            f"/occurrences/{self.occurrence_1.pk}"
        )
        response = self.client.delete(path, **self._auth())
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json()["code"], "occurrence_delete_not_available",
        )
        # Row not deleted.
        self.assertTrue(
            Event.objects.filter(pk=self.occurrence_1.pk).exists(),
        )

    def test_patch_unknown_series_returns_404_with_unknown_series(self):
        path = (
            f"/api/event-series/999999"
            f"/occurrences/{self.occurrence_1.pk}"
        )
        response = self._patch(path, {"status": "cancelled"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_series")


class EventSeriesUrlOrderingTest(EventSeriesApiTestBase):
    def test_bulk_url_resolves_before_int_capture(self):
        """The ``occurrences/bulk`` literal must not be swallowed by
        ``occurrences/<int:occurrence_id>``."""
        from django.urls import resolve

        match = resolve(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
        )
        self.assertEqual(
            match.url_name, "api_event_series_occurrences_bulk",
        )


class EventSeriesChronologicalNamingTest(EventSeriesApiTestBase):
    """Issue #876: chronological numbering + non-stale auto-titles."""

    def setUp(self):
        # A clean series with no pre-existing occurrences so positions are
        # unambiguous (the shared fixture series already has Sessions 1-2).
        self.fresh = EventSeries.objects.create(
            name="LLM Zoomcamp office hours",
            slug="llm-zoomcamp-office-hours",
            cadence="weekly",
            day_of_week=1,
            start_time=time(17, 0),
            timezone="Europe/Berlin",
        )
        self.base = timezone.now().replace(microsecond=0) + timedelta(days=10)

    def _dt(self, days):
        return (self.base + timedelta(days=days)).isoformat()

    def _bulk(self, rows):
        return self._post(
            f"/api/event-series/{self.fresh.pk}/occurrences/bulk",
            {"occurrences": rows},
        )

    def test_out_of_order_bulk_creates_chronological_positions(self):
        # Submit six dates shuffled; positions must follow date order.
        day_offsets = [2, 11, 16, 23, 38, 51]
        shuffled = [day_offsets[i] for i in (2, 0, 4, 3, 1, 5)]
        resp = self._bulk([{"start_datetime": self._dt(d)} for d in shuffled])
        self.assertEqual(resp.status_code, 201)

        occ = list(
            Event.objects.filter(event_series=self.fresh).order_by(
                "start_datetime",
            )
        )
        self.assertEqual(
            [e.series_position for e in occ], [1, 2, 3, 4, 5, 6],
        )
        # Each auto-title's trailing "Session N" equals its position.
        for event in occ:
            self.assertTrue(
                event.title.endswith(f"Session {event.series_position}"),
                event.title,
            )
            self.assertEqual(
                event.title,
                f"LLM Zoomcamp office hours — Session "
                f"{event.series_position}",
            )

    def test_serialize_event_includes_series_position(self):
        self._bulk([{"start_datetime": self._dt(2)}])
        occ = Event.objects.get(event_series=self.fresh)
        detail = self._get(
            f"/api/event-series/{self.fresh.pk}/occurrences/{occ.pk}",
        )
        self.assertEqual(detail.json()["series_position"], 1)
        # A standalone (non-series) event serializes series_position null.
        standalone = Event.objects.create(
            title="Standalone",
            slug="standalone-evt-876",
            start_datetime=self.base,
            end_datetime=self.base + timedelta(hours=1),
            status="upcoming",
            origin="studio",
        )
        resp = self._get(f"/api/events/{standalone.slug}")
        self.assertIsNone(resp.json()["series_position"])

    def test_appending_later_occurrence_keeps_existing_numbers(self):
        self._bulk(
            [
                {"start_datetime": self._dt(2)},
                {"start_datetime": self._dt(9)},
                {"start_datetime": self._dt(16)},
            ],
        )
        before = {
            e.pk: e.series_position
            for e in Event.objects.filter(event_series=self.fresh)
        }
        # Add a strictly later date.
        self._bulk([{"start_datetime": self._dt(30)}])
        after = {
            e.pk: e.series_position
            for e in Event.objects.filter(event_series=self.fresh)
        }
        # The three originals keep their positions; the new one is 4.
        for pk, pos in before.items():
            self.assertEqual(after[pk], pos)
        newest = Event.objects.filter(event_series=self.fresh).order_by(
            "-start_datetime",
        ).first()
        self.assertEqual(newest.series_position, 4)

    def test_inserting_earlier_occurrence_renumbers_and_retitles(self):
        self._bulk(
            [
                {"start_datetime": self._dt(2)},
                {"start_datetime": self._dt(16)},
                {"start_datetime": self._dt(30)},
            ],
        )
        # Insert a date between the first two (Session 2 slot).
        self._bulk([{"start_datetime": self._dt(9)}])
        occ = list(
            Event.objects.filter(event_series=self.fresh).order_by(
                "start_datetime",
            )
        )
        self.assertEqual([e.series_position for e in occ], [1, 2, 3, 4])
        # The inserted date sits at position 2.
        inserted = Event.objects.get(
            event_series=self.fresh,
            start_datetime=self.base + timedelta(days=9),
        )
        self.assertEqual(inserted.series_position, 2)
        # Every auto-title number matches the new chronological position.
        for event in occ:
            self.assertTrue(
                event.title.endswith(f"Session {event.series_position}"),
                event.title,
            )

    def test_series_rename_rewrites_auto_titles_only(self):
        self._bulk(
            [
                {"start_datetime": self._dt(2)},
                {"start_datetime": self._dt(9)},
                {
                    "start_datetime": self._dt(16),
                    "title": "Kickoff special",
                },
            ],
        )
        operator = Event.objects.get(title="Kickoff special")
        slugs_before = {
            e.pk: e.slug
            for e in Event.objects.filter(event_series=self.fresh)
        }

        resp = self._patch(
            f"/api/event-series/{self.fresh.pk}",
            {"name": "LLM Zoomcamp 2026 office hours"},
        )
        self.assertEqual(resp.status_code, 200)

        autos = Event.objects.filter(
            event_series=self.fresh, title_is_auto=True,
        )
        for event in autos:
            self.assertTrue(
                event.title.startswith("LLM Zoomcamp 2026 office hours — "),
                event.title,
            )
        # No occurrence retains the old name.
        self.assertFalse(
            Event.objects.filter(
                event_series=self.fresh,
                title__contains="LLM Zoomcamp office hours",
            ).exists(),
        )
        # The operator title is untouched.
        operator.refresh_from_db()
        self.assertEqual(operator.title, "Kickoff special")
        self.assertFalse(operator.title_is_auto)
        # All slugs are byte-for-byte unchanged.
        slugs_after = {
            e.pk: e.slug
            for e in Event.objects.filter(event_series=self.fresh)
        }
        self.assertEqual(slugs_before, slugs_after)

    def test_explicit_title_at_create_is_not_auto(self):
        self._bulk(
            [{"start_datetime": self._dt(2), "title": "Bring-your-own demo"}],
        )
        event = Event.objects.get(event_series=self.fresh)
        self.assertEqual(event.title, "Bring-your-own demo")
        self.assertFalse(event.title_is_auto)

    def test_occurrence_patch_title_freezes_against_rename(self):
        self._bulk(
            [
                {"start_datetime": self._dt(2)},
                {"start_datetime": self._dt(9)},
            ],
        )
        target = Event.objects.filter(event_series=self.fresh).order_by(
            "start_datetime",
        ).first()
        patch_resp = self._patch(
            f"/api/event-series/{self.fresh.pk}/occurrences/{target.pk}",
            {"title": "Custom session name"},
        )
        self.assertEqual(patch_resp.status_code, 200)
        target.refresh_from_db()
        self.assertFalse(target.title_is_auto)

        # A later series rename must not touch the custom title.
        self._patch(
            f"/api/event-series/{self.fresh.pk}",
            {"name": "Renamed Series"},
        )
        target.refresh_from_db()
        self.assertEqual(target.title, "Custom session name")

    def test_occurrence_patch_start_datetime_renumbers(self):
        self._bulk(
            [
                {"start_datetime": self._dt(2)},
                {"start_datetime": self._dt(9)},
                {"start_datetime": self._dt(16)},
            ],
        )
        # Move the earliest (position 1) to after the last → it becomes 3.
        first = Event.objects.filter(event_series=self.fresh).order_by(
            "start_datetime",
        ).first()
        new_start = self.base + timedelta(days=40)
        resp = self._patch(
            f"/api/event-series/{self.fresh.pk}/occurrences/{first.pk}",
            {
                "start_datetime": new_start.isoformat(),
                "end_datetime": (
                    new_start + timedelta(hours=1)
                ).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["series_position"], 3)
        occ = list(
            Event.objects.filter(event_series=self.fresh).order_by(
                "start_datetime",
            )
        )
        self.assertEqual([e.series_position for e in occ], [1, 2, 3])

    def test_series_get_occurrences_contiguous_and_titled(self):
        self._bulk(
            [
                {"start_datetime": self._dt(16)},
                {"start_datetime": self._dt(2)},
                {"start_datetime": self._dt(9)},
            ],
        )
        body = self._get(f"/api/event-series/{self.fresh.pk}").json()
        positions = [o["series_position"] for o in body["occurrences"]]
        self.assertEqual(positions, [1, 2, 3])
        for occ in body["occurrences"]:
            self.assertTrue(
                occ["title"].endswith(f"Session {occ['series_position']}"),
                occ["title"],
            )


class TitleIsAutoBackfillMigrationTest(TestCase):
    """Issue #876: the data migration classifies legacy titles correctly."""

    def test_backfill_classifies_existing_titles(self):
        from importlib import import_module

        from django.apps import apps as global_apps

        series = EventSeries.objects.create(
            name="Migration Series",
            slug="migration-series-876",
            cadence="weekly",
            day_of_week=1,
            start_time=time(17, 0),
            timezone="Europe/Berlin",
        )
        base = timezone.now().replace(microsecond=0) + timedelta(days=5)
        auto = Event.objects.create(
            title="Migration Series — Session 5",
            slug="migration-session-5",
            start_datetime=base,
            end_datetime=base + timedelta(hours=1),
            status="upcoming",
            origin="studio",
            event_series=series,
            series_position=5,
        )
        operator = Event.objects.create(
            title="Kickoff special",
            slug="migration-kickoff",
            start_datetime=base + timedelta(days=1),
            end_datetime=base + timedelta(days=1, hours=1),
            status="upcoming",
            origin="studio",
            event_series=series,
            series_position=1,
        )
        # Both default to title_is_auto=True; the backfill should only flip
        # the operator-named row to False.
        Event.objects.filter(
            pk__in=[auto.pk, operator.pk],
        ).update(title_is_auto=True)

        migration = import_module(
            "events.migrations.0031_backfill_title_is_auto",
        )
        migration.backfill(global_apps, None)

        auto.refresh_from_db()
        operator.refresh_from_db()
        self.assertTrue(auto.title_is_auto)
        self.assertFalse(operator.title_is_auto)
