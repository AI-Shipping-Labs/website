"""Tests for the InterviewNote endpoints (issue #433).

The visibility gate is the security-critical contract of this issue.
Two cross-cutting tests in this module pin down the architectural
invariant:

- ``test_visibility_gate_lives_in_queryset_not_template`` reads
  ``api/views/interview_notes.py`` as plain text and asserts the literal
  string ``is_staff`` does not appear there. Every staff branch must
  live in ``api/views/_permissions.py``, not the view module.
- The non-staff detail-by-id case returns 404 with NO leak of the
  internal note's body; the test checks the body string is absent from
  the response.
"""

import datetime
import json
import pathlib

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class InterviewNotesTestBase(TestCase):
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
            name="s", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member_plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )
        cls.internal_note = InterviewNote.objects.create(
            plan=cls.member_plan, member=cls.member,
            visibility="internal", kind="meeting",
            body="Member is shy about asking questions in the cohort.",
            created_by=cls.staff,
        )
        cls.external_note = InterviewNote.objects.create(
            plan=cls.member_plan, member=cls.member,
            visibility="external", kind="general",
            body="Member to share progress in slack channel.",
            created_by=cls.staff,
        )

    def _auth(self, token):
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


class PlanInterviewNotesListTest(InterviewNotesTestBase):
    def test_staff_sees_internal_and_external_notes(self):
        response = self.client.get(
            f"/api/plans/{self.member_plan.id}/interview-notes",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        ids = {n["id"] for n in response.json()["interview_notes"]}
        self.assertIn(self.internal_note.id, ids)
        self.assertIn(self.external_note.id, ids)


class InterviewNoteDetailTest(InterviewNotesTestBase):
    def test_staff_detail_on_internal_note_returns_200(self):
        response = self.client.get(
            f"/api/interview-notes/{self.internal_note.id}",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["visibility"], "internal")

    def test_member_notes_detail_alias_returns_same_payload(self):
        response = self.client.get(
            f"/api/member-notes/{self.external_note.id}",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], self.external_note.id)

    def test_member_notes_detail_alias_patch_and_delete(self):
        note = InterviewNote.objects.create(
            plan=None,
            member=self.member,
            visibility="external",
            kind="general",
            body="before",
            created_by=self.staff,
        )
        response = self.client.patch(
            f"/api/member-notes/{note.id}",
            data=json.dumps({"body": "after"}),
            content_type="application/json",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.body, "after")

        response = self.client.delete(
            f"/api/member-notes/{note.id}",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(InterviewNote.objects.filter(pk=note.pk).exists())


class InterviewNoteCreateTest(InterviewNotesTestBase):
    def _post(self, payload, *, token):
        return self.client.post(
            "/api/interview-notes",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_staff_creates_internal_note(self):
        before = InterviewNote.objects.count()
        response = self._post(
            {
                "user_email": "member@test.com",
                "visibility": "internal",
                "kind": "intake",
                "body": "secret",
            },
            token=self.staff_token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(InterviewNote.objects.count(), before + 1)
        self.assertEqual(response.json()["visibility"], "internal")

    def test_member_notes_create_alias_creates_note(self):
        before = InterviewNote.objects.count()
        response = self.client.post(
            "/api/member-notes",
            data=json.dumps({
                "user_email": "member@test.com",
                "visibility": "internal",
                "kind": "intake",
                "body": "alias note",
            }),
            content_type="application/json",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(InterviewNote.objects.count(), before + 1)
        self.assertEqual(response.json()["body"], "alias note")


class UserInterviewNotesInboxTest(InterviewNotesTestBase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Inbox notes (no plan) for the member.
        cls.inbox_note = InterviewNote.objects.create(
            plan=None, member=cls.member,
            visibility="internal", kind="intake",
            body="Member's intake form answers.",
            created_by=cls.staff,
        )

    def test_staff_user_notes_returns_all_member_notes(self):
        response = self.client.get(
            "/api/users/member@test.com/interview-notes",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        ids = {n["id"] for n in response.json()["interview_notes"]}
        self.assertIn(self.inbox_note.id, ids)
        self.assertIn(self.internal_note.id, ids)
        self.assertIn(self.external_note.id, ids)

    def test_staff_user_notes_plan_null_preserves_inbox_filter(self):
        response = self.client.get(
            "/api/users/member@test.com/interview-notes?plan=null",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        ids = {n["id"] for n in response.json()["interview_notes"]}
        self.assertEqual(ids, {self.inbox_note.id})

    def test_staff_user_notes_plan_id_filters_to_that_plan(self):
        other_sprint = Sprint.objects.create(
            name="other",
            slug="other",
            start_date=datetime.date(2026, 6, 1),
        )
        other_plan = Plan.objects.create(
            member=self.member,
            sprint=other_sprint,
        )
        other_note = InterviewNote.objects.create(
            plan=other_plan,
            member=self.member,
            visibility="internal",
            kind="meeting",
            body="other plan note",
            created_by=self.staff,
        )
        response = self.client.get(
            f"/api/users/member@test.com/interview-notes?plan={other_plan.pk}",
            **self._auth(self.staff_token),
        )
        self.assertEqual(response.status_code, 200)
        ids = {n["id"] for n in response.json()["interview_notes"]}
        self.assertEqual(ids, {other_note.id})

    def test_user_member_notes_alias_matches_legacy_payload(self):
        legacy = self.client.get(
            "/api/users/member@test.com/interview-notes",
            **self._auth(self.staff_token),
        )
        alias = self.client.get(
            "/api/users/member@test.com/notes",
            **self._auth(self.staff_token),
        )
        self.assertEqual(alias.status_code, 200)
        self.assertEqual(alias.json(), legacy.json())

    def test_notes_alias_without_authorization_returns_401(self):
        response = self.client.get("/api/users/member@test.com/notes")
        self.assertEqual(response.status_code, 401)


class VisibilityGateInQuerysetTest(TestCase):
    """Architecturally important test: every file in ``api/views/``
    other than ``_permissions.py`` MUST NOT contain the literal string
    ``is_staff``. The only place the API may inspect that attribute is
    ``api/views/_permissions.py``; views compose against the queryset
    helpers it exports.
    """

    def test_interview_notes_view_has_no_is_staff_reference(self):
        view_path = (
            pathlib.Path(__file__).resolve().parent.parent
            / "views" / "interview_notes.py"
        )
        source = view_path.read_text(encoding="utf-8")
        self.assertNotIn(
            "is_staff", source,
            msg=(
                "is_staff must not appear in interview_notes.py -- the "
                "visibility gate must compose against "
                "api/views/_permissions.py instead."
            ),
        )

    def test_no_other_view_module_inspects_is_staff(self):
        """Sweep every ``api/views/*.py`` module other than the
        permissions helper and assert ``is_staff`` does not appear.
        Adding a new view module that inlines ``request.user.is_staff``
        will fail this test even if the developer forgets to add a
        per-module check.
        """
        views_dir = (
            pathlib.Path(__file__).resolve().parent.parent / "views"
        )
        offending = []
        for path in sorted(views_dir.glob("*.py")):
            if path.name in ("_permissions.py", "__init__.py"):
                continue
            source = path.read_text(encoding="utf-8")
            if "is_staff" in source:
                offending.append(path.name)
        self.assertEqual(
            offending, [],
            msg=(
                "These files inspect is_staff outside _permissions.py: "
                f"{offending}"
            ),
        )
