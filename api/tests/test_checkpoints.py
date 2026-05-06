"""Tests for Checkpoint create / patch / delete (issue #433).

The ``move`` endpoint is the hot path for #434's drag-drop UI and lives
in its own ``test_checkpoint_move.py`` module so the file stays focused.
"""

import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()


class CheckpointsApiTestBase(TestCase):
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
            email="m@test.com", password="pw",
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    def setUp(self):
        # Per-test week so the create-shift tests don't see siblings
        # from a previous run inside the same TestCase class.
        self.week = Week.objects.create(plan=self.plan, week_number=1)

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}


class CheckpointCreateTest(CheckpointsApiTestBase):
    def _post(self, payload):
        return self.client.post(
            f"/api/weeks/{self.week.id}/checkpoints",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth(),
        )

    def test_create_checkpoint_appends(self):
        Checkpoint.objects.create(
            week=self.week, description="a", position=0,
        )
        Checkpoint.objects.create(
            week=self.week, description="b", position=1,
        )
        response = self._post({"description": "c"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["position"], 2)

    def test_create_first_checkpoint_starts_at_position_zero(self):
        response = self._post({"description": "first"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["position"], 0)

    def test_create_at_position_zero_shifts_existing(self):
        a = Checkpoint.objects.create(
            week=self.week, description="a", position=0,
        )
        b = Checkpoint.objects.create(
            week=self.week, description="b", position=1,
        )
        response = self._post({"description": "c", "position": 0})
        self.assertEqual(response.status_code, 201)
        a.refresh_from_db()
        b.refresh_from_db()
        new_id = response.json()["id"]
        self.assertEqual(a.position, 1)
        self.assertEqual(b.position, 2)
        self.assertEqual(
            Checkpoint.objects.get(pk=new_id).position, 0,
        )

    def test_create_partial_failure_rolls_back(self):
        """If the sibling-shift bulk update raises, no row is created.

        Patches ``Checkpoint.save`` so it raises immediately after the
        sibling-shift has already run. Because the create path is wrapped
        in ``transaction.atomic``, the shift must roll back too -- we
        verify by asserting both ``count`` and the original row's
        position are unchanged.
        """
        existing = Checkpoint.objects.create(
            week=self.week, description="a", position=0,
        )
        before = Checkpoint.objects.filter(week=self.week).count()

        original_save = Checkpoint.save

        def failing_save(self, *args, **kwargs):
            # Only fail on the new row (no PK yet).
            if self.pk is None:
                raise RuntimeError("forced failure")
            return original_save(self, *args, **kwargs)

        with mock.patch.object(
            Checkpoint, "save", new=failing_save,
        ):
            with self.assertRaises(RuntimeError):
                self._post({"description": "c", "position": 0})

        self.assertEqual(
            Checkpoint.objects.filter(week=self.week).count(), before,
        )
        existing.refresh_from_db()
        self.assertEqual(existing.position, 0)


class CheckpointPatchTest(CheckpointsApiTestBase):
    def test_patch_done_at(self):
        cp = Checkpoint.objects.create(
            week=self.week, description="a", position=0,
        )
        ts = "2026-05-01T12:00:00+00:00"
        response = self.client.patch(
            f"/api/checkpoints/{cp.id}",
            data=json.dumps({"done_at": ts}),
            content_type="application/json",
            **self._auth(),
        )
        self.assertEqual(response.status_code, 200)
        cp.refresh_from_db()
        self.assertIsNotNone(cp.done_at)


class CheckpointDeleteTest(CheckpointsApiTestBase):
    def test_delete_decrements_subsequent(self):
        cps = [
            Checkpoint.objects.create(
                week=self.week, description=f"cp{i}", position=i,
            )
            for i in range(4)
        ]
        # Delete the row at position 1.
        response = self.client.delete(
            f"/api/checkpoints/{cps[1].id}", **self._auth(),
        )
        self.assertEqual(response.status_code, 204)
        # Subsequent siblings shift down so positions stay contiguous.
        cps[0].refresh_from_db()
        cps[2].refresh_from_db()
        cps[3].refresh_from_db()
        self.assertEqual(cps[0].position, 0)
        self.assertEqual(cps[2].position, 1)
        self.assertEqual(cps[3].position, 2)
