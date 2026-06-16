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
            "summary", "focus", "accountability", "goal",
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

    def test_create_plan_accepts_next_step_kind(self):
        response = self._post({
            "user_email": "member@test.com",
            "next_steps": [
                {
                    "description": "Send GitHub link",
                    "kind": "pre_sprint",
                },
            ],
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["next_steps"][0]["kind"], "pre_sprint")
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.next_steps.get().kind, "pre_sprint")

    def test_create_plan_rejects_unknown_next_step_kind(self):
        before = Plan.objects.count()
        response = self._post({
            "user_email": "member@test.com",
            "next_steps": [
                {
                    "description": "Call Alexey",
                    "kind": "facilitator_follow_up",
                },
            ],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(Plan.objects.count(), before)

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
            member=cls.member, sprint=cls.sprint,
        )
        cls.other_plan = Plan.objects.create(
            member=cls.other, sprint=cls.sprint,
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
        self.assertEqual(body["next_steps"][0]["kind"], "pre_sprint")
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
            summary_goal="orig goal",
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

    def test_patch_updates_summary(self):
        response = self._patch({
            "summary": {"goal": "new goal"},
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["goal"], "new goal")
        # Other summary fields untouched.
        self.assertEqual(body["accountability"], "orig")

    def test_patch_silently_ignores_status(self):
        """Issue #728: ``status`` is no longer a model field.

        A PATCH that includes the legacy ``status`` key returns 200, the
        response shape does NOT include ``status``, and no other top-
        level field is corrupted by the unknown key.
        """
        response = self._patch({"status": "active", "goal": "kept goal"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("status", body)
        self.assertEqual(body["goal"], "kept goal")

    def test_patch_updates_short_goal(self):
        response = self._patch({"goal": "Ship one project"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["goal"], "Ship one project")
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, "Ship one project")

    def test_patch_rejects_overlong_short_goal(self):
        response = self._patch({"goal": "x" * 281})

        self.assertEqual(response.status_code, 422)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, "")

    def test_patch_ignores_immutable_fields(self):
        response = self._patch({
            "id": 9999,
            "user_email": "evil@x.com",
            "sprint": "evil-sprint",
            "goal": "kept goal",
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # The mutable field DID change, proving the request reached the
        # endpoint -- but the immutable fields did not.
        self.assertEqual(body["goal"], "kept goal")
        self.assertEqual(body["id"], self.original_id)
        self.assertEqual(body["user_email"], "member@test.com")
        self.assertEqual(body["sprint"], "may-2026")
        # Issue #728: ``status`` is gone from both model and response.
        self.assertNotIn("status", body)


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


class PlanCreateMaxLengthValidationTest(PlansApiTestBase):
    """Issue #725: every ``max_length``-constrained string field on the
    plans-API write surface must reject overflow with 422, not 500.

    The previous code only validated ``Plan.goal``; the other four caps
    (``summary_weekly_hours``, ``Week.theme``, ``Resource.title``,
    ``Resource.url``) blew up at the DB layer. Each scenario asserts both
    the structured error shape and that no row was persisted.
    """

    def _post(self, payload, *, token=None):
        return self.client.post(
            "/api/sprints/may-2026/plans",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_rejects_overlong_summary_weekly_hours_nested(self):
        max_len = Plan._meta.get_field("summary_weekly_hours").max_length
        before = Plan.objects.count()
        response = self._post({
            "user_email": "member@test.com",
            "summary": {"weekly_hours": "x" * (max_len + 1)},
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("summary.weekly_hours", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)
        # No row persisted.
        self.assertEqual(Plan.objects.count(), before)

    def test_rejects_overlong_summary_weekly_hours_flat(self):
        # When the client sent the flat shape, the error path key
        # mirrors that shape ("summary_weekly_hours") so they can find
        # the offending field in their original payload.
        max_len = Plan._meta.get_field("summary_weekly_hours").max_length
        response = self._post({
            "user_email": "member@test.com",
            "summary_weekly_hours": "x" * (max_len + 1),
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("summary_weekly_hours", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)

    def test_accepts_boundary_summary_weekly_hours(self):
        # Exactly at the cap is valid (one char over is the rejection).
        max_len = Plan._meta.get_field("summary_weekly_hours").max_length
        value = "x" * max_len
        response = self._post({
            "user_email": "member@test.com",
            "summary": {"weekly_hours": value},
        })
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.summary_weekly_hours, value)

    def test_rejects_overlong_week_theme(self):
        max_len = Week._meta.get_field("theme").max_length
        before = Plan.objects.count()
        response = self._post({
            "user_email": "member@test.com",
            "weeks": [
                {"week_number": 1, "theme": "x" * (max_len + 1)},
            ],
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("weeks[0].theme", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)
        # Plan itself rolled back via outer atomic block.
        self.assertEqual(Plan.objects.count(), before)

    def test_accepts_boundary_week_theme(self):
        max_len = Week._meta.get_field("theme").max_length
        value = "x" * max_len
        response = self._post({
            "user_email": "member@test.com",
            "weeks": [{"week_number": 1, "theme": value}],
        })
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.weeks.get(week_number=1).theme, value)

    def test_rejects_overlong_resource_title(self):
        max_len = Resource._meta.get_field("title").max_length
        before = Plan.objects.count()
        response = self._post({
            "user_email": "member@test.com",
            "resources": [
                {"title": "x" * (max_len + 1), "url": "https://example.com"},
            ],
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("resources[0].title", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)
        self.assertEqual(Plan.objects.count(), before)

    def test_rejects_overlong_resource_url(self):
        max_len = Resource._meta.get_field("url").max_length
        before = Plan.objects.count()
        response = self._post({
            "user_email": "member@test.com",
            "resources": [
                {"title": "ok", "url": "https://x.example.com/"
                 + "y" * (max_len + 1)},
            ],
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("resources[0].url", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)
        self.assertEqual(Plan.objects.count(), before)

    def test_accepts_boundary_resource_fields(self):
        title_max = Resource._meta.get_field("title").max_length
        url_max = Resource._meta.get_field("url").max_length
        # Build URL of exactly url_max length so we exercise the
        # accept-side of the boundary on both fields at once.
        url_prefix = "https://x.example.com/"
        url_value = url_prefix + "y" * (url_max - len(url_prefix))
        self.assertEqual(len(url_value), url_max)
        response = self._post({
            "user_email": "member@test.com",
            "resources": [
                {"title": "t" * title_max, "url": url_value},
            ],
        })
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        resource = plan.resources.get()
        self.assertEqual(len(resource.title), title_max)
        self.assertEqual(len(resource.url), url_max)

    def test_regression_payload_under_caps_succeeds(self):
        # Sanity: a payload that touches every newly-validated field, all
        # under the caps, still results in 201 and persists the data.
        response = self._post({
            "user_email": "member@test.com",
            "summary": {"weekly_hours": "5h/week"},
            "weeks": [
                {"week_number": 1, "theme": "warm-up"},
                {"week_number": 2, "theme": "build"},
            ],
            "resources": [
                {"title": "Blog post", "url": "https://example.com/p"},
            ],
        })
        self.assertEqual(response.status_code, 201)
        plan = Plan.objects.get(member=self.member, sprint=self.sprint)
        self.assertEqual(plan.summary_weekly_hours, "5h/week")
        self.assertEqual(plan.weeks.count(), 2)
        self.assertEqual(plan.resources.count(), 1)


class PlanPatchMaxLengthValidationTest(PlansApiTestBase):
    """Issue #725: PATCH must enforce the same caps as create."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def _patch(self, payload, *, token=None):
        return self.client.patch(
            f"/api/plans/{self.plan.id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_patch_rejects_overlong_summary_weekly_hours_nested(self):
        max_len = Plan._meta.get_field("summary_weekly_hours").max_length
        response = self._patch({
            "summary": {"weekly_hours": "x" * (max_len + 1)},
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("summary.weekly_hours", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)
        # No partial mutation: row still has the original empty value.
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.summary_weekly_hours, "")

    def test_patch_rejects_overlong_summary_weekly_hours_flat(self):
        max_len = Plan._meta.get_field("summary_weekly_hours").max_length
        response = self._patch({
            "summary_weekly_hours": "x" * (max_len + 1),
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("summary_weekly_hours", body["details"])
        self.assertEqual(body["details"]["max_length"], max_len)

    def test_patch_accepts_boundary_summary_weekly_hours(self):
        max_len = Plan._meta.get_field("summary_weekly_hours").max_length
        value = "x" * max_len
        response = self._patch({"summary": {"weekly_hours": value}})
        self.assertEqual(response.status_code, 200)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.summary_weekly_hours, value)

    def test_patch_goal_uses_model_meta_max_length(self):
        # Regression: the existing 280-char check on goal still rejects
        # over-length values AND now also surfaces ``max_length`` in
        # ``details`` for client introspection.
        goal_max = Plan._meta.get_field("goal").max_length
        response = self._patch({"goal": "x" * (goal_max + 1)})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["max_length"], goal_max)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, "")
