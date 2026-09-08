"""Fail-closed schedule-set reconciliation tests for issue #1540."""

from unittest import mock

from django.core.cache import caches
from django.test import TestCase
from django_q.models import Schedule

from jobs.schedule_reconciliation import (
    SCHEDULE_RECONCILIATION_CACHE_KEY,
    ScheduleDefinition,
    apply_schedule_definitions,
    build_schedule_definitions,
)
from jobs.tasks.helpers import schedule as write_schedule
from jobs.tasks.schedule_reconciliation import reconcile_schedules


class ScheduleSetValidationTest(TestCase):
    def setUp(self):
        caches["django_q"].clear()

    def test_invalid_late_definition_writes_no_schedule_rows(self):
        definitions = (
            ScheduleDefinition(
                "valid",
                "jobs.tasks.healthcheck.health_check",
                "0 * * * *",
            ),
            ScheduleDefinition("invalid", "jobs.tasks.healthcheck", "0 * * * *"),
        )

        with mock.patch("jobs.schedule_reconciliation.schedule") as writer:
            with self.assertRaisesMessage(ValueError, "must resolve to a function"):
                apply_schedule_definitions(definitions)

        writer.assert_not_called()
        self.assertFalse(Schedule.objects.exists())

    def test_degraded_payload_redacts_secret_shaped_error_values(self):
        definition = ScheduleDefinition(
            "invalid", "jobs.tasks.healthcheck", "0 * * * *",
        )
        with mock.patch(
            "jobs.schedule_reconciliation.validate_schedule_definitions",
            side_effect=RuntimeError("token=super-secret Bearer also-secret"),
        ):
            with self.assertRaises(RuntimeError):
                apply_schedule_definitions((definition,))

        state = caches["django_q"].get(SCHEDULE_RECONCILIATION_CACHE_KEY)
        self.assertNotIn("super-secret", state["error"])
        self.assertNotIn("also-secret", state["error"])
        self.assertIn("token=[redacted]", state["error"])

    def test_duplicate_names_are_rejected_before_write(self):
        definitions = (
            ScheduleDefinition(
                "duplicate",
                "jobs.tasks.healthcheck.health_check",
                "0 * * * *",
            ),
            ScheduleDefinition(
                "duplicate",
                "jobs.tasks.healthcheck.health_check",
                "5 * * * *",
            ),
        )

        with mock.patch("jobs.schedule_reconciliation.schedule") as writer:
            with self.assertRaisesMessage(ValueError, "duplicate schedule names"):
                apply_schedule_definitions(definitions)

        writer.assert_not_called()

    def test_missing_cron_is_rejected_before_write(self):
        definition = ScheduleDefinition(
            "missing-cron",
            "jobs.tasks.healthcheck.health_check",
            "",
        )
        with self.assertRaisesMessage(ValueError, "cron is required"):
            apply_schedule_definitions((definition,))
        self.assertFalse(Schedule.objects.exists())


class AtomicScheduleApplyTest(TestCase):
    def setUp(self):
        caches["django_q"].clear()

    def test_second_write_failure_rolls_back_first_update(self):
        Schedule.objects.create(
            name="first",
            func="jobs.tasks.healthcheck.health_check",
            schedule_type=Schedule.CRON,
            cron="0 0 * * *",
        )
        definitions = (
            ScheduleDefinition(
                "first",
                "jobs.tasks.healthcheck.health_check",
                "0 * * * *",
            ),
            ScheduleDefinition(
                "second",
                "jobs.tasks.healthcheck.health_check",
                "5 * * * *",
            ),
        )
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second write failed")
            return write_schedule(*args, **kwargs)

        with mock.patch(
            "jobs.schedule_reconciliation.schedule",
            side_effect=fail_second,
        ):
            with self.assertRaisesMessage(RuntimeError, "second write failed"):
                apply_schedule_definitions(definitions)

        self.assertEqual(Schedule.objects.get(name="first").cron, "0 0 * * *")
        self.assertFalse(Schedule.objects.filter(name="second").exists())
        state = caches["django_q"].get(SCHEDULE_RECONCILIATION_CACHE_KEY)
        self.assertEqual(state["status"], "degraded")
        self.assertIn("RuntimeError: second write failed", state["error"])

    def test_success_clears_degraded_and_preserves_unmanaged_rows(self):
        caches["django_q"].set(
            SCHEDULE_RECONCILIATION_CACHE_KEY,
            {"status": "degraded", "last_success_at": None},
            timeout=None,
        )
        Schedule.objects.create(
            name="operator-owned",
            func="operator.task",
            schedule_type=Schedule.CRON,
            cron="0 0 * * *",
        )
        definitions = (
            ScheduleDefinition(
                "health-check",
                "jobs.tasks.healthcheck.health_check",
                "*/15 * * * *",
            ),
        )

        apply_schedule_definitions(definitions)

        state = caches["django_q"].get(SCHEDULE_RECONCILIATION_CACHE_KEY)
        self.assertEqual(state["status"], "ok")
        self.assertIsNotNone(state["last_success_at"])
        self.assertIsNone(state["error"])
        self.assertEqual(state["missing_names"], [])
        self.assertTrue(Schedule.objects.filter(name="operator-owned").exists())


class PeriodicScheduleReconciliationTest(TestCase):
    def setUp(self):
        caches["django_q"].clear()

    def test_declarative_set_registers_periodic_reconciler(self):
        definitions = build_schedule_definitions(background_enabled=False)
        apply_schedule_definitions(definitions)

        row = Schedule.objects.get(name="reconcile-schedules")
        self.assertEqual(
            row.func,
            "jobs.tasks.schedule_reconciliation.reconcile_schedules",
        )
        self.assertEqual(row.cron, "*/15 * * * *")

    def test_periodic_task_reapplies_missing_schedule(self):
        # The task imports release gating inside the function, so patch the
        # source owner rather than the lazily-created module attribute.
        with mock.patch(
            "website.release_phase.background_work_enabled",
            return_value=False,
        ):
            reconcile_schedules()
            Schedule.objects.filter(name="health-check").delete()
            reconcile_schedules()

        self.assertTrue(Schedule.objects.filter(name="health-check").exists())
        self.assertEqual(
            Schedule.objects.filter(name="reconcile-schedules").count(),
            1,
        )
