"""Tests for ``POST /api/checkpoints/<id>/move`` (issue #433).

The move endpoint is the hot path for #434's drag-drop UI, so we test:

- Same-week reorder.
- Cross-week move.
- Idempotent no-op (no UPDATE writes when source==destination AND
  position unchanged).
- Returns the canonical reconciliation envelope.
- Atomicity under mid-transaction failure.
- Rejects cross-plan moves with 422.
"""

import datetime
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase

from accounts.models import Token
from plans.models import Checkpoint, Plan, Sprint, Week

User = get_user_model()


class CheckpointMoveTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="m@test.com", password="pw",
        )
        cls.other = User.objects.create_user(
            email="o@test.com", password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="s")

        cls.sprint = Sprint.objects.create(
            name="s", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )
        cls.other_plan = Plan.objects.create(
            member=cls.other, sprint=cls.sprint,
        )

    def setUp(self):
        # Per-test week+checkpoint setup so the reorder logic always
        # sees a clean slate.
        self.week_a = Week.objects.create(plan=self.plan, week_number=1)
        self.week_b = Week.objects.create(plan=self.plan, week_number=2)
        self.cps_a = [
            Checkpoint.objects.create(
                week=self.week_a, description=f"a{i}", position=i,
            )
            for i in range(4)
        ]
        self.cps_b = [
            Checkpoint.objects.create(
                week=self.week_b, description=f"b{i}", position=i,
            )
            for i in range(2)
        ]

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def _move(self, cp_id, payload, *, token=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload)
        return self.client.post(
            f"/api/checkpoints/{cp_id}/move",
            data=body,
            content_type="application/json",
            **self._auth(token),
        )


class CheckpointMoveSameWeekTest(CheckpointMoveTestBase):
    def test_move_within_same_week_reorders(self):
        # Move a0 (position 0) -> position 2 in same week.
        response = self._move(
            self.cps_a[0].id,
            {"week_id": self.week_a.id, "position": 2},
        )
        self.assertEqual(response.status_code, 200)
        # New order: a1@0, a2@1, a0@2, a3@3
        self.cps_a[0].refresh_from_db()
        self.cps_a[1].refresh_from_db()
        self.cps_a[2].refresh_from_db()
        self.cps_a[3].refresh_from_db()
        self.assertEqual(self.cps_a[1].position, 0)
        self.assertEqual(self.cps_a[2].position, 1)
        self.assertEqual(self.cps_a[0].position, 2)
        self.assertEqual(self.cps_a[3].position, 3)


class CheckpointMoveAcrossWeeksTest(CheckpointMoveTestBase):
    def test_move_across_weeks(self):
        # Move a1 (position 1, week_a) -> position 0, week_b.
        # Source becomes a0, a2, a3 at 0,1,2.
        # Destination becomes a1, b0, b1 at 0,1,2.
        response = self._move(
            self.cps_a[1].id,
            {"week_id": self.week_b.id, "position": 0},
        )
        self.assertEqual(response.status_code, 200)
        for cp in self.cps_a + self.cps_b:
            cp.refresh_from_db()
        moved = self.cps_a[1]
        self.assertEqual(moved.week_id, self.week_b.id)
        self.assertEqual(moved.position, 0)
        # Source week is now contiguous.
        self.assertEqual(self.cps_a[0].position, 0)
        self.assertEqual(self.cps_a[2].position, 1)
        self.assertEqual(self.cps_a[3].position, 2)
        # Destination siblings shifted up.
        self.assertEqual(self.cps_b[0].position, 1)
        self.assertEqual(self.cps_b[1].position, 2)


class CheckpointMoveNoOpTest(CheckpointMoveTestBase):
    def test_no_op_returns_200_no_writes(self):
        """An idempotent no-op move must NOT issue any UPDATE statement
        against ``plans_checkpoint`` (no sibling shift, no own-row save).

        We assert the strong guarantee directly by capturing every SQL
        statement and counting how many UPDATEs target the checkpoint
        table. The exact ``SELECT`` budget can drift (token auth, plan
        visibility probe, week select, checkpoint_ids select) -- the
        load-bearing claim is "zero writes against checkpoint rows".
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        cp = self.cps_a[1]
        with CaptureQueriesContext(connection) as ctx:
            response = self._move(
                cp.id,
                {"week_id": self.week_a.id, "position": cp.position},
            )

        self.assertEqual(response.status_code, 200)
        checkpoint_updates = [
            q for q in ctx.captured_queries
            if q["sql"].startswith("UPDATE")
            and "plans_checkpoint" in q["sql"]
        ]
        self.assertEqual(
            checkpoint_updates, [],
            msg=f"no-op move issued checkpoint writes: {checkpoint_updates}",
        )
        # And nothing in the DB changed.
        for c in self.cps_a:
            old = c.position
            c.refresh_from_db()
            self.assertEqual(c.position, old)


class CheckpointMoveEnvelopeTest(CheckpointMoveTestBase):
    def test_returns_canonical_envelope(self):
        response = self._move(
            self.cps_a[1].id,
            {"week_id": self.week_b.id, "position": 0},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("checkpoint", body)
        self.assertIn("source_week", body)
        self.assertIn("destination_week", body)
        self.assertEqual(body["source_week"]["id"], self.week_a.id)
        self.assertEqual(body["destination_week"]["id"], self.week_b.id)
        # Source has a0, a2, a3 (in some position-sorted order).
        self.assertEqual(
            body["source_week"]["checkpoint_ids"],
            [self.cps_a[0].id, self.cps_a[2].id, self.cps_a[3].id],
        )
        # Destination starts with the moved checkpoint.
        self.assertEqual(
            body["destination_week"]["checkpoint_ids"][0],
            self.cps_a[1].id,
        )


class CheckpointMoveValidationTest(CheckpointMoveTestBase):
    def test_move_to_other_plan_returns_422(self):
        other_week = Week.objects.create(
            plan=self.other_plan, week_number=1,
        )
        response = self._move(
            self.cps_a[0].id,
            {"week_id": other_week.id, "position": 0},
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("week_id", body["details"])
        # Source unchanged.
        for i, cp in enumerate(self.cps_a):
            cp.refresh_from_db()
            self.assertEqual(cp.position, i)

    def test_move_negative_position_returns_422(self):
        response = self._move(
            self.cps_a[0].id,
            {"week_id": self.week_a.id, "position": -1},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_move_unknown_week_returns_404(self):
        response = self._move(
            self.cps_a[0].id, {"week_id": 99999, "position": 0},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_week")

    def test_move_missing_week_id_returns_400(self):
        response = self._move(self.cps_a[0].id, {})
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "missing_field")
        self.assertEqual(body["details"]["field"], "week_id")

    def test_move_invalid_json_returns_400(self):
        response = self._move(
            self.cps_a[0].id, None, raw_body="not-json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid JSON"})

    def test_move_unknown_checkpoint_returns_404(self):
        response = self._move(
            999999, {"week_id": self.week_a.id, "position": 0},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "unknown_checkpoint")


class CheckpointMoveAtomicityTest(TransactionTestCase):
    """Atomicity test that needs a real (non-rolled-back) DB so the
    failure-mid-transaction scenario writes meaningful state.

    We patch the destination-week shift call so it raises AFTER the
    source-week shift has already run. The whole operation MUST roll
    back so the source week's positions are restored to their original
    contiguous sequence.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        self.token = Token.objects.create(user=self.staff, name="s")
        self.sprint = Sprint.objects.create(
            name="s", slug="s",
            start_date=datetime.date(2026, 5, 1),
        )
        self.member = User.objects.create_user(
            email="m@test.com", password="pw",
        )
        self.plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )
        self.week_a = Week.objects.create(plan=self.plan, week_number=1)
        self.week_b = Week.objects.create(plan=self.plan, week_number=2)
        self.cps_a = [
            Checkpoint.objects.create(
                week=self.week_a, description=f"a{i}", position=i,
            )
            for i in range(3)
        ]
        self.cps_b = [
            Checkpoint.objects.create(
                week=self.week_b, description=f"b{i}", position=i,
            )
            for i in range(2)
        ]

    def test_atomicity_under_failure(self):
        """Patch the second sibling-shift to raise, assert no partial write.

        The move endpoint runs source-shift first, then destination-shift,
        then the moved row's save. We patch the moved-row ``save`` to
        raise: that runs after both sibling shifts but before commit.
        Because the work is wrapped in ``transaction.atomic``, both
        shifts must roll back.
        """
        original_save = Checkpoint.save

        def failing_save(self, *args, **kwargs):
            # Only raise when called inside the move (the moved
            # checkpoint's ``update_fields`` includes ``week_id``).
            if "update_fields" in kwargs and "week_id" in (
                kwargs["update_fields"] or []
            ):
                raise RuntimeError("forced failure")
            return original_save(self, *args, **kwargs)

        snapshot_a = [(c.pk, c.position) for c in self.cps_a]
        snapshot_b = [(c.pk, c.position) for c in self.cps_b]

        # Django's test client re-raises view exceptions by default;
        # disable that here so we can observe the 500 response status.
        self.client.raise_request_exception = False
        with mock.patch.object(
            Checkpoint, "save", new=failing_save,
        ):
            response = self.client.post(
                f"/api/checkpoints/{self.cps_a[1].id}/move",
                data=json.dumps(
                    {"week_id": self.week_b.id, "position": 0},
                ),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Token {self.token.key}",
            )
            # Internal failure surfaces as a 500 from Django; the
            # important assertion is the side-effect rollback below.
            self.assertEqual(response.status_code, 500)

        for pk, original_pos in snapshot_a:
            self.assertEqual(
                Checkpoint.objects.get(pk=pk).position, original_pos,
            )
        for pk, original_pos in snapshot_b:
            self.assertEqual(
                Checkpoint.objects.get(pk=pk).position, original_pos,
            )
        # The moved row's week_id is also unchanged.
        moved = Checkpoint.objects.get(pk=self.cps_a[1].pk)
        self.assertEqual(moved.week_id, self.week_a.id)
