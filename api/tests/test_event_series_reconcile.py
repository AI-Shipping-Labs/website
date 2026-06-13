"""Tests for issue #878.

Two clean-ups on the event-series API:

1. published/status dual-flag reconciliation. ``Event.status`` is the
   single source of truth for public visibility; a ``draft`` occurrence must
   never carry a contradictory ``published=True`` + ``published_at`` stamp.
   The bulk creator now creates drafts with ``published=False``.
2. Idempotent ``PUT /api/event-series/<id>/occurrences`` schedule-replace:
   declare the exact desired set in one atomic call (create / keep / cancel /
   reactivate). Removal is cancellation, never a hard-delete (#864).
"""

import json
from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()


class ReconcileApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-reconcile@test.com",
            password="pw",
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(
            user=cls.staff, name="reconcile",
        )

        cls.series = EventSeries.objects.create(
            name="Reconcile Series",
            slug="reconcile-series",
            description="Weekly live sessions",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _put(self, path, payload, *, token="__default__"):
        kwargs = {}
        if token != "__skip__":
            kwargs = self._auth(None if token == "__default__" else token)
        return self.client.put(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **kwargs,
        )

    def _post(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _get(self, path):
        return self.client.get(path, **self._auth())

    def _reconcile_url(self):
        return f"/api/event-series/{self.series.pk}/occurrences"

    def _iso(self, dt):
        return dt.isoformat()

    def _make_occurrence(self, start, *, status="upcoming", slug=None):
        slug = slug or f"occ-{start.strftime('%Y%m%d%H%M')}"
        return Event.objects.create(
            title=f"Occurrence {slug}",
            slug=slug,
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status=status,
            origin="studio",
            event_series=self.series,
            title_is_auto=True,
        )


class ReconcileExactSetTest(ReconcileApiTestBase):
    """Operator declares the exact schedule in one call."""

    def setUp(self):
        base = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        self.a = base
        self.b = base + timedelta(days=7)
        self.c = base + timedelta(days=14)
        self.d = base + timedelta(days=21)
        self.e = base + timedelta(days=28)
        self.occ_a = self._make_occurrence(self.a, slug="occ-a")
        self.occ_b = self._make_occurrence(self.b, slug="occ-b")
        self.occ_c = self._make_occurrence(self.c, slug="occ-c")
        self.occ_d = self._make_occurrence(self.d, slug="occ-d")

    def test_creates_keeps_and_cancels(self):
        response = self._put(
            self._reconcile_url(),
            {
                "occurrences": [
                    {"start_datetime": self._iso(self.a)},
                    {"start_datetime": self._iso(self.c)},
                    {"start_datetime": self._iso(self.e)},
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()

        # A and C kept, B and D cancelled, E created.
        self.assertEqual(set(body["kept"]), {self.occ_a.pk, self.occ_c.pk})
        self.assertEqual(set(body["cancelled"]), {self.occ_b.pk, self.occ_d.pk})
        self.assertEqual(len(body["created"]), 1)
        self.assertEqual(body["reactivated"], [])
        self.assertEqual(body["created_count"], 1)
        self.assertEqual(body["kept_count"], 2)
        self.assertEqual(body["cancelled_count"], 2)

        self.occ_a.refresh_from_db()
        self.occ_b.refresh_from_db()
        self.occ_c.refresh_from_db()
        self.occ_d.refresh_from_db()
        self.assertEqual(self.occ_a.status, "upcoming")
        self.assertEqual(self.occ_c.status, "upcoming")
        self.assertEqual(self.occ_b.status, "cancelled")
        self.assertEqual(self.occ_d.status, "cancelled")

        # E exists at minute E with a draft/published-consistent state.
        new_event = Event.objects.get(pk=body["created"][0])
        self.assertEqual(
            new_event.start_datetime.replace(second=0, microsecond=0),
            self.e.replace(second=0, microsecond=0),
        )

    def test_cancelled_occurrences_are_not_hard_deleted(self):
        self._put(
            self._reconcile_url(),
            {
                "occurrences": [
                    {"start_datetime": self._iso(self.a)},
                    {"start_datetime": self._iso(self.c)},
                ],
            },
        )
        # B and D still retrievable (200, not 404) with status cancelled.
        for occ in (self.occ_b, self.occ_d):
            resp = self._get(
                f"/api/event-series/{self.series.pk}/occurrences/{occ.pk}",
            )
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "cancelled")


class ReconcileIdempotentTest(ReconcileApiTestBase):
    """Re-declaring the same schedule changes nothing."""

    def setUp(self):
        base = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        self.a = base
        self.c = base + timedelta(days=14)
        self.e = base + timedelta(days=28)
        self.occ_a = self._make_occurrence(self.a, slug="idem-a")
        self.occ_c = self._make_occurrence(self.c, slug="idem-c")
        self.occ_e = self._make_occurrence(self.e, slug="idem-e")

    def test_replay_is_noop(self):
        desired = {
            "occurrences": [
                {"start_datetime": self._iso(self.a)},
                {"start_datetime": self._iso(self.c)},
                {"start_datetime": self._iso(self.e)},
            ],
        }
        response = self._put(self._reconcile_url(), desired)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["created"], [])
        self.assertEqual(body["cancelled"], [])
        self.assertEqual(body["reactivated"], [])
        self.assertEqual(
            set(body["kept"]),
            {self.occ_a.pk, self.occ_c.pk, self.occ_e.pk},
        )

        # No new rows created across the replay.
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(), 3,
        )


class ReconcileReactivateTest(ReconcileApiTestBase):
    """Re-adding a dropped date reactivates the cancelled occurrence."""

    def setUp(self):
        base = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        self.a = base
        self.occ_a = self._make_occurrence(
            self.a, status="cancelled", slug="react-a",
        )

    def test_reactivates_instead_of_duplicating(self):
        before = Event.objects.filter(event_series=self.series).count()
        response = self._put(
            self._reconcile_url(),
            {"occurrences": [{"start_datetime": self._iso(self.a)}]},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["reactivated"], [self.occ_a.pk])
        self.assertEqual(body["created"], [])

        # No duplicate row at minute A.
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(), before,
        )
        self.occ_a.refresh_from_db()
        self.assertEqual(self.occ_a.status, "upcoming")


class ReconcileAtomicTest(ReconcileApiTestBase):
    """Reconcile rolls back fully on a bad row."""

    def setUp(self):
        base = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        self.a = base
        self.b = base + timedelta(days=7)
        self.occ_a = self._make_occurrence(self.a, slug="atom-a")
        self.occ_b = self._make_occurrence(self.b, slug="atom-b")

    def test_bad_row_rolls_back_everything(self):
        before = Event.objects.filter(event_series=self.series).count()
        response = self._put(
            self._reconcile_url(),
            {
                "occurrences": [
                    {"start_datetime": self._iso(self.a)},
                    {"start_datetime": "not-a-datetime"},
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["details"]["index"], 1)

        # A and B unchanged, no new occurrence.
        self.occ_a.refresh_from_db()
        self.occ_b.refresh_from_db()
        self.assertEqual(self.occ_a.status, "upcoming")
        self.assertEqual(self.occ_b.status, "upcoming")
        self.assertEqual(
            Event.objects.filter(event_series=self.series).count(), before,
        )

    def test_in_batch_duplicate_rejected(self):
        before_states = {
            e.pk: e.status
            for e in Event.objects.filter(event_series=self.series)
        }
        response = self._put(
            self._reconcile_url(),
            {
                "occurrences": [
                    {"start_datetime": self._iso(self.a)},
                    {"start_datetime": self._iso(self.a)},
                ],
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "duplicate_in_batch")
        # Series unchanged.
        after_states = {
            e.pk: e.status
            for e in Event.objects.filter(event_series=self.series)
        }
        self.assertEqual(before_states, after_states)


class ReconcileUnknownSeriesTest(ReconcileApiTestBase):
    def test_unknown_series_404(self):
        response = self._put(
            "/api/event-series/999999/occurrences",
            {"occurrences": [{"start_datetime": self._iso(timezone.now())}]},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_series")


class ReconcileAuthTest(ReconcileApiTestBase):
    def test_requires_token(self):
        response = self._put(
            self._reconcile_url(),
            {"occurrences": []},
            token="__skip__",
        )
        self.assertEqual(response.status_code, 401)


class PublishedStatusContractTest(ReconcileApiTestBase):
    """A draft occurrence is never simultaneously published."""

    def test_bulk_created_draft_is_not_published(self):
        start = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            {"occurrences": [{"start_datetime": start.isoformat()}]},
        )
        self.assertEqual(response.status_code, 201)
        occ_id = response.json()["occurrence_ids"][0]

        # Serializer must never report published=True on a hidden draft.
        detail = self._get(
            f"/api/event-series/{self.series.pk}/occurrences/{occ_id}",
        )
        self.assertEqual(detail.json()["status"], "draft")
        self.assertFalse(detail.json()["published"])

        # No bogus first-publish timestamp stamped on the draft.
        event = Event.objects.get(pk=occ_id)
        self.assertFalse(event.published)
        self.assertIsNone(event.published_at)

    def test_reconcile_created_draft_is_not_published(self):
        start = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        response = self._put(
            self._reconcile_url(),
            {"occurrences": [{"start_datetime": start.isoformat()}]},
        )
        self.assertEqual(response.status_code, 200)
        occ_id = response.json()["created"][0]
        event = Event.objects.get(pk=occ_id)
        self.assertEqual(event.status, "draft")
        self.assertFalse(event.published)
        self.assertIsNone(event.published_at)


class BulkStillAddsOnlyTest(ReconcileApiTestBase):
    """The additive bulk endpoint is unchanged: adds-only, never cancels."""

    def setUp(self):
        self.a = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        self.b = self.a + timedelta(days=7)
        self.occ_a = self._make_occurrence(self.a, slug="bulk-a")

    def test_bulk_adds_only_and_skips_existing(self):
        response = self._post(
            f"/api/event-series/{self.series.pk}/occurrences/bulk",
            {
                "occurrences": [
                    {"start_datetime": self.a.isoformat()},
                    {"start_datetime": self.b.isoformat()},
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(body["skipped_existing"], 1)

        # A is NOT cancelled by the additive endpoint.
        self.occ_a.refresh_from_db()
        self.assertEqual(self.occ_a.status, "upcoming")


class MigrationHealTest(TestCase):
    """The data migration heals contradictory pre-existing rows.

    Verified by re-applying the migration's heal function against a row
    seeded into the contradictory state. We bypass ``Event.save()`` (which
    would re-sync ``published_at``) with ``QuerySet.update`` to reproduce a
    pre-#878 row, then call the migration operation directly.
    """

    def test_heals_series_draft_published_and_skips_standalone(self):
        import importlib

        from django.apps import apps as django_apps

        heal_mod = importlib.import_module(
            "events.migrations.0032_heal_series_draft_published",
        )

        series = EventSeries.objects.create(
            name="Heal Series",
            slug="heal-series",
            cadence="weekly",
            day_of_week=2,
            start_time=time(18, 0),
            timezone="Europe/Berlin",
        )
        start = timezone.now().replace(
            second=0, microsecond=0,
        ) + timedelta(days=7)
        series_occ = Event.objects.create(
            title="Heal occ",
            slug="heal-occ",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="draft",
            origin="studio",
            event_series=series,
        )
        standalone = Event.objects.create(
            title="Standalone draft",
            slug="standalone-draft",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="draft",
            origin="studio",
        )
        # Force the contradictory state directly in the DB (bypass save()).
        Event.objects.filter(pk=series_occ.pk).update(
            published=True, published_at=timezone.now(),
        )
        Event.objects.filter(pk=standalone.pk).update(
            published=True, published_at=timezone.now(),
        )

        heal_mod.heal_series_draft_published(django_apps, None)

        series_occ.refresh_from_db()
        standalone.refresh_from_db()
        # Series occurrence healed.
        self.assertFalse(series_occ.published)
        self.assertIsNone(series_occ.published_at)
        self.assertEqual(series_occ.status, "draft")
        # Standalone draft untouched.
        self.assertTrue(standalone.published)
        self.assertIsNotNone(standalone.published_at)
