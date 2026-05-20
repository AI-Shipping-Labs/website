"""PATCH reconciliation for nested plan collections (issue #734).

The PATCH endpoint accepts the same full nested payload shape as POST.
For each collection key present in the payload, the server reconciles
the existing rows against the incoming list using id-presence as the
CREATE / UPDATE / DELETE signal. Top-level-only PATCH callers are
unaffected because none of the keys they send are collection keys.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import Token
from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    Week,
)

User = get_user_model()


class PlansPatchReconcileTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="s")
        cls.sprint = Sprint.objects.create(
            name="May 2026", slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6, status="active",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.staff_token.key}"}

    def _seed_plan(self):
        """Build a small but representative plan for reconcile tests."""
        plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
            status="draft",
            goal="orig goal",
            accountability="orig",
            focus_main="m",
            focus_supporting=["a"],
        )
        week1 = Week.objects.create(
            plan=plan, week_number=1, theme="warm-up", position=0,
        )
        week2 = Week.objects.create(
            plan=plan, week_number=2, theme="dive in", position=1,
        )
        cp1 = Checkpoint.objects.create(
            week=week1, description="cp1", position=0,
        )
        cp2 = Checkpoint.objects.create(
            week=week1, description="cp2", position=1,
        )
        cp3 = Checkpoint.objects.create(
            week=week2, description="cp3", position=0,
        )
        r1 = Resource.objects.create(
            plan=plan, title="r1", url="https://r1", position=0,
        )
        r2 = Resource.objects.create(
            plan=plan, title="r2", url="https://r2", position=1,
        )
        d1 = Deliverable.objects.create(
            plan=plan, description="d1", position=0,
        )
        n1 = NextStep.objects.create(
            plan=plan, description="n1", position=0,
        )
        return {
            "plan": plan,
            "week1": week1, "week2": week2,
            "cp1": cp1, "cp2": cp2, "cp3": cp3,
            "r1": r1, "r2": r2,
            "d1": d1, "n1": n1,
        }

    def _patch(self, plan_id, payload):
        return self.client.patch(
            f"/api/plans/{plan_id}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def _get(self, plan_id):
        response = self.client.get(
            f"/api/plans/{plan_id}", **self._auth(),
        )
        return response.json()


@tag("core")
class PatchReconcileWeeksTest(PlansPatchReconcileTestBase):
    """CREATE / UPDATE / DELETE + reorder of weeks."""

    def test_patch_creates_new_week_when_id_absent(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        # Send the two existing weeks unchanged (with ids) plus a new one
        # with no id — that new one must be CREATEd.
        response = self._patch(plan.id, {
            "weeks": [
                {"id": seed["week1"].id, "week_number": 1, "position": 0},
                {"id": seed["week2"].id, "week_number": 2, "position": 1},
                {"week_number": 3, "theme": "new", "position": 2},
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["weeks"]), 3)
        themes = {w["week_number"]: w["theme"] for w in body["weeks"]}
        self.assertEqual(themes[3], "new")
        # The new week has a real id (not None).
        ids = [w["id"] for w in body["weeks"]]
        self.assertTrue(all(i is not None for i in ids))
        self.assertIn(seed["week1"].id, ids)
        self.assertIn(seed["week2"].id, ids)

    def test_patch_updates_existing_week_by_id(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {
            "weeks": [
                {
                    "id": seed["week1"].id,
                    "week_number": 1,
                    "theme": "updated theme",
                    "position": 0,
                },
                {"id": seed["week2"].id, "week_number": 2, "position": 1},
            ],
        })
        self.assertEqual(response.status_code, 200)
        seed["week1"].refresh_from_db()
        self.assertEqual(seed["week1"].theme, "updated theme")

    def test_patch_deletes_omitted_week_and_cascades_checkpoints(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        # Omit week2 entirely; that row + its checkpoint must be gone.
        cp3_id = seed["cp3"].id
        response = self._patch(plan.id, {
            "weeks": [
                {"id": seed["week1"].id, "week_number": 1, "position": 0},
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Week.objects.filter(pk=seed["week2"].id).exists(),
        )
        # Cascade via FK on_delete=CASCADE.
        self.assertFalse(Checkpoint.objects.filter(pk=cp3_id).exists())
        # The retained week's checkpoints are untouched.
        self.assertTrue(
            Checkpoint.objects.filter(pk=seed["cp1"].id).exists(),
        )

    def test_patch_reorders_weeks_via_position(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        # Swap positions only; ids unchanged.
        response = self._patch(plan.id, {
            "weeks": [
                {"id": seed["week1"].id, "week_number": 1, "position": 5},
                {"id": seed["week2"].id, "week_number": 2, "position": 4},
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # Detail orders by (position, week_number) — week2 (pos 4)
        # must come before week1 (pos 5).
        self.assertEqual(body["weeks"][0]["id"], seed["week2"].id)
        self.assertEqual(body["weeks"][1]["id"], seed["week1"].id)
        self.assertEqual(body["weeks"][0]["position"], 4)
        self.assertEqual(body["weeks"][1]["position"], 5)

    def test_patch_weeks_empty_list_deletes_all_weeks(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {"weeks": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Week.objects.filter(plan=plan).count(), 0)
        # Cascade also cleared every checkpoint.
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(), 0,
        )

    def test_patch_weeks_absent_does_not_touch_weeks(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        # No collection keys in payload.
        response = self._patch(plan.id, {"status": "active"})
        self.assertEqual(response.status_code, 200)
        # Every week + checkpoint + resource is still there.
        self.assertEqual(Week.objects.filter(plan=plan).count(), 2)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(), 3,
        )

    def test_patch_week_id_from_other_plan_returns_422(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        other_plan = Plan.objects.create(
            member=User.objects.create_user(
                email="x@test.com", password="pw",
            ),
            sprint=self.sprint,
        )
        other_week = Week.objects.create(
            plan=other_plan, week_number=1,
        )
        before_themes = list(
            Week.objects.filter(plan=plan)
            .order_by("week_number")
            .values_list("theme", flat=True)
        )
        response = self._patch(plan.id, {
            "weeks": [
                {
                    "id": other_week.id,
                    "week_number": 1,
                    "theme": "stolen",
                },
            ],
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
        # Nothing on either plan changed.
        self.assertEqual(
            list(
                Week.objects.filter(plan=plan)
                .order_by("week_number")
                .values_list("theme", flat=True)
            ),
            before_themes,
        )


@tag("core")
class PatchReconcileCheckpointsTest(PlansPatchReconcileTestBase):
    """Per-week checkpoint reconciliation."""

    def test_patch_reconciles_checkpoints_create_update_delete_in_one_call(
        self,
    ):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {
            "weeks": [
                {
                    "id": seed["week1"].id,
                    "week_number": 1,
                    "position": 0,
                    "checkpoints": [
                        # UPDATE cp1
                        {
                            "id": seed["cp1"].id,
                            "description": "cp1 updated",
                            "position": 0,
                        },
                        # CREATE new
                        {
                            "description": "brand new cp",
                            "position": 2,
                        },
                        # cp2 omitted -> DELETE
                    ],
                },
                {
                    "id": seed["week2"].id, "week_number": 2, "position": 1,
                    "checkpoints": [
                        {"id": seed["cp3"].id, "description": "cp3"},
                    ],
                },
            ],
        })
        self.assertEqual(response.status_code, 200)

        # cp1 was UPDATEd
        seed["cp1"].refresh_from_db()
        self.assertEqual(seed["cp1"].description, "cp1 updated")
        # cp2 was DELETEd
        self.assertFalse(Checkpoint.objects.filter(pk=seed["cp2"].id).exists())
        # A brand new checkpoint exists on week1
        new_cps = Checkpoint.objects.filter(
            week=seed["week1"], description="brand new cp",
        )
        self.assertEqual(new_cps.count(), 1)

    def test_patch_rejects_cross_week_checkpoint_move(self):
        """Moving a checkpoint from one week to another in a single PATCH
        is out of scope for #734 — must be rejected with 422 and roll
        back all other changes in the same request."""
        seed = self._seed_plan()
        plan = seed["plan"]
        # Snapshot the state we expect to be preserved on failure.
        before_week1_theme = seed["week1"].theme
        before_resources = list(
            Resource.objects.filter(plan=plan)
            .order_by("position")
            .values_list("id", "title")
        )

        # Attempt: put cp3 (currently under week2) under week1.
        response = self._patch(plan.id, {
            "weeks": [
                {
                    "id": seed["week1"].id,
                    "week_number": 1,
                    "theme": "would-be-changed",
                    "checkpoints": [
                        {"id": seed["cp1"].id, "description": "cp1"},
                        # Cross-week move — cp3 belongs to week2.
                        {"id": seed["cp3"].id, "description": "cp3"},
                    ],
                },
                {"id": seed["week2"].id, "week_number": 2},
            ],
            # Also try to delete a resource in the same call so we can
            # prove rollback.
            "resources": [
                {"id": seed["r1"].id, "title": "r1", "position": 0},
            ],
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        # Rollback proof: week1 theme + resource list both unchanged.
        seed["week1"].refresh_from_db()
        self.assertEqual(seed["week1"].theme, before_week1_theme)
        self.assertEqual(
            list(
                Resource.objects.filter(plan=plan)
                .order_by("position")
                .values_list("id", "title")
            ),
            before_resources,
        )

    def test_patch_rejects_checkpoint_id_from_other_plan(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        other_plan = Plan.objects.create(
            member=User.objects.create_user(
                email="x2@test.com", password="pw",
            ),
            sprint=self.sprint,
        )
        other_week = Week.objects.create(plan=other_plan, week_number=1)
        other_cp = Checkpoint.objects.create(
            week=other_week, description="other plan cp",
        )

        response = self._patch(plan.id, {
            "weeks": [
                {
                    "id": seed["week1"].id, "week_number": 1,
                    "checkpoints": [
                        {"id": other_cp.id, "description": "x"},
                    ],
                },
                {"id": seed["week2"].id, "week_number": 2},
            ],
        })
        self.assertEqual(response.status_code, 422)
        # No changes on this plan.
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(), 3,
        )


@tag("core")
class PatchReconcileResourcesTest(PlansPatchReconcileTestBase):
    def test_patch_resources_mixed_crud(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {
            "resources": [
                # UPDATE r1 title
                {
                    "id": seed["r1"].id,
                    "title": "r1 new title",
                    "url": "https://r1",
                    "position": 0,
                },
                # CREATE a new resource
                {
                    "title": "freshly added",
                    "url": "https://new",
                    "position": 1,
                },
                # r2 omitted -> DELETE
            ],
        })
        self.assertEqual(response.status_code, 200)
        seed["r1"].refresh_from_db()
        self.assertEqual(seed["r1"].title, "r1 new title")
        self.assertFalse(Resource.objects.filter(pk=seed["r2"].id).exists())
        self.assertEqual(
            Resource.objects.filter(plan=plan, title="freshly added").count(),
            1,
        )

    def test_patch_deliverables_empty_list_clears_all(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        # Pre-seed an additional row to make the assertion meaningful.
        Deliverable.objects.create(
            plan=plan, description="d2", position=1,
        )
        response = self._patch(plan.id, {"deliverables": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Deliverable.objects.filter(plan=plan).count(), 0,
        )

    def test_patch_next_steps_roundtrips_done_at_iso(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        done_at_iso = "2026-05-20T10:00:00+00:00"
        response = self._patch(plan.id, {
            "next_steps": [
                {
                    "id": seed["n1"].id,
                    "description": "n1",
                    "position": 0,
                    "done_at": done_at_iso,
                },
            ],
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["next_steps"]), 1)
        # The done_at field round-trips as an ISO string.
        self.assertEqual(
            body["next_steps"][0]["done_at"], done_at_iso,
        )


@tag("core")
class PatchReconcileInterviewNotesTest(PlansPatchReconcileTestBase):
    """Option A: ``interview_notes`` participates in PATCH and is
    serialized in the response (visibility-filtered)."""

    def test_serialize_plan_detail_includes_interview_notes_for_staff(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        InterviewNote.objects.create(
            plan=plan, member=self.member,
            visibility="internal", kind="general", body="internal note",
        )
        InterviewNote.objects.create(
            plan=plan, member=self.member,
            visibility="external", kind="general", body="external note",
        )
        body = self._get(plan.id)
        self.assertIn("interview_notes", body)
        bodies = {n["body"] for n in body["interview_notes"]}
        self.assertEqual(bodies, {"internal note", "external note"})

    def test_patch_creates_interview_note(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        before = InterviewNote.objects.filter(plan=plan).count()
        response = self._patch(plan.id, {
            "interview_notes": [
                {
                    "visibility": "external",
                    "kind": "general",
                    "body": "shareable",
                },
            ],
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            InterviewNote.objects.filter(plan=plan).count(), before + 1,
        )

    def test_patch_updates_and_deletes_interview_notes(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        n1 = InterviewNote.objects.create(
            plan=plan, member=self.member,
            visibility="external", kind="general", body="keep me",
        )
        n2 = InterviewNote.objects.create(
            plan=plan, member=self.member,
            visibility="internal", kind="general", body="delete me",
        )
        response = self._patch(plan.id, {
            "interview_notes": [
                {"id": n1.id, "body": "updated"},
                # n2 omitted -> DELETE
            ],
        })
        self.assertEqual(response.status_code, 200)
        n1.refresh_from_db()
        self.assertEqual(n1.body, "updated")
        self.assertFalse(
            InterviewNote.objects.filter(pk=n2.id).exists(),
        )

    def test_patch_interview_note_invalid_visibility_returns_422(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        # Pre-existing data we'll prove stays intact on validation failure.
        before_status = plan.status
        response = self._patch(plan.id, {
            "status": "active",
            "interview_notes": [
                {"visibility": "nope", "kind": "general", "body": "x"},
            ],
        })
        self.assertEqual(response.status_code, 422)
        # status didn't change (atomic rollback).
        plan.refresh_from_db()
        self.assertEqual(plan.status, before_status)


@tag("core")
class PatchTopLevelOnlyRegressionTest(PlansPatchReconcileTestBase):
    """Existing PATCH callers that only send top-level fields must NOT
    see any behaviour change after issue #734."""

    def test_status_only_patch_does_not_touch_children(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        before_week_count = Week.objects.filter(plan=plan).count()
        before_cp_count = Checkpoint.objects.filter(week__plan=plan).count()
        before_resource_count = Resource.objects.filter(plan=plan).count()
        before_deliverable_count = Deliverable.objects.filter(plan=plan).count()
        before_next_step_count = NextStep.objects.filter(plan=plan).count()

        response = self._patch(plan.id, {"status": "active"})
        self.assertEqual(response.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "active")

        # Nothing in any child collection moved.
        self.assertEqual(
            Week.objects.filter(plan=plan).count(), before_week_count,
        )
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(),
            before_cp_count,
        )
        self.assertEqual(
            Resource.objects.filter(plan=plan).count(), before_resource_count,
        )
        self.assertEqual(
            Deliverable.objects.filter(plan=plan).count(),
            before_deliverable_count,
        )
        self.assertEqual(
            NextStep.objects.filter(plan=plan).count(),
            before_next_step_count,
        )

    def test_goal_focus_summary_patch_does_not_touch_children(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        before_cp_ids = set(
            Checkpoint.objects.filter(week__plan=plan)
            .values_list("id", flat=True)
        )
        response = self._patch(plan.id, {
            "goal": "new goal",
            "focus": {"main": "focus", "supporting": ["x", "y"]},
            "summary": {"current_situation": "ok"},
        })
        self.assertEqual(response.status_code, 200)
        after_cp_ids = set(
            Checkpoint.objects.filter(week__plan=plan)
            .values_list("id", flat=True)
        )
        self.assertEqual(before_cp_ids, after_cp_ids)

    def test_plan_id_unchanged_across_reconcile_patch(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        original_id = plan.id
        original_content_id = plan.comment_content_id

        response = self._patch(plan.id, {
            "weeks": [
                {"id": seed["week1"].id, "week_number": 1},
            ],
            "resources": [],
        })
        self.assertEqual(response.status_code, 200)
        plan.refresh_from_db()
        self.assertEqual(plan.id, original_id)
        # Regression catch: comment bridge id is stable so existing
        # comments / notifications still resolve to this plan.
        self.assertEqual(plan.comment_content_id, original_content_id)
        # Only one Plan row for this (member, sprint).
        self.assertEqual(
            Plan.objects.filter(member=self.member, sprint=self.sprint)
            .count(),
            1,
        )


@tag("core")
class PatchReconcileAtomicityTest(PlansPatchReconcileTestBase):
    """The whole PATCH must be atomic: any failure rolls back every
    change made earlier in the same request."""

    def test_mid_reconcile_validation_failure_rolls_back_earlier_writes(
        self,
    ):
        """Earlier in the same PATCH we mutate resources successfully;
        then a later check (interview_notes invalid kind) fails — the
        resource mutation must NOT persist."""
        seed = self._seed_plan()
        plan = seed["plan"]
        before_resource_titles = {
            r.id: r.title for r in Resource.objects.filter(plan=plan)
        }
        response = self._patch(plan.id, {
            "resources": [
                {
                    "id": seed["r1"].id,
                    "title": "would-be-changed",
                    "url": "https://r1",
                },
            ],
            "interview_notes": [
                {"visibility": "external", "kind": "not-a-kind", "body": "x"},
            ],
        })
        self.assertEqual(response.status_code, 422)
        # Resource title must NOT have changed.
        after_resource_titles = {
            r.id: r.title for r in Resource.objects.filter(plan=plan)
        }
        self.assertEqual(before_resource_titles, after_resource_titles)

    def test_duplicate_week_number_triggers_integrity_error_and_rolls_back(
        self,
    ):
        """``unique_week_number_per_plan`` is enforced at the DB layer.
        A PATCH that would create a duplicate must surface an error
        without committing any of the writes."""
        seed = self._seed_plan()
        plan = seed["plan"]
        before_count = Week.objects.filter(plan=plan).count()
        from django.db import IntegrityError

        # Send a new week with week_number=1 (already taken by week1).
        # The reconciler creates the row inside the atomic block; the
        # constraint fires on commit. We catch the exception here so
        # the test asserts the data state, not the exception type.
        try:
            self._patch(plan.id, {
                "weeks": [
                    {"id": seed["week1"].id, "week_number": 1, "position": 0},
                    {"id": seed["week2"].id, "week_number": 2, "position": 1},
                    # CREATE with a duplicate week_number on the same plan.
                    {"week_number": 1, "theme": "dup"},
                ],
            })
        except IntegrityError:
            # Some DBs raise immediately on the second insert; the
            # important assertion is that no extra row was committed.
            pass
        # No new week committed.
        self.assertEqual(Week.objects.filter(plan=plan).count(), before_count)

    def test_top_level_failure_rolls_back_nested_reconcile(self):
        """A top-level validation error (e.g. goal > 280 chars) must
        roll back any nested writes that already happened earlier in
        the same PATCH."""
        seed = self._seed_plan()
        plan = seed["plan"]
        before_resource_ids = set(
            Resource.objects.filter(plan=plan).values_list("id", flat=True),
        )
        response = self._patch(plan.id, {
            # Long goal triggers a validation error in
            # _apply_top_level_fields.
            "goal": "x" * 281,
            "resources": [],  # would delete every resource.
        })
        self.assertEqual(response.status_code, 422)
        # Resources untouched.
        self.assertEqual(
            set(
                Resource.objects.filter(plan=plan)
                .values_list("id", flat=True),
            ),
            before_resource_ids,
        )


@tag("core")
class PatchReconcileSharedValidatorsTest(PlansPatchReconcileTestBase):
    """The validators extracted from ``_create_plan_from_payload`` are
    re-exercised through PATCH to prove POST and PATCH cannot drift."""

    def test_weeks_not_a_list_returns_invalid_type(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {"weeks": "not-a-list"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "invalid_type")

    def test_focus_supporting_not_a_list_returns_422(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {
            "focus": {"supporting": "not-a-list"},
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_overlong_goal_returns_422(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {"goal": "x" * 281})
        self.assertEqual(response.status_code, 422)

    def test_invalid_status_returns_422(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {"status": "not-a-status"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_weeks_entry_not_a_dict_returns_422(self):
        seed = self._seed_plan()
        plan = seed["plan"]
        response = self._patch(plan.id, {"weeks": ["not-a-dict"]})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")
