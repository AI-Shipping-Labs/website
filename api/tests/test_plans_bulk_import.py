"""Tests for ``POST /api/sprints/<slug>/plans/bulk-import`` (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import (
    Checkpoint,
    Deliverable,
    InterviewNote,
    NextStep,
    Plan,
    Resource,
    Sprint,
)

User = get_user_model()


class BulkImportTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="s")
        cls.member1 = User.objects.create_user(
            email="m1@test.com", password="pw",
        )
        cls.member2 = User.objects.create_user(
            email="m2@test.com", password="pw",
        )
        cls.member3 = User.objects.create_user(
            email="m3@test.com", password="pw",
        )
        cls.non_staff_token = Token.objects.create(
            user=cls.member1, name="m",
        )
        cls.sprint = Sprint.objects.create(
            name="May 2026", slug="may-2026",
            start_date=datetime.date(2026, 5, 1),
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _post(self, payload, *, token=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        return self.client.post(
            "/api/sprints/may-2026/plans/bulk-import",
            data=body,
            content_type="application/json",
            **self._auth(token),
        )


class BulkImportHappyPathTest(BulkImportTestBase):
    def test_creates_full_tree(self):
        before = Plan.objects.count()
        response = self._post({
            "plans": [
                {
                    "user_email": "m1@test.com",
                    "status": "shared",
                    "summary": {
                        "current_situation": "now",
                        "goal": "later",
                    },
                    "focus": {
                        "main": "RAG",
                        "supporting": ["evals", "guardrails"],
                    },
                    "accountability": "Slack standup",
                    "weeks": [
                        {
                            "week_number": 1, "theme": "warm-up",
                            "checkpoints": [
                                {"description": "read paper"},
                                {"description": "build prototype"},
                            ],
                        },
                        {
                            "week_number": 2, "theme": "build",
                            "checkpoints": [{"description": "ship"}],
                        },
                    ],
                    "resources": [
                        {"title": "blog", "url": "https://x", "note": ""},
                    ],
                    "deliverables": [
                        {"description": "demo video"},
                    ],
                    "next_steps": [
                        {
                            "description": "join channel",
                        },
                    ],
                    "interview_notes": [
                        {
                            "visibility": "internal",
                            "kind": "intake",
                            "body": "intake details",
                        },
                    ],
                },
            ],
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["created"], 1)
        self.assertEqual(Plan.objects.count(), before + 1)

        plan = Plan.objects.get(member=self.member1, sprint=self.sprint)
        self.assertEqual(plan.status, "shared")
        self.assertEqual(plan.focus_supporting, ["evals", "guardrails"])
        self.assertEqual(plan.weeks.count(), 2)
        self.assertEqual(
            Checkpoint.objects.filter(week__plan=plan).count(), 3,
        )
        self.assertEqual(Resource.objects.filter(plan=plan).count(), 1)
        self.assertEqual(Deliverable.objects.filter(plan=plan).count(), 1)
        self.assertEqual(NextStep.objects.filter(plan=plan).count(), 1)
        self.assertEqual(
            InterviewNote.objects.filter(plan=plan).count(), 1,
        )
        # Internal interview note created for staff -- this is the
        # motivating use case for bulk import.
        self.assertEqual(
            InterviewNote.objects.get(plan=plan).visibility, "internal",
        )

    def test_bulk_import_with_three_plans(self):
        before = Plan.objects.count()
        payload = {
            "plans": [
                {"user_email": email}
                for email in ("m1@test.com", "m2@test.com", "m3@test.com")
            ],
        }
        response = self._post(payload)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["created"], 3)
        self.assertEqual(len(body["plan_ids"]), 3)
        self.assertEqual(Plan.objects.count(), before + 3)


class BulkImportFailureModesTest(BulkImportTestBase):
    def test_atomic_on_unknown_user_in_second_row(self):
        before = Plan.objects.count()
        response = self._post({
            "plans": [
                {"user_email": "m1@test.com"},
                {"user_email": "nobody@test.com"},
                {"user_email": "m3@test.com"},
            ],
        })
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "unknown_user")
        self.assertEqual(body["details"]["index"], 1)
        # Atomic: zero plans created even though row 0 succeeded.
        self.assertEqual(Plan.objects.count(), before)

    def test_duplicate_returns_409_with_index(self):
        Plan.objects.create(member=self.member1, sprint=self.sprint)
        before = Plan.objects.count()
        response = self._post({
            "plans": [
                {"user_email": "m2@test.com"},
                {"user_email": "m1@test.com"},  # duplicate
            ],
        })
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "duplicate_plan")
        self.assertEqual(body["details"]["index"], 1)
        # Atomic: m2's plan rolled back.
        self.assertEqual(Plan.objects.count(), before)

    def test_non_staff_returns_403(self):
        before = Plan.objects.count()
        response = self._post(
            {"plans": [{"user_email": "m1@test.com"}]},
            token=self.non_staff_token,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["code"], "forbidden_other_user_plan",
        )
        self.assertEqual(Plan.objects.count(), before)

    def test_invalid_json_returns_400(self):
        response = self._post(None, raw_body="not-json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})

    def test_missing_plans_key_returns_400(self):
        response = self._post({})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_field")
        self.assertEqual(body["details"]["field"], "plans")

    def test_unknown_sprint_returns_404(self):
        response = self.client.post(
            "/api/sprints/nope/plans/bulk-import",
            data=json.dumps({"plans": []}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_sprint")
