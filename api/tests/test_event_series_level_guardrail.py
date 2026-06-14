"""Tests for the occurrence/series access-level guardrail (issue #958).

The API forbids any write that would leave an occurrence at a level
different from its series' ``required_level``. There is NO override flag —
the legitimate "first session free, rest paid" case lives in Studio only.
Inheritance: an occurrence write that omits ``required_level`` inherits the
series level. Changing the series' own level never rewrites existing
occurrences.
"""

import json
from datetime import time, timedelta

from django.utils import timezone

from api.tests.test_event_series import EventSeriesApiTestBase
from events.models import Event, EventSeries


class _LevelBase(EventSeriesApiTestBase):
    """Adds a Main-gated and a Free series with no occurrences."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.main_series = EventSeries.objects.create(
            name="Main Gated Series",
            slug="main-gated-series",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
            required_level=20,
        )
        cls.free_series = EventSeries.objects.create(
            name="Free Series",
            slug="free-series",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
            required_level=0,
        )

    def _bulk_url(self, series):
        return f"/api/event-series/{series.id}/occurrences/bulk"

    def _reconcile_url(self, series):
        return f"/api/event-series/{series.id}/occurrences"

    def _occ_url(self, series, occ):
        return f"/api/event-series/{series.id}/occurrences/{occ.id}"


class InheritanceTest(_LevelBase):
    def test_bulk_row_omitting_level_inherits_series_level(self):
        start = timezone.now().replace(microsecond=0) + timedelta(days=30)
        response = self._post(
            self._bulk_url(self.main_series),
            {
                "occurrences": [
                    {"start_datetime": start.isoformat()},
                    {
                        "start_datetime": (
                            start + timedelta(days=7)
                        ).isoformat(),
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        created = Event.objects.filter(event_series=self.main_series)
        self.assertEqual(created.count(), 2)
        for event in created:
            self.assertEqual(event.required_level, 20)


class BulkRejectTest(_LevelBase):
    def test_lower_than_series_rejected_and_rolls_back(self):
        start = timezone.now().replace(microsecond=0) + timedelta(days=30)
        response = self._post(
            self._bulk_url(self.main_series),
            {
                "occurrences": [
                    {
                        "start_datetime": start.isoformat(),
                        "required_level": 0,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "level_mismatch")
        self.assertEqual(body["details"]["index"], 0)
        self.assertEqual(body["details"]["occurrence_required_level"], 0)
        self.assertEqual(body["details"]["series_required_level"], 20)
        # Atomic rollback — zero occurrences created.
        self.assertEqual(
            Event.objects.filter(event_series=self.main_series).count(), 0,
        )

    def test_higher_than_series_also_rejected(self):
        start = timezone.now().replace(microsecond=0) + timedelta(days=30)
        response = self._post(
            self._bulk_url(self.free_series),
            {
                "occurrences": [
                    {
                        "start_datetime": start.isoformat(),
                        "required_level": 20,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "level_mismatch")
        self.assertEqual(body["details"]["occurrence_required_level"], 20)
        self.assertEqual(body["details"]["series_required_level"], 0)
        self.assertEqual(
            Event.objects.filter(event_series=self.free_series).count(), 0,
        )

    def test_mismatch_in_later_row_rolls_back_earlier_rows(self):
        start = timezone.now().replace(microsecond=0) + timedelta(days=30)
        response = self._post(
            self._bulk_url(self.main_series),
            {
                "occurrences": [
                    {"start_datetime": start.isoformat()},
                    {
                        "start_datetime": (
                            start + timedelta(days=7)
                        ).isoformat(),
                        "required_level": 0,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["details"]["index"], 1)
        self.assertEqual(
            Event.objects.filter(event_series=self.main_series).count(), 0,
        )


class BulkAllowTest(_LevelBase):
    def test_mix_of_omitted_and_matching_explicit_levels_accepted(self):
        start = timezone.now().replace(microsecond=0) + timedelta(days=30)
        response = self._post(
            self._bulk_url(self.main_series),
            {
                "occurrences": [
                    {"start_datetime": start.isoformat()},
                    {
                        "start_datetime": (
                            start + timedelta(days=7)
                        ).isoformat(),
                        "required_level": 20,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        created = Event.objects.filter(event_series=self.main_series)
        self.assertEqual(created.count(), 2)
        for event in created:
            self.assertEqual(event.required_level, 20)


class SinglePatchLevelTest(_LevelBase):
    def setUp(self):
        self.occ = Event.objects.create(
            title="Main Gated Series — Session 1",
            slug="main-gated-series-session-1",
            start_datetime=(
                timezone.now().replace(microsecond=0) + timedelta(days=30)
            ),
            end_datetime=(
                timezone.now().replace(microsecond=0)
                + timedelta(days=30, hours=1)
            ),
            status="upcoming",
            origin="studio",
            event_series=self.main_series,
            series_position=1,
            required_level=20,
        )

    def test_mismatch_patch_rejected_no_index_level_unchanged(self):
        response = self._patch(
            self._occ_url(self.main_series, self.occ),
            {"required_level": 0},
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "level_mismatch")
        self.assertNotIn("index", body["details"])
        self.assertEqual(body["details"]["occurrence_required_level"], 0)
        self.assertEqual(body["details"]["series_required_level"], 20)
        self.occ.refresh_from_db()
        self.assertEqual(self.occ.required_level, 20)

    def test_matching_patch_accepted(self):
        response = self._patch(
            self._occ_url(self.main_series, self.occ),
            {"required_level": 20},
        )
        self.assertEqual(response.status_code, 200)
        self.occ.refresh_from_db()
        self.assertEqual(self.occ.required_level, 20)


class SeriesLevelCreatePatchTest(_LevelBase):
    def test_create_serializes_level_and_patch_does_not_rewrite_occurrences(
        self,
    ):
        response = self._post(
            "/api/event-series",
            {
                "name": "New Gated Series",
                "day_of_week": 2,
                "start_time": "18:00",
                "required_level": 20,
            },
        )
        self.assertEqual(response.status_code, 201)
        series_id = response.json()["id"]
        self.assertEqual(response.json()["required_level"], 20)

        get_resp = self._get(f"/api/event-series/{series_id}")
        self.assertEqual(get_resp.json()["required_level"], 20)

        # Create an occurrence at level 20 (matches the series).
        series = EventSeries.objects.get(pk=series_id)
        occ = Event.objects.create(
            title="x",
            slug="x-new-gated",
            start_datetime=(
                timezone.now().replace(microsecond=0) + timedelta(days=30)
            ),
            end_datetime=(
                timezone.now().replace(microsecond=0)
                + timedelta(days=30, hours=1)
            ),
            status="upcoming",
            origin="studio",
            event_series=series,
            series_position=1,
            required_level=20,
        )

        # Change the series level — existing occurrence must keep level 20.
        patch_resp = self._patch(
            f"/api/event-series/{series_id}",
            {"required_level": 10},
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()["required_level"], 10)
        get_resp2 = self._get(f"/api/event-series/{series_id}")
        self.assertEqual(get_resp2.json()["required_level"], 10)
        occ.refresh_from_db()
        self.assertEqual(occ.required_level, 20)

    def test_unknown_level_rejected_on_create(self):
        response = self._post(
            "/api/event-series",
            {
                "name": "Bad Level Series",
                "day_of_week": 2,
                "start_time": "18:00",
                "required_level": 999,
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("required_level", response.json()["details"])


class ReconcileLevelTest(_LevelBase):
    def setUp(self):
        base = timezone.now().replace(microsecond=0) + timedelta(days=40)
        self.active_occ = Event.objects.create(
            title="Main Gated Series — Session 1",
            slug="main-gated-series-r-1",
            start_datetime=base,
            end_datetime=base + timedelta(hours=1),
            status="upcoming",
            origin="studio",
            event_series=self.main_series,
            series_position=1,
            required_level=20,
        )
        self.cancelled_occ = Event.objects.create(
            title="Main Gated Series — Session 2",
            slug="main-gated-series-r-2",
            start_datetime=base + timedelta(days=7),
            end_datetime=base + timedelta(days=7, hours=1),
            status="cancelled",
            origin="studio",
            event_series=self.main_series,
            series_position=2,
            required_level=0,
        )
        self.base = base

    def _reconcile(self, rows):
        return self.client.put(
            self._reconcile_url(self.main_series),
            data=json.dumps({"occurrences": rows}),
            content_type="application/json",
            **self._auth(),
        )

    def test_new_row_mismatch_rejected_and_rolls_back(self):
        new_start = self.base + timedelta(days=14)
        response = self._reconcile([
            {"start_datetime": self.active_occ.start_datetime.isoformat()},
            {
                "start_datetime": new_start.isoformat(),
                "required_level": 0,
            },
        ])
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "level_mismatch")
        self.assertEqual(body["details"]["index"], 1)
        # No new occurrence created at the new date.
        self.assertFalse(
            Event.objects.filter(
                event_series=self.main_series,
                start_datetime=new_start,
            ).exists(),
        )

    def test_reactivating_cancelled_occurrence_keeps_its_level(self):
        # Declare both the active and the cancelled date; the cancelled
        # 0-level occurrence reactivates without a level mismatch and keeps
        # its stored level (no retroactive change).
        response = self._reconcile([
            {"start_datetime": self.active_occ.start_datetime.isoformat()},
            {
                "start_datetime": (
                    self.cancelled_occ.start_datetime.isoformat()
                ),
            },
        ])
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.cancelled_occ.pk, response.json()["reactivated"])
        self.cancelled_occ.refresh_from_db()
        self.assertEqual(self.cancelled_occ.status, "upcoming")
        self.assertEqual(self.cancelled_occ.required_level, 0)


class NoOverrideFlagTest(_LevelBase):
    def test_override_flag_does_not_bypass_guardrail(self):
        start = timezone.now().replace(microsecond=0) + timedelta(days=30)
        response = self._post(
            self._bulk_url(self.main_series),
            {
                "allow_level_override": True,
                "occurrences": [
                    {
                        "start_datetime": start.isoformat(),
                        "required_level": 0,
                        "allow_level_override": True,
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "level_mismatch")
        self.assertEqual(
            Event.objects.filter(event_series=self.main_series).count(), 0,
        )


class NoRetroactiveChangeTest(_LevelBase):
    def test_changing_series_level_leaves_mixed_occurrences_untouched(self):
        # Mirror the LLM Zoomcamp accident: a series whose new field defaults
        # to 0 but whose occurrences already sit at different levels.
        series = EventSeries.objects.create(
            name="Mixed Series",
            slug="mixed-series",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
            required_level=0,
        )
        base = timezone.now().replace(microsecond=0) + timedelta(days=50)
        occ_free = Event.objects.create(
            title="Mixed — 1",
            slug="mixed-1",
            start_datetime=base,
            end_datetime=base + timedelta(hours=1),
            status="upcoming",
            origin="studio",
            event_series=series,
            series_position=1,
            required_level=0,
        )
        occ_main = Event.objects.create(
            title="Mixed — 2",
            slug="mixed-2",
            start_datetime=base + timedelta(days=7),
            end_datetime=base + timedelta(days=7, hours=1),
            status="upcoming",
            origin="studio",
            event_series=series,
            series_position=2,
            required_level=20,
        )

        response = self._patch(
            f"/api/event-series/{series.id}",
            {"required_level": 20},
        )
        self.assertEqual(response.status_code, 200)
        occ_free.refresh_from_db()
        occ_main.refresh_from_db()
        self.assertEqual(occ_free.required_level, 0)
        self.assertEqual(occ_main.required_level, 20)
