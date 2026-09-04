"""Tests for the member-facing Plans API."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import MemberAPIKey, Token
from comments.models import Comment
from crm.models import CRMRecord
from member_api.serializers.plans import serialize_member_plan_detail
from plans.markdown_export import render_plan_markdown_export
from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
    SprintEnrollment,
    Week,
    WeekNote,
)

User = get_user_model()


class MemberPlansApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email="member-api-owner@test.com",
            first_name="Owner",
        )
        cls.other = User.objects.create_user(
            email="member-api-other@test.com",
            first_name="Other",
        )
        cls.staff = User.objects.create_user(
            email="member-api-staff@test.com",
            is_staff=True,
        )
        cls.key, cls.plaintext = MemberAPIKey.create_for_user(
            user=cls.member,
            name="local tool",
        )
        cls.other_key, cls.other_plaintext = MemberAPIKey.create_for_user(
            user=cls.other,
            name="other tool",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="operator")
        cls.sprint = Sprint.objects.create(
            name="May 2026",
            slug="member-api-may-2026",
            start_date=datetime.date(2026, 5, 1),
            duration_weeks=6,
            status="active",
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.other)

    def _auth(self, plaintext=None):
        return {"HTTP_AUTHORIZATION": f"Token {plaintext or self.plaintext}"}

    def _patch_json(self, path, payload, *, plaintext=None):
        return self.client.patch(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(plaintext),
        )

    def _create_plan(self, member, *, title="Member API Plan", visibility="private"):
        plan = Plan.objects.create(
            member=member,
            sprint=self.sprint,
            title=title,
            visibility=visibility,
            goal="Ship a member-safe tool",
            summary_current_situation="Current public plan context",
            summary_goal="Goal public plan context",
            summary_main_gap="Gap public plan context",
            summary_weekly_hours="4h",
            summary_why_this_plan="Because it matters",
            focus_main="Focus on delivery",
            focus_supporting=["Use tests"],
            accountability="Post weekly updates",
            shared_at=timezone.now(),
        )
        week = Week.objects.create(
            plan=plan,
            week_number=1,
            theme="Build the API",
        )
        Checkpoint.objects.create(
            week=week,
            description="Wire endpoints",
            position=1,
        )
        Resource.objects.create(
            plan=plan,
            title="Docs",
            url="https://example.com/docs",
            note="Read this",
            position=1,
        )
        Deliverable.objects.create(
            plan=plan,
            description="Working client",
            position=1,
        )
        NextStep.objects.create(
            plan=plan,
            description="Send progress",
            position=1,
        )
        WeekNote.objects.create(
            week=week,
            author=member,
            body="Member-visible weekly note",
        )
        return plan


@tag("core")
class MemberPlansApiReadTest(MemberPlansApiTestBase):
    def test_member_lists_only_owned_plans(self):
        owned = self._create_plan(self.member, title="Owned")
        other = self._create_plan(self.other, title="Other")

        response = self.client.get("/member-api/v1/plans", **self._auth())

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ids = {row["id"] for row in payload["plans"]}
        self.assertIn(owned.id, ids)
        self.assertNotIn(other.id, ids)
        self.assertEqual(
            payload["pagination"],
            {
                "page": 1,
                "page_size": 20,
                "total": 1,
                "total_pages": 1,
            },
        )
        body = json.dumps(payload)
        self.assertNotIn("member-api-other@test.com", body)
        self.assertNotIn("user_email", body)

    def test_empty_owner_list_includes_zero_pagination(self):
        response = self.client.get("/member-api/v1/plans", **self._auth())

        self.assertEqual(
            response.json(),
            {
                "plans": [],
                "pagination": {
                    "page": 1,
                    "page_size": 20,
                    "total": 0,
                    "total_pages": 0,
                },
            },
        )

    def test_list_paginates_newest_first_and_rejects_invalid_pages(self):
        plans = []
        for index in range(22):
            sprint = Sprint.objects.create(
                name=f"Page sprint {index:02d}",
                slug=f"member-api-page-{index:02d}",
                start_date=datetime.date(2026, 5, 1),
                duration_weeks=6,
                status="active",
            )
            plans.append(
                Plan.objects.create(
                    member=self.member,
                    sprint=sprint,
                    title=f"Plan {index:02d}",
                )
            )
        newest_first = list(reversed(plans))

        first_page = self.client.get(
            "/member-api/v1/plans?page=1",
            **self._auth(),
        )
        second_page = self.client.get(
            "/member-api/v1/plans?page=2",
            **self._auth(),
        )

        first_body = first_page.json()
        second_body = second_page.json()
        self.assertEqual(
            [row["id"] for row in first_body["plans"]],
            [plan.id for plan in newest_first[:20]],
        )
        self.assertEqual(
            [row["id"] for row in second_body["plans"]],
            [plan.id for plan in newest_first[20:]],
        )
        self.assertEqual(
            first_body["pagination"],
            {
                "page": 1,
                "page_size": 20,
                "total": 22,
                "total_pages": 2,
            },
        )
        self.assertEqual(second_body["pagination"]["page"], 2)
        self.assertEqual(second_body["pagination"]["total"], 22)
        self.assertEqual(second_body["pagination"]["total_pages"], 2)

        for invalid_page in ("0", "-1", "abc", "1.5"):
            with self.subTest(page=invalid_page):
                response = self.client.get(
                    f"/member-api/v1/plans?page={invalid_page}",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 422)
                payload = response.json()
                self.assertEqual(payload["code"], "validation_error")
                self.assertIn("page", payload["details"])
                self.assertNotIn("plans", payload)

    def test_detail_uses_prefetched_child_order(self):
        plan = Plan.objects.create(
            member=self.member,
            sprint=self.sprint,
            title="Ordered plan",
        )
        later = Week.objects.create(plan=plan, week_number=1, position=2, theme="Later")
        earlier = Week.objects.create(plan=plan, week_number=2, position=1, theme="Earlier")
        Checkpoint.objects.create(week=earlier, description="Second", position=2)
        Checkpoint.objects.create(week=earlier, description="First", position=1)
        Resource.objects.create(plan=plan, title="Second resource", position=2)
        Resource.objects.create(plan=plan, title="First resource", position=1)

        response = self.client.get(f"/member-api/v1/plans/{plan.id}", **self._auth())

        body = response.json()
        self.assertEqual(
            [week["id"] for week in body["weeks"]],
            [earlier.id, later.id],
        )
        self.assertEqual(
            [item["description"] for item in body["weeks"][0]["checkpoints"]],
            ["First", "Second"],
        )
        self.assertEqual(
            [item["title"] for item in body["resources"]],
            ["First resource", "Second resource"],
        )

    def test_owner_reads_safe_plan_detail_without_internal_context(self):
        plan = self._create_plan(self.member)
        CRMRecord.objects.create(
            user=self.member,
            persona="Staff-only persona",
            summary="Staff-only CRM summary",
            next_steps="Staff-only CRM next steps",
        )
        InterviewNote.objects.create(
            plan=plan,
            member=self.member,
            visibility="internal",
            body="Internal interview note",
            created_by=self.staff,
        )
        InterviewNote.objects.create(
            plan=plan,
            member=self.member,
            visibility="external",
            body="External interview note still not member API v1",
            created_by=self.staff,
        )
        Comment.objects.create(
            content_id=plan.comment_content_id,
            user=self.other,
            body="Plan comment should not leak",
        )

        response = self.client.get(
            f"/member-api/v1/plans/{plan.id}",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        serialized = json.dumps(body)
        self.assertEqual(body["id"], plan.id)
        self.assertEqual(body["weeks"][0]["note"]["body"], "Member-visible weekly note")
        self.assertEqual(body["member"]["display_name"], "Owner")
        self.assertNotIn("user_email", serialized)
        self.assertNotIn("author_email", serialized)
        self.assertNotIn("interview_notes", body)
        for forbidden in (
            "Internal interview note",
            "External interview note",
            "Staff-only persona",
            "Staff-only CRM summary",
            "Staff-only CRM next steps",
            "Plan comment should not leak",
            "member-api-owner@test.com",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_cohort_visibility_does_not_grant_api_read_access(self):
        plan = self._create_plan(self.member, visibility="cohort")

        response = self.client.get(
            f"/member-api/v1/plans/{plan.id}",
            **self._auth(self.other_plaintext),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "plan_not_found")

    def test_markdown_returns_shared_member_safe_export(self):
        plan = self._create_plan(self.member)

        response = self.client.get(
            f"/member-api/v1/plans/{plan.id}/markdown",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "text/markdown; charset=utf-8",
        )
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertEqual(response.content.decode(), render_plan_markdown_export(plan))

    def test_serializer_shape_has_no_forbidden_internal_keys(self):
        plan = self._create_plan(self.member)

        body = serialize_member_plan_detail(plan)
        serialized = json.dumps(body)

        for forbidden in (
            "user_email",
            "interview_notes",
            "comment_content_id",
            "source_metadata",
        ):
            self.assertNotIn(forbidden, serialized)


@tag("core")
class MemberPlansApiProgressTest(MemberPlansApiTestBase):
    def test_progress_updates_owned_items_atomically(self):
        plan = self._create_plan(self.member)
        checkpoint = plan.weeks.get().checkpoints.get()
        deliverable = plan.deliverables.get()
        next_step = plan.next_steps.get()
        deliverable.done_at = timezone.now()
        deliverable.save(update_fields=["done_at", "updated_at"])

        response = self._patch_json(
            f"/member-api/v1/plans/{plan.id}/progress",
            {
                "checkpoints": [{"id": checkpoint.id, "done": True}],
                "deliverables": [{"id": deliverable.id, "done": False}],
                "next_steps": [{"id": next_step.id, "done": True}],
            },
        )

        self.assertEqual(response.status_code, 200)
        checkpoint.refresh_from_db()
        deliverable.refresh_from_db()
        next_step.refresh_from_db()
        self.assertIsNotNone(checkpoint.done_at)
        self.assertIsNone(deliverable.done_at)
        self.assertIsNotNone(next_step.done_at)
        self.assertEqual(response.json()["progress"]["checkpoints_done"], 1)

    def test_invalid_progress_payload_rolls_back(self):
        plan = self._create_plan(self.member)
        other_plan = self._create_plan(self.other)
        checkpoint = plan.weeks.get().checkpoints.get()
        other_checkpoint = other_plan.weeks.get().checkpoints.get()

        response = self._patch_json(
            f"/member-api/v1/plans/{plan.id}/progress",
            {
                "checkpoints": [
                    {"id": checkpoint.id, "done": True},
                    {"id": other_checkpoint.id, "done": True},
                ],
            },
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "item_not_found")
        checkpoint.refresh_from_db()
        other_checkpoint.refresh_from_db()
        self.assertIsNone(checkpoint.done_at)
        self.assertIsNone(other_checkpoint.done_at)

    def test_progress_rejects_unknown_collection_duplicate_and_non_boolean(self):
        plan = self._create_plan(self.member)
        checkpoint = plan.weeks.get().checkpoints.get()

        cases = [
            ({"resources": [{"id": 1, "done": True}]}, "unknown_collection"),
            (
                {
                    "checkpoints": [
                        {"id": checkpoint.id, "done": True},
                        {"id": checkpoint.id, "done": False},
                    ],
                },
                "duplicate_id",
            ),
            (
                {"checkpoints": [{"id": checkpoint.id, "done": "yes"}]},
                "validation_error",
            ),
        ]

        for payload, code in cases:
            with self.subTest(code=code):
                response = self._patch_json(
                    f"/member-api/v1/plans/{plan.id}/progress",
                    payload,
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], code)


@tag("core")
class MemberPlansApiAuthTest(MemberPlansApiTestBase):
    def test_operator_token_and_browser_session_do_not_use_member_api(self):
        plan = self._create_plan(self.member)

        operator = self.client.get(
            "/member-api/v1/plans",
            HTTP_AUTHORIZATION=f"Token {self.staff_token.key}",
        )
        self.assertEqual(operator.status_code, 401)
        self.assertEqual(operator.json()["code"], "invalid_member_api_key")

        self.client.force_login(self.member)
        session_response = self.client.get(f"/member-api/v1/plans/{plan.id}")
        self.assertEqual(session_response.status_code, 401)
        self.assertEqual(
            session_response.json()["code"],
            "member_api_key_required",
        )

    def test_member_key_cannot_use_operator_api(self):
        plan = self._create_plan(self.member)

        openapi = self.client.get(
            "/api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {self.plaintext}",
        )
        detail = self.client.get(
            f"/api/plans/{plan.id}",
            HTTP_AUTHORIZATION=f"Token {self.plaintext}",
        )

        self.assertEqual(openapi.status_code, 401)
        self.assertEqual(detail.status_code, 401)
