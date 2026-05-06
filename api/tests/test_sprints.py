"""Tests for the Sprint endpoints (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
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
        cls.member_token = Token.objects.create(user=cls.member, name="m")

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
                "status", "created_at", "updated_at",
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

    def test_create_rejects_non_staff_token(self):
        before = Sprint.objects.count()
        response = self._post(
            {
                "name": "x", "slug": "x",
                "start_date": "2026-01-01", "duration_weeks": 6,
            },
            token=self.member_token,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "forbidden_other_user_plan")
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

    def test_delete_with_attached_plans_returns_409(self):
        Plan.objects.create(member=self.member, sprint=self.sprint_active)
        response = self.client.delete(
            "/api/sprints/may-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "sprint_has_plans")
        self.assertTrue(Sprint.objects.filter(slug="may-2026").exists())

    def test_delete_empty_sprint_returns_204(self):
        # Use the draft sprint which has no attached plans.
        response = self.client.delete(
            "/api/sprints/jul-2026", **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Sprint.objects.filter(slug="jul-2026").exists())

    def test_detail_for_non_staff_with_no_plan_returns_404(self):
        response = self.client.get(
            "/api/sprints/may-2026", **self._auth(self.member_token),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_sprint")

    def test_detail_for_non_staff_with_plan_in_sprint_returns_200(self):
        Plan.objects.create(member=self.member, sprint=self.sprint_active)
        response = self.client.get(
            "/api/sprints/may-2026", **self._auth(self.member_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["slug"], "may-2026")


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
