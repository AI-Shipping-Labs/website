"""Cross-cutting auth matrix for the plans API (issue #433).

A single test class that walks every endpoint and asserts:

- No ``Authorization`` header -> 401.
- Bogus token -> 401.
- Wrong HTTP method -> 405.

The point of this matrix is to catch a future endpoint added without
``@token_required`` or without ``@require_methods``. Adding an endpoint
adds one tuple to the table.
"""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Plan, Sprint

User = get_user_model()


# (method_for_a_valid_call, path, body_or_None, allowed_methods_set)
# Used by every test in this module so the matrix stays in one place.
def _build_endpoints(plan_id, sprint_slug, week_id, checkpoint_id, note_id,
                     resource_id, deliverable_id, next_step_id, email):
    return [
        # Sprints
        (
            "GET", "/api/sprints", None,
            {"GET", "POST"},
        ),
        (
            "GET", f"/api/sprints/{sprint_slug}", None,
            {"GET", "PATCH", "DELETE"},
        ),
        # Plans
        (
            "GET", f"/api/sprints/{sprint_slug}/plans", None,
            {"GET", "POST"},
        ),
        (
            "POST", f"/api/sprints/{sprint_slug}/plans/bulk-import",
            {"plans": []},
            {"POST"},
        ),
        (
            "GET", f"/api/plans/{plan_id}", None,
            {"GET", "PATCH", "DELETE"},
        ),
        # Weeks
        (
            "GET", f"/api/plans/{plan_id}/weeks", None,
            {"GET", "POST"},
        ),
        (
            "PATCH", f"/api/weeks/{week_id}", {},
            {"PATCH", "DELETE"},
        ),
        # Checkpoints
        (
            "POST", f"/api/weeks/{week_id}/checkpoints",
            {"description": "x"},
            {"POST"},
        ),
        (
            "PATCH", f"/api/checkpoints/{checkpoint_id}", {},
            {"PATCH", "DELETE"},
        ),
        (
            "POST", f"/api/checkpoints/{checkpoint_id}/move",
            {"week_id": week_id, "position": 0},
            {"POST"},
        ),
        # Plan items
        (
            "GET", f"/api/plans/{plan_id}/resources", None,
            {"GET", "POST"},
        ),
        (
            "PATCH", f"/api/resources/{resource_id}", {},
            {"PATCH", "DELETE"},
        ),
        (
            "GET", f"/api/plans/{plan_id}/deliverables", None,
            {"GET", "POST"},
        ),
        (
            "PATCH", f"/api/deliverables/{deliverable_id}", {},
            {"PATCH", "DELETE"},
        ),
        (
            "GET", f"/api/plans/{plan_id}/next-steps", None,
            {"GET", "POST"},
        ),
        (
            "PATCH", f"/api/next-steps/{next_step_id}", {},
            {"PATCH", "DELETE"},
        ),
        # Interview notes
        (
            "GET", f"/api/plans/{plan_id}/interview-notes", None,
            {"GET"},
        ),
        (
            "GET", f"/api/users/{email}/interview-notes", None,
            {"GET"},
        ),
        (
            "POST", "/api/interview-notes",
            {"user_email": email, "body": "x"},
            {"POST"},
        ),
        (
            "GET", f"/api/interview-notes/{note_id}", None,
            {"GET", "PATCH", "DELETE"},
        ),
    ]


class AuthMatrixTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="s")

    def setUp(self):
        # Fixtures need to actually exist so we exercise real endpoints
        # rather than 404-ing past the auth check. We don't care about
        # the response code on the happy path; only the auth surface.
        from plans.models import (
            Checkpoint,
            Deliverable,
            InterviewNote,
            NextStep,
            Resource,
            Week,
        )

        self.sprint = Sprint.objects.create(
            name="s", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        self.member = User.objects.create_user(
            email="member@test.com", password="pw",
        )
        self.plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )
        self.week = Week.objects.create(plan=self.plan, week_number=1)
        self.cp = Checkpoint.objects.create(
            week=self.week, description="x", position=0,
        )
        self.note = InterviewNote.objects.create(
            plan=self.plan, member=self.member, body="x", visibility="external",
        )
        self.resource = Resource.objects.create(
            plan=self.plan, title="r", position=0,
        )
        self.deliverable = Deliverable.objects.create(
            plan=self.plan, description="d", position=0,
        )
        self.next_step = NextStep.objects.create(
            plan=self.plan, description="ns", position=0,
        )
        self.endpoints = _build_endpoints(
            plan_id=self.plan.id,
            sprint_slug=self.sprint.slug,
            week_id=self.week.id,
            checkpoint_id=self.cp.id,
            note_id=self.note.id,
            resource_id=self.resource.id,
            deliverable_id=self.deliverable.id,
            next_step_id=self.next_step.id,
            email="member@test.com",
        )

    def _call(self, method, path, body, *, headers=None):
        kwargs = {"content_type": "application/json", **(headers or {})}
        if method == "GET":
            return self.client.get(path, **(headers or {}))
        if method == "DELETE":
            return self.client.delete(path, **(headers or {}))
        data = json.dumps(body) if body is not None else "{}"
        if method == "POST":
            return self.client.post(path, data=data, **kwargs)
        if method == "PATCH":
            return self.client.patch(path, data=data, **kwargs)
        raise AssertionError(f"unexpected method {method}")

    def test_no_header_returns_401_for_every_endpoint(self):
        before = Plan.objects.count()
        for method, path, body, _allowed in self.endpoints:
            with self.subTest(method=method, path=path):
                response = self._call(method, path, body)
                self.assertEqual(
                    response.status_code, 401,
                    msg=f"{method} {path} returned {response.status_code}",
                )
        # No side effects from any of the unauthorized calls.
        self.assertEqual(Plan.objects.count(), before)

    def test_invalid_token_returns_401_for_every_endpoint(self):
        for method, path, body, _allowed in self.endpoints:
            with self.subTest(method=method, path=path):
                response = self._call(
                    method, path, body,
                    headers={"HTTP_AUTHORIZATION": "Token nope"},
                )
                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json(), {"error": "Invalid token"},
                )

    def test_wrong_method_returns_405_for_every_endpoint(self):
        # Map of the four primary verbs we test against.
        all_methods = ["GET", "POST", "PATCH", "DELETE"]
        auth = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}
        for _method, path, _body, allowed in self.endpoints:
            for verb in all_methods:
                if verb in allowed:
                    continue
                with self.subTest(verb=verb, path=path):
                    response = self._call(
                        verb, path, body=None, headers=auth,
                    )
                    self.assertEqual(
                        response.status_code, 405,
                        msg=(
                            f"{verb} {path} returned {response.status_code}, "
                            f"expected 405"
                        ),
                    )
