"""Tests for the Sprint endpoints (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from api.serializers.plans import serialize_sprint
from events.models.event_series import EventSeries
from plans.models import Plan, Sprint

User = get_user_model()


class SprintApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="staff")

        cls.sprint_active = Sprint.objects.create(
            name="May 2026", slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status="active",
        )
        cls.sprint_draft = Sprint.objects.create(
            name="Jul 2026", slug="jul-2026",
            start_date=datetime.date(2026, 7, 1),
            duration_weeks=4, status="draft",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


class SprintsListTest(SprintApiTestBase):
    def test_list_returns_canonical_shape(self):
        response = self.client.get("/api/sprints", **self._auth())
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("sprints", body)
        self.assertEqual(len(body["sprints"]), 2)
        first = body["sprints"][0]
        # Exactly the documented keys.
        self.assertEqual(
            set(first.keys()),
            {
                "slug", "name", "start_date", "duration_weeks",
                "status", "event_series", "created_at", "updated_at",
            },
        )

    def test_list_filters_by_status(self):
        response = self.client.get(
            "/api/sprints?status=active", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        slugs = {s["slug"] for s in response.json()["sprints"]}
        self.assertIn("may-2026", slugs)
        self.assertNotIn("jul-2026", slugs)


class SprintsCreateTest(SprintApiTestBase):
    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/sprints",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_create_returns_201_and_persists(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Sprint.objects.count(), before + 1)
        body = response.json()
        self.assertEqual(body["slug"], "sep-2026")
        self.assertEqual(body["duration_weeks"], 8)
        self.assertEqual(body["status"], "draft")  # default

    def test_create_rejects_missing_required_field(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "x", "slug": "x", "duration_weeks": 6,
        })
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_field")
        self.assertEqual(body["details"]["field"], "start_date")
        self.assertEqual(Sprint.objects.count(), before)


class SprintDetailTest(SprintApiTestBase):
    def test_detail_for_unknown_slug_returns_404(self):
        response = self.client.get("/api/sprints/nope", **self._auth())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_sprint")

    def test_patch_updates_only_supplied_fields(self):
        response = self.client.patch(
            "/api/sprints/may-2026",
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        # Untouched fields keep their values.
        self.assertEqual(body["name"], "May 2026")
        self.assertEqual(body["start_date"], "2026-05-01")

    def test_delete_returns_405_pointing_to_studio(self):
        # Issue #864 (2026-06-13): sprint DELETE is blocked via the API. The
        # 405 fires before any lookup and the sprint row is never touched.
        Plan.objects.create(member=self.member, sprint=self.sprint_active)
        response = self.client.delete(
            "/api/sprints/may-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        body = response.json()
        self.assertEqual(body["code"], "sprint_delete_not_available")
        self.assertIn("Studio", body["error"])
        self.assertTrue(Sprint.objects.filter(slug="may-2026").exists())

    def test_delete_empty_sprint_also_returns_405(self):
        # Even an empty sprint (no attached plans) cannot be deleted via the
        # API; the row survives.
        response = self.client.delete(
            "/api/sprints/jul-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json()["code"], "sprint_delete_not_available",
        )
        self.assertTrue(Sprint.objects.filter(slug="jul-2026").exists())


class SprintsAuthTest(SprintApiTestBase):
    def test_no_header_returns_401_no_side_effects(self):
        before = Sprint.objects.count()
        response = self.client.get("/api/sprints")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Sprint.objects.count(), before)

    def test_invalid_token_returns_401(self):
        response = self.client.get(
            "/api/sprints",
            HTTP_AUTHORIZATION="Token does-not-exist",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_wrong_method_returns_405(self):
        response = self.client.put(
            "/api/sprints", **self._auth(),
        )
        self.assertEqual(response.status_code, 405)


class SprintEventSeriesBase(SprintApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name="June 2026 Community Sprint",
            slug="june-2026-community-sprint",
            start_time=datetime.time(18, 0),
        )
        cls.other_series = EventSeries.objects.create(
            name="Other Series",
            slug="other-series",
            start_time=datetime.time(17, 0),
        )

    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/sprints",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, slug, payload, *, token=None):
        return self.client.patch(
            f"/api/sprints/{slug}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )


class SprintSerializerEventSeriesTest(SprintEventSeriesBase):
    def test_linked_sprint_serializes_id_and_slug(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        data = serialize_sprint(self.sprint_active)
        self.assertEqual(
            data["event_series"],
            {"id": self.series.id, "slug": "june-2026-community-sprint"},
        )

    def test_unlinked_sprint_serializes_null(self):
        data = serialize_sprint(self.sprint_draft)
        self.assertIsNone(data["event_series"])

    def test_detail_endpoint_shows_linked_series(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self.client.get(
            "/api/sprints/may-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["event_series"],
            {"id": self.series.id, "slug": "june-2026-community-sprint"},
        )

    def test_detail_endpoint_shows_null_when_unlinked(self):
        response = self.client.get(
            "/api/sprints/jul-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["event_series"])


class SprintCreateEventSeriesTest(SprintEventSeriesBase):
    def test_create_with_series_id_links(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": self.series.id,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.json()["event_series"],
            {"id": self.series.id, "slug": "june-2026-community-sprint"},
        )
        sprint = Sprint.objects.get(slug="sep-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)

    def test_create_with_series_slug_links(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": "june-2026-community-sprint",
        })
        self.assertEqual(response.status_code, 201)
        sprint = Sprint.objects.get(slug="sep-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)

    def test_create_with_numeric_string_id_links(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": str(self.series.id),
        })
        self.assertEqual(response.status_code, 201)
        sprint = Sprint.objects.get(slug="sep-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)

    def test_create_omitting_series_is_unlinked(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
        })
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["event_series"])
        self.assertIsNone(
            Sprint.objects.get(slug="sep-2026").event_series_id,
        )

    def test_create_with_null_series_is_unlinked(self):
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": None,
        })
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(
            Sprint.objects.get(slug="sep-2026").event_series_id,
        )

    def test_create_with_unknown_id_returns_422_no_write(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": 999999,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_series")
        self.assertEqual(Sprint.objects.count(), before)

    def test_create_with_unknown_slug_returns_422_no_write(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": "no-such-series",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_series")
        self.assertFalse(Sprint.objects.filter(slug="sep-2026").exists())
        self.assertEqual(Sprint.objects.count(), before)

    def test_create_with_wrong_type_returns_422_validation_error(self):
        before = Sprint.objects.count()
        response = self._post({
            "name": "Sep 2026", "slug": "sep-2026",
            "start_date": "2026-09-01", "duration_weeks": 8,
            "event_series": [],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(Sprint.objects.count(), before)


class SprintPatchEventSeriesTest(SprintEventSeriesBase):
    def test_patch_sets_link_with_id_and_bumps_updated_at(self):
        before = Sprint.objects.get(slug="may-2026").updated_at
        response = self._patch("may-2026", {"event_series": self.series.id})
        self.assertEqual(response.status_code, 200)
        sprint = Sprint.objects.get(slug="may-2026")
        self.assertEqual(sprint.event_series_id, self.series.id)
        self.assertGreater(sprint.updated_at, before)

    def test_patch_retargets_with_slug(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch(
            "may-2026", {"event_series": "other-series"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Sprint.objects.get(slug="may-2026").event_series_id,
            self.other_series.id,
        )

    def test_patch_null_clears_link(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"event_series": None})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )

    def test_patch_empty_string_clears_link(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"event_series": ""})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )

    def test_patch_omitting_series_leaves_link_untouched(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"status": "completed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Sprint.objects.get(slug="may-2026").event_series_id,
            self.series.id,
        )

    def test_patch_unknown_id_returns_422_unchanged(self):
        self.sprint_active.event_series = self.series
        self.sprint_active.save(update_fields=["event_series"])
        response = self._patch("may-2026", {"event_series": 999999})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_series")
        self.assertEqual(
            Sprint.objects.get(slug="may-2026").event_series_id,
            self.series.id,
        )

    def test_patch_wrong_type_returns_422_validation_error(self):
        response = self._patch("may-2026", {"event_series": {"id": 1}})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )


class SprintEventSeriesAuthTest(SprintEventSeriesBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Mint a token while the user is staff, then demote: the token still
        # authenticates the user, but the staff gate must reject the write
        # before any event_series resolution runs. ``token_required`` returns
        # 401 for a now-non-staff token; the view's own 403 branch is
        # defence in depth. Either rejection is acceptable -- what matters is
        # that the request never resolves the series and never writes.
        cls.demoted = User.objects.create_user(
            email="demoted@test.com", password="pw", is_staff=True,
        )
        cls.demoted_token = Token.objects.create(
            user=cls.demoted, name="demoted",
        )
        cls.demoted.is_staff = False
        cls.demoted.save(update_fields=["is_staff"])

    def test_non_staff_post_rejected_before_resolution(self):
        before = Sprint.objects.count()
        response = self._post(
            {
                "name": "Sep 2026", "slug": "sep-2026",
                "start_date": "2026-09-01", "duration_weeks": 8,
                "event_series": self.series.id,
            },
            token=self.demoted_token,
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(Sprint.objects.count(), before)
        self.assertFalse(Sprint.objects.filter(slug="sep-2026").exists())

    def test_non_staff_patch_rejected_before_resolution(self):
        response = self._patch(
            "may-2026",
            {"event_series": self.series.id},
            token=self.demoted_token,
        )
        self.assertIn(response.status_code, (401, 403))
        self.assertIsNone(
            Sprint.objects.get(slug="may-2026").event_series_id,
        )
