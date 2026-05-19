"""Tests for schedule registration during container entrypoint boot.

Issue #708: the container entrypoint (``scripts/entrypoint_init.py``) must
call ``setup_schedules`` on every boot for BOTH the web container
(``RUN_MIGRATIONS=true``) and the worker container
(``RUN_MIGRATIONS!=true``), so the django-q ``Schedule`` rows that drive
``complete-finished-events`` and friends always exist in prod.

We test the dedicated helper ``_register_schedules`` rather than ``main``
because ``main`` ends by handing off to gunicorn / qcluster (both
blocking), and what we care about is the behavior of the registration
step itself.
"""

import logging
import os
from unittest import mock

from django.test import TestCase
from django_q.models import Schedule

from scripts.entrypoint_init import _register_schedules


class EntrypointRegistersSchedulesTest(TestCase):
    """``_register_schedules`` populates the django-q ``Schedule`` table."""

    def test_registers_complete_finished_events_schedule(self):
        """The cron behind issue #708 is created with the correct func and cron."""
        _register_schedules()

        schedule = Schedule.objects.get(name='complete-finished-events')
        self.assertEqual(
            schedule.func,
            'events.tasks.complete_finished_events.complete_finished_events',
        )
        self.assertEqual(schedule.cron, '*/5 * * * *')
        self.assertEqual(schedule.schedule_type, Schedule.CRON)

    def test_registers_all_default_schedules(self):
        """Every schedule registered by setup_schedules lands in the DB."""
        _register_schedules()

        names = set(Schedule.objects.values_list('name', flat=True))
        expected = {
            'health-check',
            'cleanup-webhook-logs',
            'event-reminders',
            'complete-finished-events',
            'expire-tier-overrides',
            'slack-membership-refresh',
            'import-slack-daily',
            'import-stripe-daily',
            'remind-unverified-users',
            'purge-unverified-users',
        }
        self.assertEqual(names, expected)

    def test_idempotent_across_two_boots(self):
        """Running entrypoint twice produces exactly one row per schedule."""
        _register_schedules()
        _register_schedules()

        self.assertEqual(
            Schedule.objects.filter(name='complete-finished-events').count(),
            1,
        )
        # Spot-check a couple of others to make sure no command-wide
        # duplication regression sneaks in.
        self.assertEqual(
            Schedule.objects.filter(name='health-check').count(), 1,
        )
        self.assertEqual(
            Schedule.objects.filter(name='event-reminders').count(), 1,
        )


class EntrypointSchedulesEnvDispatchTest(TestCase):
    """The schedule must be registered for both web and worker variants.

    Both containers run the same entrypoint and dispatch on
    ``RUN_MIGRATIONS``. The schedule registration step must NOT live
    behind that flag — both variants need the cron table populated
    because either container's boot may land first in prod (see issue
    #336 deadlock note).
    """

    def test_schedule_registered_for_web_variant(self):
        """``RUN_MIGRATIONS=true`` (web): schedule exists after registration."""
        with mock.patch.dict(os.environ, {'RUN_MIGRATIONS': 'true'}):
            _register_schedules()

        self.assertTrue(
            Schedule.objects.filter(name='complete-finished-events').exists(),
        )

    def test_schedule_registered_for_worker_variant(self):
        """``RUN_MIGRATIONS!=true`` (worker): schedule still exists."""
        # Explicitly set to "false" to mirror what the worker task def
        # produces (the env var is simply absent there, but covering the
        # set-to-non-true case proves the registration is not gated on
        # the flag).
        with mock.patch.dict(os.environ, {'RUN_MIGRATIONS': 'false'}):
            _register_schedules()

        self.assertTrue(
            Schedule.objects.filter(name='complete-finished-events').exists(),
        )

    def test_schedule_registered_when_env_var_unset(self):
        """RUN_MIGRATIONS unset (worker default in prod): schedule exists."""
        env_without_flag = {
            k: v for k, v in os.environ.items() if k != 'RUN_MIGRATIONS'
        }
        with mock.patch.dict(os.environ, env_without_flag, clear=True):
            _register_schedules()

        self.assertTrue(
            Schedule.objects.filter(name='complete-finished-events').exists(),
        )


class EntrypointScheduleFailureIsSwallowedTest(TestCase):
    """A ``setup_schedules`` exception must NOT crash boot.

    Web cold-start cannot be allowed to crash on a schedule-registration
    regression; the worst-case fallout of swallowing the error is that
    one cron does not fire until the next deploy, which is still far
    better than an ECS crash loop that takes down the site.
    """

    def test_exception_is_logged_and_swallowed(self):
        with mock.patch(
            'django.core.management.call_command',
            side_effect=RuntimeError('simulated bad schedule entry'),
        ), self.assertLogs('scripts.entrypoint_init', level='ERROR') as cm:
            # Must not raise.
            _register_schedules()

        self.assertTrue(
            any('setup_schedules failed' in msg for msg in cm.output),
            f'expected failure log line, got {cm.output}',
        )

    def test_exception_does_not_propagate(self):
        with mock.patch(
            'django.core.management.call_command',
            side_effect=RuntimeError('boom'),
        ):
            # If this raised, the container would crash-loop. Asserting
            # no exception escapes is the whole point.
            try:
                _register_schedules()
            except Exception as exc:  # pragma: no cover - failure path
                self.fail(
                    f'_register_schedules must swallow exceptions, raised {exc!r}',
                )

    def test_logger_uses_module_name(self):
        """Failures log under the entrypoint module so ops can grep for them."""
        # Sanity check: logger name matches the module so log filtering
        # in CloudWatch / structured logs can find these events.
        logger = logging.getLogger('scripts.entrypoint_init')
        self.assertEqual(logger.name, 'scripts.entrypoint_init')
