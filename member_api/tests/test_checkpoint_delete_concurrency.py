"""Concurrent retry coverage for idempotent member checkpoint deletion."""

import datetime
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import OperationalError, close_old_connections
from django.test import Client, TransactionTestCase, tag

from accounts.models import MemberAPIKey, User
from plans.models import (
    Checkpoint,
    CheckpointDeletionReceipt,
    Plan,
    Sprint,
    SprintEnrollment,
    Week,
)


@tag("core")
class CheckpointDeleteConcurrencyTest(TransactionTestCase):
    """Overlapping requests share one receipt and the same success response."""

    def setUp(self):
        super().setUp()
        member = User.objects.create_user(email="delete-race@test.com")
        sprint = Sprint.objects.create(
            name="Delete race",
            slug="delete-race",
            start_date=datetime.date(2026, 9, 1),
            status="active",
        )
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        self.plan = Plan.objects.create(member=member, sprint=sprint)
        week = Week.objects.create(plan=self.plan, week_number=1)
        self.checkpoint = Checkpoint.objects.create(
            week=week,
            description="delete once",
        )
        self.plaintexts = [
            MemberAPIKey.create_for_user(
                user=member,
                name=f"delete race {index}",
                scopes=["plans:read", "plans:write"],
            )[1]
            for index in range(2)
        ]

    def test_overlapping_delete_retries_both_succeed(self):
        barrier = Barrier(2)
        url = (
            f"/member-api/v1/plans/{self.plan.id}"
            f"/checkpoints/{self.checkpoint.id}"
        )

        def delete(plaintext):
            barrier.wait(timeout=10)
            for retry in range(12):
                close_old_connections()
                try:
                    return Client().delete(
                        url,
                        HTTP_AUTHORIZATION=f"Token {plaintext}",
                    )
                except OperationalError:
                    if retry == 11:
                        raise
                    # Shared-cache SQLite raises immediately for table locks;
                    # PostgreSQL waits on the plan row in production.
                    time.sleep(0.05 * min(retry + 1, 4))
                finally:
                    close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(delete, plaintext)
                for plaintext in self.plaintexts
            ]
            responses = [future.result(timeout=20) for future in futures]

        expected = {"deleted": True, "id": self.checkpoint.id}
        self.assertEqual([response.json() for response in responses], [expected, expected])
        self.assertFalse(Checkpoint.objects.filter(pk=self.checkpoint.pk).exists())
        self.assertEqual(
            CheckpointDeletionReceipt.objects.filter(
                plan=self.plan,
                checkpoint_id=self.checkpoint.id,
            ).count(),
            1,
        )
