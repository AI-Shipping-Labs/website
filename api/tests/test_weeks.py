"""Tests for the Week endpoints (issue #433)."""

import datetime
import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Checkpoint, Plan, Sprint, Week, WeekNote

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
        cls.other = User.objects.create_user(
            email="other@test.com", password="pw",
        )
        cls.member_token = Token(
            key="member-week-note-token",
            user=cls.member,
            name="m",
        )
        cls.other_token = Token(
            key="other-week-note-token",
            user=cls.other,
            name="o",
        )
        Token.objects.bulk_create([cls.member_token, cls.other_token])
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def _auth(self, token=None):
        token = token or self.token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}


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
        self.assertIsNone(response.json()["note"])

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
        self.assertIsNone(response.json()["note"])


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


class WeekNoteApiTest(WeeksApiTestBase):
    def setUp(self):
        self.week = Week.objects.create(plan=self.plan, week_number=1)

    def _put(self, payload, *, token=None):
        return self.client.put(
            f"/api/weeks/{self.week.id}/note",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def _patch(self, payload, *, token=None):
        return self.client.patch(
            f"/api/weeks/{self.week.id}/note",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(token),
        )

    def test_get_returns_null_when_no_note_exists(self):
        response = self.client.get(
            f"/api/weeks/{self.week.id}/note",
            **self._auth(self.member_token),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"note": None})

    def test_staff_put_then_put_updates_same_member_authored_note(self):
        response = self._put({"body": "Week 1 shipped"})
        self.assertEqual(response.status_code, 201)
        first = response.json()["note"]
        self.assertEqual(first["body"], "Week 1 shipped")
        self.assertEqual(first["author_email"], "member@test.com")

        response = self._put({"body": "Week 1 shipped and demo recorded"})
        self.assertEqual(response.status_code, 200)
        second = response.json()["note"]
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["body"], "Week 1 shipped and demo recorded")
        self.assertEqual(WeekNote.objects.filter(week=self.week).count(), 1)
        self.assertEqual(self.week.notes.get().author_id, self.member.id)

    def test_member_patch_can_manage_own_week_note(self):
        response = self._patch(
            {"body": "Member-authored note"},
            token=self.member_token,
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["note"]["author_email"], "member@test.com")

    def test_other_member_cannot_read_or_write_note(self):
        WeekNote.objects.create(
            week=self.week,
            body="private weekly note",
            author=self.member,
        )
        response = self.client.get(
            f"/api/weeks/{self.week.id}/note",
            **self._auth(self.other_token),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_week")

        response = self._put(
            {"body": "overwrite"},
            token=self.other_token,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.week.notes.get().body, "private weekly note")

    def test_blank_body_rejected_without_erasing_existing_note(self):
        note = WeekNote.objects.create(
            week=self.week,
            body="keep me",
            author=self.member,
        )
        response = self._patch({"body": "   "})
        self.assertEqual(response.status_code, 400)
        note.refresh_from_db()
        self.assertEqual(note.body, "keep me")

    def test_delete_clears_note_and_get_returns_null(self):
        WeekNote.objects.create(
            week=self.week,
            body="delete me",
            author=self.member,
        )
        response = self.client.delete(
            f"/api/weeks/{self.week.id}/note",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(WeekNote.objects.filter(week=self.week).exists())

        response = self.client.get(
            f"/api/weeks/{self.week.id}/note",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"note": None})

    def test_week_list_and_plan_detail_serialize_singular_note(self):
        note = WeekNote.objects.create(
            week=self.week,
            body="serialized singleton",
            author=self.member,
        )
        response = self.client.get(
            f"/api/plans/{self.plan.id}/weeks",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        week_body = response.json()["weeks"][0]
        self.assertEqual(week_body["note"]["id"], note.id)
        self.assertNotIn("notes", week_body)

        response = self.client.get(
            f"/api/plans/{self.plan.id}",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        week_body = response.json()["weeks"][0]
        self.assertEqual(week_body["note"]["body"], "serialized singleton")
        self.assertNotIn("notes", week_body)
