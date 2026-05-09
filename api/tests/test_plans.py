"""Tests for the Plan endpoints (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
)

User = get_user_model()


class PlansApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.other = User.objects.create_user(
            email="other@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="s")

        cls.sprint = Sprint.objects.create(
            name="May 2026", slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status="active",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


class PlansCreateTest(PlansApiTestBase):
    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/sprints/may-2026/plans",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_create_plan_returns_nested_detail(self):
        before = Plan.objects.count()
        response = self._post({"user_email": "member@test.com"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Plan.objects.count(), before + 1)
        body = response.json()
        self.assertEqual(body["sprint"], "may-2026")
        self.assertEqual(body["user_email"], "member@test.com")
        # Nested keys present, even if empty.
        for key in (
            "weeks", "resources", "deliverables", "next_steps",
            "summary", "focus", "accountability",
        ):
            self.assertIn(key, body)

    def test_create_plan_accepts_flat_summary_fields(self):
        response = self._post({
            "user_email": "member@test.com",
            "summary_current_situation": "Currently doing X",
            "summary_goal": "Become Y",
        })
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.summary_current_situation, "Currently doing X")
        self.assertEqual(plan.summary_goal, "Become Y")

    def test_create_plan_accepts_nested_summary(self):
        response = self._post({
            "user_email": "member@test.com",
            "summary": {
                "current_situation": "X",
                "goal": "Y",
                "main_gap": "Z",
            },
        })
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.summary_current_situation, "X")
        self.assertEqual(plan.summary_main_gap, "Z")

    def test_create_plan_unknown_user_returns_422(self):
        before = Plan.objects.count()
        response = self._post({"user_email": "noone@test.com"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_user")
        self.assertEqual(Plan.objects.count(), before)

    def test_create_duplicate_user_sprint_returns_409(self):
        Plan.objects.create(member=self.member, sprint=self.sprint)
        before = Plan.objects.count()
        response = self._post({"user_email": "member@test.com"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "duplicate_plan")
        self.assertEqual(Plan.objects.count(), before)


class PlansListTest(PlansApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.member_plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint, status="shared",
        )
        cls.other_plan = Plan.objects.create(
            member=cls.other, sprint=cls.sprint, status="draft",
        )

    def test_staff_sees_all_plans(self):
        response = self.client.get(
            "/api/sprints/may-2026/plans", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        ids = {p["id"] for p in response.json()["plans"]}
        self.assertIn(self.member_plan.id, ids)
        self.assertIn(self.other_plan.id, ids)


class PlanDetailTest(PlansApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
            summary_goal="my goal",
            focus_main="main focus",
            focus_supporting=["a", "b"],
        )
        cls.week1 = Week.objects.create(
            plan=cls.plan, week_number=1, theme="warm-up",
        )
        cls.week2 = Week.objects.create(
            plan=cls.plan, week_number=2, theme="dive in",
        )
        Checkpoint.objects.create(
            week=cls.week1, description="cp1", position=0,
        )
        Checkpoint.objects.create(
            week=cls.week1, description="cp2", position=1,
        )
        Checkpoint.objects.create(
            week=cls.week2, description="cp3", position=0,
        )
        Resource.objects.create(
            plan=cls.plan, title="resource", url="https://x", position=0,
        )
        Resource.objects.create(
            plan=cls.plan, title="resource b", url="https://y", position=1,
        )
        Deliverable.objects.create(
            plan=cls.plan, description="ship something", position=0,
        )
        NextStep.objects.create(
            plan=cls.plan, description="do thing", position=0,
        )

    def test_get_detail_returns_full_nested_shape(self):
        response = self.client.get(
            f"/api/plans/{self.plan.id}", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], self.plan.id)
        self.assertEqual(len(body["weeks"]), 2)
        # Week 1 has 2 checkpoints; week 2 has 1.
        weeks_by_num = {w["week_number"]: w for w in body["weeks"]}
        self.assertEqual(len(weeks_by_num[1]["checkpoints"]), 2)
        self.assertEqual(len(weeks_by_num[2]["checkpoints"]), 1)
        self.assertEqual(len(body["resources"]), 2)
        self.assertEqual(len(body["deliverables"]), 1)
        self.assertEqual(len(body["next_steps"]), 1)
        self.assertEqual(body["summary"]["goal"], "my goal")
        self.assertEqual(body["focus"]["main"], "main focus")
        self.assertEqual(body["focus"]["supporting"], ["a", "b"])

    def test_get_detail_for_other_users_plan_returns_200_for_staff(self):
        response = self.client.get(
            f"/api/plans/{self.plan.id}", **self._auth(),
        )
        self.assertEqual(response.status_code, 200)


class PlanPatchTest(PlansApiTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
            status="draft", summary_goal="orig goal",
            accountability="orig",
        )
        cls.original_created_at = cls.plan.created_at
        cls.original_id = cls.plan.id

    def _patch(self, payload, *, token=None):
        return self.client.patch(
            f"/api/plans/{self.plan.id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_patch_updates_status_and_summary(self):
        response = self._patch({
            "status": "active",
            "summary": {"goal": "new goal"},
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["summary"]["goal"], "new goal")
        # Other summary fields untouched.
        self.assertEqual(body["accountability"], "orig")

    def test_patch_ignores_immutable_fields(self):
        response = self._patch({
            "id": 9999,
            "user_email": "evil@x.com",
            "sprint": "evil-sprint",
            "status": "shared",
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # The mutable field DID change, proving the request reached the
        # endpoint -- but the immutable fields did not.
        self.assertEqual(body["status"], "shared")
        self.assertEqual(body["id"], self.original_id)
        self.assertEqual(body["user_email"], "member@test.com")
        self.assertEqual(body["sprint"], "may-2026")


class PlanDeleteTest(PlansApiTestBase):
    def test_delete_returns_204_and_removes_plan(self):
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )
        Week.objects.create(plan=plan, week_number=1)
        response = self.client.delete(
            f"/api/plans/{plan.id}", **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Plan.objects.filter(pk=plan.id).exists())
        # Children went too via the model's CASCADE -- not the focus of
        # this test (we don't test Django framework behaviour) but the
        # API contract returns 204 only when the plan row is gone.


class PlansAuthTest(PlansApiTestBase):
    def test_no_header_returns_401_no_side_effects(self):
        before = Plan.objects.count()
        response = self.client.post(
            "/api/sprints/may-2026/plans",
            data=json.dumps({"user_email": "member@test.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(Plan.objects.count(), before)
