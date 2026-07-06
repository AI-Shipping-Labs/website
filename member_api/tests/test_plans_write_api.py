"""Tests for the member Plans API content-editing endpoints (issue #1128).

These exercise the ``plans:write`` scope surface: the plan-level PATCH plus
create / update / delete for weeks, checkpoints, deliverables, next-steps,
resources, and the singleton week note. Each test tells the story of a member
(often driving a coding agent) reshaping their own plan, and every scenario
also proves the write can never reach another member's plan.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import MemberAPIKey
from plans.models import (
    Checkpoint,
    Deliverable,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)

User = get_user_model()


@tag("core")
class MemberPlansWriteApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="write-owner@test.com", first_name="Owner",
        )
        cls.other = User.objects.create_user(
            email="write-other@test.com", first_name="Other",
        )
        cls.sprint = Sprint.objects.create(
            name="May 2026",
            slug="write-may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
            status="active",
        )
        cls.sprint2 = Sprint.objects.create(
            name="Jun 2026",
            slug="write-jun-2026",
            start_date=datetime.date(2026, 6, 1),
            duration_weeks=6,
            status="active",
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.other)
        SprintEnrollment.objects.create(sprint=cls.sprint2, user=cls.member)
        # Full-scope key for the owner and for member B.
        cls.key, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member, name="agent",
        )
        cls.other_key, cls.other_plaintext = MemberAPIKey.create_for_user(
            user=cls.other, name="other agent",
        )
        # A progress-only key (no ``plans:write``).
        cls.progress_key, cls.progress_plaintext = MemberAPIKey.create_for_user(
            user=cls.member,
            name="progress only",
            scopes=["plans:read", "plans:write_progress"],
        )

    def _auth(self, plaintext=None):
        return {"HTTP_AUTHORIZATION": f"Token {plaintext or self.plaintext}"}

    def _post(self, path, payload, *, plaintext=None):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(plaintext),
        )

    def _patch(self, path, payload, *, plaintext=None):
        return self.client.patch(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(plaintext),
        )

    def _put(self, path, payload, *, plaintext=None):
        return self.client.put(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(plaintext),
        )

    def _delete(self, path, *, plaintext=None):
        return self.client.delete(path, **self._auth(plaintext))

    def _get_detail(self, plan, *, plaintext=None):
        response = self.client.get(
            f"/member-api/v1/plans/{plan.id}", **self._auth(plaintext),
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _make_plan(self, member, *, title="Owner plan", weeks=0, visibility="private", sprint=None):
        plan = Plan.objects.create(
            member=member,
            sprint=sprint or self.sprint,
            title=title,
            visibility=visibility,
            goal="Original goal",
        )
        for index in range(1, weeks + 1):
            Week.objects.create(plan=plan, week_number=index, position=index)
        return plan


class PlanPatchTest(MemberPlansWriteApiTestBase):
    def test_patch_updates_narrative_and_returns_full_detail(self):
        plan = self._make_plan(self.member)

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}",
            {
                "title": "Ship an eval harness",
                "goal": "Working harness",
                "summary": {"goal": "A reusable eval harness", "weekly_hours": "6h"},
                "focus": {"main": "Evaluation", "supporting": ["Tracing", "CI"]},
                "accountability": "Weekly demo",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["title"], "Ship an eval harness")
        self.assertEqual(body["goal"], "Working harness")
        self.assertEqual(body["summary"]["goal"], "A reusable eval harness")
        self.assertEqual(body["summary"]["weekly_hours"], "6h")
        self.assertEqual(body["focus"]["main"], "Evaluation")
        self.assertEqual(body["focus"]["supporting"], ["Tracing", "CI"])
        self.assertEqual(body["accountability"], "Weekly demo")

    def test_patch_applies_only_supplied_keys(self):
        plan = self._make_plan(self.member, title="Keep me")
        plan.accountability = "Existing accountability"
        plan.save()

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}", {"goal": "Only goal changed"},
        )

        self.assertEqual(response.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.goal, "Only goal changed")
        self.assertEqual(plan.title, "Keep me")
        self.assertEqual(plan.accountability, "Existing accountability")

    def test_patch_sets_visibility_to_cohort(self):
        plan = self._make_plan(self.member, visibility="private")

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}",
            {"focus": {"main": "Shipping"}, "visibility": "cohort"},
        )

        self.assertEqual(response.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.visibility, "cohort")
        self.assertEqual(plan.focus_main, "Shipping")

    def test_patch_rejects_public_visibility_without_writing(self):
        plan = self._make_plan(self.member, visibility="private")

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}",
            {"goal": "changed", "visibility": "public"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_visibility")
        plan.refresh_from_db()
        self.assertEqual(plan.visibility, "private")
        self.assertEqual(plan.goal, "Original goal")

    def test_patch_rejects_unknown_visibility(self):
        plan = self._make_plan(self.member)

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}", {"visibility": "secret"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_visibility")

    def test_patch_rejects_unknown_fields(self):
        plan = self._make_plan(self.member)

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}", {"not_a_field": "x"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "unknown_field")

    def test_patch_on_unowned_plan_returns_404(self):
        plan_b = self._make_plan(self.other, title="B plan")

        response = self._patch(
            f"/member-api/v1/plans/{plan_b.id}", {"title": "hijacked"},
        )

        self.assertEqual(response.status_code, 404)
        plan_b.refresh_from_db()
        self.assertEqual(plan_b.title, "B plan")


class WeekWriteTest(MemberPlansWriteApiTestBase):
    def test_create_update_delete_week(self):
        plan = self._make_plan(self.member)

        created = self._post(
            f"/member-api/v1/plans/{plan.id}/weeks",
            {"week_number": 1, "theme": "Discovery"},
        )
        self.assertEqual(created.status_code, 201)
        week_id = created.json()["id"]
        self.assertEqual(created.json()["theme"], "Discovery")

        updated = self._patch(
            f"/member-api/v1/plans/{plan.id}/weeks/{week_id}",
            {"theme": "Build", "position": 3},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["theme"], "Build")
        self.assertEqual(updated.json()["position"], 3)

        deleted = self._delete(f"/member-api/v1/plans/{plan.id}/weeks/{week_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Week.objects.filter(pk=week_id).exists())

    def test_duplicate_week_number_returns_422_without_write(self):
        plan = self._make_plan(self.member, weeks=1)

        response = self._post(
            f"/member-api/v1/plans/{plan.id}/weeks", {"week_number": 1},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "duplicate_week_number")
        self.assertEqual(plan.weeks.count(), 1)

    def test_delete_week_cascades_checkpoints_and_note(self):
        plan = self._make_plan(self.member, weeks=4)
        week3 = plan.weeks.get(week_number=3)
        checkpoint = Checkpoint.objects.create(week=week3, description="do it")
        WeekNote.objects.create(week=week3, author=self.member, body="week note")

        response = self._delete(
            f"/member-api/v1/plans/{plan.id}/weeks/{week3.id}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(plan.weeks.count(), 3)
        self.assertFalse(Checkpoint.objects.filter(pk=checkpoint.id).exists())
        self.assertFalse(WeekNote.objects.filter(week_id=week3.id).exists())

    def test_cannot_create_week_on_unowned_plan(self):
        plan_b = self._make_plan(self.other)

        response = self._post(
            f"/member-api/v1/plans/{plan_b.id}/weeks", {"week_number": 1},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(plan_b.weeks.exists())

    def test_cannot_patch_week_of_another_plan(self):
        plan = self._make_plan(self.member, weeks=1)
        plan_b = self._make_plan(self.other, weeks=1)
        week_b = plan_b.weeks.get()

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}/weeks/{week_b.id}",
            {"theme": "hijack"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "week_not_found")


class CheckpointWriteTest(MemberPlansWriteApiTestBase):
    def test_create_with_initial_done_records_timestamp(self):
        plan = self._make_plan(self.member, weeks=1)
        week = plan.weeks.get()

        response = self._post(
            f"/member-api/v1/plans/{plan.id}/weeks/{week.id}/checkpoints",
            {"description": "Ship v1", "done": True},
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNotNone(response.json()["done_at"])

    def test_reshape_loop_rename_delete_add_in_position_order(self):
        plan = self._make_plan(self.member, weeks=2)
        week2 = plan.weeks.get(week_number=2)
        c1 = Checkpoint.objects.create(week=week2, description="first", position=0)
        c2 = Checkpoint.objects.create(week=week2, description="second", position=1)
        c3 = Checkpoint.objects.create(week=week2, description="third", position=2)

        self.assertEqual(
            self._patch(
                f"/member-api/v1/plans/{plan.id}/checkpoints/{c2.id}",
                {"description": "second renamed"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self._delete(
                f"/member-api/v1/plans/{plan.id}/checkpoints/{c3.id}",
            ).status_code,
            200,
        )
        self.assertEqual(
            self._post(
                f"/member-api/v1/plans/{plan.id}/weeks/{week2.id}/checkpoints",
                {"description": "fourth", "position": 3},
            ).status_code,
            201,
        )

        detail = self._get_detail(plan)
        week2_data = next(w for w in detail["weeks"] if w["week_number"] == 2)
        descriptions = [c["description"] for c in week2_data["checkpoints"]]
        self.assertEqual(descriptions, ["first", "second renamed", "fourth"])
        # Untouched checkpoint keeps its identity.
        self.assertTrue(Checkpoint.objects.filter(pk=c1.id).exists())

    def test_move_checkpoint_to_another_week(self):
        plan = self._make_plan(self.member, weeks=2)
        week1 = plan.weeks.get(week_number=1)
        week2 = plan.weeks.get(week_number=2)
        checkpoint = Checkpoint.objects.create(week=week1, description="movable")

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}/checkpoints/{checkpoint.id}",
            {"week_id": week2.id},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["week_id"], week2.id)
        detail = self._get_detail(plan)
        week1_data = next(w for w in detail["weeks"] if w["week_number"] == 1)
        week2_data = next(w for w in detail["weeks"] if w["week_number"] == 2)
        self.assertEqual(week1_data["checkpoints"], [])
        self.assertEqual(len(week2_data["checkpoints"]), 1)

    def test_move_checkpoint_to_week_of_other_plan_rejected(self):
        plan = self._make_plan(self.member, weeks=1)
        week = plan.weeks.get()
        checkpoint = Checkpoint.objects.create(week=week, description="mine")
        plan_b = self._make_plan(self.other, weeks=1)
        week_b = plan_b.weeks.get()

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}/checkpoints/{checkpoint.id}",
            {"week_id": week_b.id},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "week_not_found")
        checkpoint.refresh_from_db()
        self.assertEqual(checkpoint.week_id, week.id)

    def test_cannot_patch_checkpoint_of_another_member(self):
        plan = self._make_plan(self.member, weeks=1)
        plan_b = self._make_plan(self.other, weeks=1)
        checkpoint_b = Checkpoint.objects.create(
            week=plan_b.weeks.get(), description="theirs",
        )

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}/checkpoints/{checkpoint_b.id}",
            {"description": "hijack"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "checkpoint_not_found")
        checkpoint_b.refresh_from_db()
        self.assertEqual(checkpoint_b.description, "theirs")

    def test_create_checkpoint_requires_description(self):
        plan = self._make_plan(self.member, weeks=1)
        week = plan.weeks.get()

        response = self._post(
            f"/member-api/v1/plans/{plan.id}/weeks/{week.id}/checkpoints",
            {"description": "   "},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")


class DeliverableWriteTest(MemberPlansWriteApiTestBase):
    def test_create_update_delete_deliverable_with_done(self):
        plan = self._make_plan(self.member)

        created = self._post(
            f"/member-api/v1/plans/{plan.id}/deliverables",
            {"description": "A working harness", "done": False},
        )
        self.assertEqual(created.status_code, 201)
        self.assertIsNone(created.json()["done_at"])
        deliverable_id = created.json()["id"]

        updated = self._patch(
            f"/member-api/v1/plans/{plan.id}/deliverables/{deliverable_id}",
            {"done": True, "position": 2},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertIsNotNone(updated.json()["done_at"])
        self.assertEqual(updated.json()["position"], 2)

        deleted = self._delete(
            f"/member-api/v1/plans/{plan.id}/deliverables/{deliverable_id}",
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Deliverable.objects.filter(pk=deliverable_id).exists())

    def test_cannot_delete_deliverable_of_another_plan(self):
        plan = self._make_plan(self.member)
        plan_b = self._make_plan(self.other)
        deliverable_b = Deliverable.objects.create(plan=plan_b, description="theirs")

        response = self._delete(
            f"/member-api/v1/plans/{plan.id}/deliverables/{deliverable_b.id}",
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "deliverable_not_found")
        self.assertTrue(Deliverable.objects.filter(pk=deliverable_b.id).exists())


class NextStepWriteTest(MemberPlansWriteApiTestBase):
    def test_create_update_delete_next_step(self):
        plan = self._make_plan(self.member)

        created = self._post(
            f"/member-api/v1/plans/{plan.id}/next-steps",
            {"description": "Read tracing docs"},
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["kind"], "pre_sprint")
        next_step_id = created.json()["id"]

        updated = self._patch(
            f"/member-api/v1/plans/{plan.id}/next-steps/{next_step_id}",
            {"kind": "next_step", "done": True},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["kind"], "next_step")
        self.assertIsNotNone(updated.json()["done_at"])

        deleted = self._delete(
            f"/member-api/v1/plans/{plan.id}/next-steps/{next_step_id}",
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(NextStep.objects.filter(pk=next_step_id).exists())

    def test_invalid_kind_returns_422(self):
        plan = self._make_plan(self.member)

        response = self._post(
            f"/member-api/v1/plans/{plan.id}/next-steps",
            {"description": "x", "kind": "bogus"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_kind")
        self.assertFalse(plan.next_steps.exists())


class ResourceWriteTest(MemberPlansWriteApiTestBase):
    def test_create_update_delete_resource(self):
        plan = self._make_plan(self.member)

        created = self._post(
            f"/member-api/v1/plans/{plan.id}/resources",
            {"title": "Tracing docs", "url": "https://example.com/tracing"},
        )
        self.assertEqual(created.status_code, 201)
        resource_id = created.json()["id"]
        self.assertEqual(created.json()["url"], "https://example.com/tracing")

        updated = self._patch(
            f"/member-api/v1/plans/{plan.id}/resources/{resource_id}",
            {"note": "Skim sections 2-4", "position": 1},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["note"], "Skim sections 2-4")

        deleted = self._delete(
            f"/member-api/v1/plans/{plan.id}/resources/{resource_id}",
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(Resource.objects.filter(pk=resource_id).exists())

    def test_invalid_url_returns_422_without_write(self):
        plan = self._make_plan(self.member)

        response = self._post(
            f"/member-api/v1/plans/{plan.id}/resources",
            {"title": "Bad", "url": "not-a-url"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_url")
        self.assertFalse(Resource.objects.filter(plan=plan).exists())


class WeekNoteWriteTest(MemberPlansWriteApiTestBase):
    def test_upsert_then_clear_singleton_note(self):
        plan = self._make_plan(self.member, weeks=1)
        week = plan.weeks.get()
        base = f"/member-api/v1/plans/{plan.id}/weeks/{week.id}/note"

        first = self._put(base, {"body": "First body"})
        self.assertEqual(first.status_code, 200)
        note = WeekNote.objects.get(week=week)
        self.assertEqual(note.body, "First body")
        self.assertEqual(note.author_id, self.member.id)

        second = self._put(base, {"body": "Second body"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(WeekNote.objects.filter(week=week).count(), 1)
        note.refresh_from_db()
        self.assertEqual(note.body, "Second body")

        removed = self._delete(base)
        self.assertEqual(removed.status_code, 200)
        self.assertFalse(WeekNote.objects.filter(week=week).exists())

        detail = self._get_detail(plan)
        week_data = next(w for w in detail["weeks"] if w["id"] == week.id)
        self.assertIsNone(week_data["note"])

    def test_note_requires_body(self):
        plan = self._make_plan(self.member, weeks=1)
        week = plan.weeks.get()

        response = self._put(
            f"/member-api/v1/plans/{plan.id}/weeks/{week.id}/note", {"body": ""},
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(WeekNote.objects.filter(week=week).exists())


class ScopeEnforcementTest(MemberPlansWriteApiTestBase):
    def test_progress_only_key_is_rejected_from_content_endpoints(self):
        plan = self._make_plan(self.member, weeks=1)
        week = plan.weeks.get()
        checkpoint = Checkpoint.objects.create(week=week, description="c")
        deliverable = Deliverable.objects.create(plan=plan, description="d")

        cases = [
            ("post", f"/member-api/v1/plans/{plan.id}/weeks", {"week_number": 2}),
            (
                "patch",
                f"/member-api/v1/plans/{plan.id}/checkpoints/{checkpoint.id}",
                {"description": "x"},
            ),
            (
                "delete",
                f"/member-api/v1/plans/{plan.id}/deliverables/{deliverable.id}",
                None,
            ),
            ("patch", f"/member-api/v1/plans/{plan.id}", {"title": "x"}),
        ]
        for method, path, payload in cases:
            with self.subTest(path=path, method=method):
                if method == "post":
                    response = self._post(path, payload, plaintext=self.progress_plaintext)
                elif method == "patch":
                    response = self._patch(path, payload, plaintext=self.progress_plaintext)
                else:
                    response = self._delete(path, plaintext=self.progress_plaintext)
                self.assertEqual(response.status_code, 401)

    def test_progress_only_key_can_still_toggle_progress(self):
        plan = self._make_plan(self.member, weeks=1)
        checkpoint = Checkpoint.objects.create(
            week=plan.weeks.get(), description="c",
        )

        response = self._patch(
            f"/member-api/v1/plans/{plan.id}/progress",
            {"checkpoints": [{"id": checkpoint.id, "done": True}]},
            plaintext=self.progress_plaintext,
        )

        self.assertEqual(response.status_code, 200)
        checkpoint.refresh_from_db()
        self.assertIsNotNone(checkpoint.done_at)

    def test_missing_key_returns_401(self):
        plan = self._make_plan(self.member)

        response = self.client.post(
            f"/member-api/v1/plans/{plan.id}/weeks",
            data=json.dumps({"week_number": 1}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "member_api_key_required")

    def test_method_not_allowed_returns_405(self):
        plan = self._make_plan(self.member)

        response = self.client.get(
            f"/member-api/v1/plans/{plan.id}/weeks", **self._auth(),
        )

        self.assertEqual(response.status_code, 405)


class BuildLoopIntegrationTest(MemberPlansWriteApiTestBase):
    def test_member_reshapes_plan_and_other_plans_untouched(self):
        plan = self._make_plan(self.member, weeks=4, title="Old title")
        untouched = self._make_plan(
            self.member, title="Second plan", sprint=self.sprint2,
        )
        week1 = plan.weeks.get(week_number=1)

        self.assertEqual(
            self._patch(
                f"/member-api/v1/plans/{plan.id}",
                {"title": "Ship an eval harness", "summary": {"goal": "Real goal"}},
            ).status_code,
            200,
        )
        for description in ["one", "two", "three"]:
            self.assertEqual(
                self._post(
                    f"/member-api/v1/plans/{plan.id}/weeks/{week1.id}/checkpoints",
                    {"description": description},
                ).status_code,
                201,
            )
        self.assertEqual(
            self._post(
                f"/member-api/v1/plans/{plan.id}/deliverables",
                {"description": "Working harness"},
            ).status_code,
            201,
        )

        detail = self._get_detail(plan)
        self.assertEqual(detail["title"], "Ship an eval harness")
        self.assertEqual(detail["summary"]["goal"], "Real goal")
        week1_data = next(w for w in detail["weeks"] if w["week_number"] == 1)
        self.assertEqual(
            [c["description"] for c in week1_data["checkpoints"]],
            ["one", "two", "three"],
        )
        self.assertEqual(len(detail["deliverables"]), 1)

        untouched.refresh_from_db()
        self.assertEqual(untouched.title, "Second plan")
        self.assertFalse(untouched.weeks.exists())

    def test_reordering_via_position_reflected_in_detail(self):
        plan = self._make_plan(self.member)
        first = self._post(
            f"/member-api/v1/plans/{plan.id}/deliverables",
            {"description": "alpha", "position": 5},
        ).json()
        second = self._post(
            f"/member-api/v1/plans/{plan.id}/deliverables",
            {"description": "beta", "position": 10},
        ).json()

        # Reorder: give beta a lower position than alpha.
        self.assertEqual(
            self._patch(
                f"/member-api/v1/plans/{plan.id}/deliverables/{second['id']}",
                {"position": 1},
            ).status_code,
            200,
        )

        detail = self._get_detail(plan)
        order = [d["description"] for d in detail["deliverables"]]
        self.assertEqual(order, ["beta", "alpha"])
        self.assertEqual(detail["deliverables"][0]["id"], second["id"])
        self.assertEqual(detail["deliverables"][1]["id"], first["id"])
