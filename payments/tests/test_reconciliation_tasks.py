"""Tests for the scheduled reconciliation task + schedule wiring (#1308)."""

from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from django_q.models import Schedule

from payments.models import (
    SubscriptionReconciliationFinding as Finding,
)
from payments.models import (
    SubscriptionReconciliationRun as Run,
)
from payments.services import subscription_reconciliation as recon
from payments.tasks import subscription_reconciliation as tasks


class ScheduledTaskTest(TestCase):
    def test_concurrent_running_run_exits_without_starting(self):
        Run.objects.create(status=Run.STATUS_RUNNING, mode=Run.MODE_DIAGNOSTIC)
        with patch.object(recon, "run_reconciliation") as rr:
            result = tasks.run_scheduled_reconciliation()
        self.assertIsNone(result)
        rr.assert_not_called()

    def test_stale_running_run_is_recovered_then_new_run_starts(self):
        stale = Run.objects.create(
            status=Run.STATUS_RUNNING, mode=Run.MODE_DIAGNOSTIC,
            started_at=timezone.now() - timedelta(hours=5),
        )
        completed = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
        )
        with patch.object(recon, "run_reconciliation", return_value=completed) as rr:
            tasks.run_scheduled_reconciliation()
        stale.refresh_from_db()
        self.assertEqual(stale.status, Run.STATUS_FAILED)
        rr.assert_called_once()

    def test_actionable_alert_skipped_when_findings_unchanged(self):
        prev = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            actionable_count=1,
            started_at=timezone.now() - timedelta(hours=1),
        )
        curr = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            actionable_count=1, started_at=timezone.now(),
        )
        for run in (prev, curr):
            Finding.objects.create(
                run=run, email="x@t.com",
                classification=recon.CLASSIFICATION_ENDED,
                stripe_status="canceled", outcome=Finding.OUTCOME_WOULD_CHANGE,
            )
        with patch.object(tasks, "mail_admins") as mail:
            tasks._maybe_send_actionable_alert(curr)
        mail.assert_not_called()

    def test_actionable_alert_sent_for_new_finding(self):
        Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            started_at=timezone.now() - timedelta(hours=1),
        )
        curr = Run.objects.create(
            status=Run.STATUS_COMPLETED, mode=Run.MODE_DIAGNOSTIC,
            actionable_count=1, started_at=timezone.now(),
        )
        Finding.objects.create(
            run=curr, email="new@t.com",
            classification=recon.CLASSIFICATION_ENDED,
            stripe_status="canceled", outcome=Finding.OUTCOME_WOULD_CHANGE,
        )
        with patch.object(tasks, "mail_admins") as mail:
            tasks._maybe_send_actionable_alert(curr)
        mail.assert_called_once()

    def test_failure_alert_after_three_consecutive_failures_then_deduped(self):
        for _ in range(3):
            Run.objects.create(
                status=Run.STATUS_FAILED, mode=Run.MODE_DIAGNOSTIC,
                source=Run.SOURCE_SCHEDULED, error_message="boom",
            )
        with patch.object(tasks, "mail_admins") as mail:
            tasks._maybe_send_failure_alert()
            tasks._maybe_send_failure_alert()
        self.assertEqual(mail.call_count, 1)


class ScheduleRegistrationTest(TestCase):
    def test_daily_schedule_is_registered_once_at_0430(self):
        call_command("setup_schedules")
        call_command("setup_schedules")  # idempotent
        rows = Schedule.objects.filter(
            name="stripe-subscription-reconciliation-daily",
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().cron, "30 4 * * *")
