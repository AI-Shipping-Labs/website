"""Tests for the Week endpoints (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()


class WeeksApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="s")
        cls.sprint = Sprint.objects.create(
            name="s", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}


class WeekCreateTest(WeeksApiTestBase):
    def _post(self, payload):
        return self.client.post(
            f"/api/plans/{self.plan.id}/weeks",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def test_create_week_appends_position(self):
        Week.objects.create(plan=self.plan, week_number=1, position=0)
        response = self._post({"week_number": 2, "theme": "deep"})
        self.assertEqual(response.status_code, 201)
        # First week has position 0; the new one auto-positions to 1.
        self.assertEqual(response.json()["position"], 1)

    def test_create_first_week_starts_at_position_zero(self):
        response = self._post({"week_number": 1})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["position"], 0)

    def test_create_duplicate_week_number_returns_409(self):
        Week.objects.create(plan=self.plan, week_number=1)
        before = Week.objects.filter(plan=self.plan).count()
        response = self._post({"week_number": 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "duplicate_week_number")
        self.assertEqual(
            Week.objects.filter(plan=self.plan).count(), before,
        )

    def test_create_missing_week_number_returns_400(self):
        response = self._post({"theme": "no week_number"})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_field")
        self.assertEqual(body["details"]["field"], "week_number")


class WeekPatchTest(WeeksApiTestBase):
    def test_patch_week_theme(self):
        week = Week.objects.create(
            plan=self.plan, week_number=1, theme="orig",
        )
        response = self.client.patch(
            f"/api/weeks/{week.id}",
            data=json.dumps({"theme": "new"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        week.refresh_from_db()
        self.assertEqual(week.theme, "new")
        self.assertEqual(week.week_number, 1)


class WeekDeleteTest(WeeksApiTestBase):
    def test_delete_week_returns_204(self):
        week = Week.objects.create(plan=self.plan, week_number=1)
        Checkpoint.objects.create(week=week, description="cp")
        response = self.client.delete(
            f"/api/weeks/{week.id}", **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Week.objects.filter(pk=week.id).exists())


class WeekUnknownIdTest(WeeksApiTestBase):
    def test_patch_unknown_week_returns_404(self):
        response = self.client.patch(
            "/api/weeks/99999",
            data=json.dumps({"theme": "x"}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_week")
