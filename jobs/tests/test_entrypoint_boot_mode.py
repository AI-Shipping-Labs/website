"""Tests for the ``BOOT_MODE`` dispatch in ``scripts/entrypoint_init.py``.

Issue #1141 Phase 2A: the entrypoint dispatches on the ``BOOT_MODE`` env var
to untangle web-vs-worker role dispatch from migrate-on-boot:

* ``web`` -> ``django.setup`` -> schedules -> gunicorn (SKIPS migrate).
* ``worker`` -> ``django.setup`` -> schedules -> qcluster (SKIPS migrate).
* absent -> legacy ``RUN_MIGRATIONS`` behavior (source Service web migrator
  runs ``migrate`` when ``RUN_MIGRATIONS=true``).

Phase 2C: gunicorn ``--workers`` is read from ``os.environ['GUNICORN_WORKERS']``
Parsing and the gunicorn argv are covered in ``test_entrypoint_gunicorn.py``.

We drive ``main()`` with the blocking handoffs (``_start_gunicorn`` /
``_start_qcluster``), ``django.setup``, ``_register_schedules`` and
``persist_boot_timing`` patched so no real boot or DB write happens, and assert
exactly which phases run in each mode. ``call_command`` (used by the migrate
helper) is patched so we can assert what was and was NOT invoked.
"""

import os
from unittest import mock

from django.test import SimpleTestCase

import scripts.entrypoint_init as entry


def _command_names(mock_call_command):
    """First positional arg of every ``call_command`` invocation."""
    return [
        call.args[0]
        for call in mock_call_command.call_args_list
        if call.args
    ]


class BootModeDispatchTestBase(SimpleTestCase):
    """Patches every blocking / side-effecting boundary of ``main()``."""

    def setUp(self):
        for p in (
            mock.patch.object(entry.django, "setup"),
            mock.patch.object(entry, "_start_gunicorn"),
            mock.patch.object(entry, "_start_qcluster"),
            mock.patch.object(entry, "_register_schedules"),
            mock.patch.object(entry, "_suppress_r1_incompatible_schedules"),
            mock.patch.object(entry, "persist_boot_timing"),
            mock.patch.object(entry, "_initialize_runtime_observability"),
            mock.patch("django.core.management.call_command"),
        ):
            p.start()
            self.addCleanup(p.stop)

        # After patching, the module attributes ARE the mocks.
        self.start_gunicorn = entry._start_gunicorn
        self.start_qcluster = entry._start_qcluster
        self.register_schedules = entry._register_schedules
        self.suppress_schedules = entry._suppress_r1_incompatible_schedules
        self.persist = entry.persist_boot_timing
        self.initialize_observability = entry._initialize_runtime_observability
        # The migrate helper imports call_command lazily from this path.
        import django.core.management as mgmt

        self.call_command = mgmt.call_command

    def _run_main_with_env(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            entry.main()


class WebModeTest(BootModeDispatchTestBase):
    def test_web_registers_schedules_and_serves_without_migrate_or_check(self):
        self._run_main_with_env({"BOOT_MODE": "web"})

        names = _command_names(self.call_command)
        self.assertNotIn("migrate", names)
        self.assertNotIn("check", names)

        self.register_schedules.assert_called_once()
        self.assertEqual(self.initialize_observability.call_count, 1)
        self.start_gunicorn.assert_called_once()
        self.start_qcluster.assert_not_called()
        # Serving boot persists its timing payload under the web role.
        self.persist.assert_called_once()
        self.assertEqual(self.persist.call_args.args[0], "web")

    def test_web_uses_configured_worker_count(self):
        self._run_main_with_env({"BOOT_MODE": "web", "GUNICORN_WORKERS": "2"})
        self.start_gunicorn.assert_called_once_with(2)

    def test_web_defaults_to_three_workers_when_unset(self):
        self._run_main_with_env({"BOOT_MODE": "web"})
        self.start_gunicorn.assert_called_once_with(3)

    def test_runtime_observability_runs_after_setup_and_before_serve(self):
        order = []
        entry.django.setup.side_effect = lambda: order.append("django_setup")
        self.initialize_observability.side_effect = (
            lambda: order.append("observability")
        )
        self.start_gunicorn.side_effect = lambda *_: order.append("gunicorn")

        self._run_main_with_env({"BOOT_MODE": "web"})

        self.assertLess(order.index("django_setup"), order.index("observability"))
        self.assertLess(order.index("observability"), order.index("gunicorn"))

    def test_smoke_check_failure_stops_before_django_setup(self):
        with self.assertRaises(RuntimeError):
            self._run_main_with_env({
                "BOOT_MODE": "web",
                "DEBUG": "false",
            })

        entry.django.setup.assert_not_called()
        self.call_command.assert_not_called()
        self.start_gunicorn.assert_not_called()
        self.start_qcluster.assert_not_called()


class WorkerModeTest(BootModeDispatchTestBase):
    def test_worker_registers_schedules_and_starts_qcluster_only(self):
        self._run_main_with_env({"BOOT_MODE": "worker"})

        names = _command_names(self.call_command)
        self.assertNotIn("migrate", names)
        self.assertNotIn("check", names)

        self.register_schedules.assert_called_once()
        self.assertEqual(self.initialize_observability.call_count, 1)
        self.start_qcluster.assert_called_once()
        self.start_gunicorn.assert_not_called()
        self.persist.assert_called_once()
        self.assertEqual(self.persist.call_args.args[0], "worker")


class RuntimeObservabilityHookTest(SimpleTestCase):

    def test_hook_uses_db_backed_runtime_config(self):
        with mock.patch(
            "integrations.services.observability.init_logfire",
        ) as init_logfire:
            entry._initialize_runtime_observability()

        init_logfire.assert_called_once_with(use_runtime_config=True)


class NoInfraServingBootTest(BootModeDispatchTestBase):
    """BOOT_MODE absent keeps migrations on the source web migrator."""

    def test_web_migrates_schedules_and_serves_without_check_by_default(self):
        # RUN_MIGRATIONS=true, BOOT_MODE absent -> source Service web path.
        self._run_main_with_env({"RUN_MIGRATIONS": "true"})

        names = _command_names(self.call_command)
        self.assertIn("migrate", names)
        self.assertNotIn("check", names)
        self.register_schedules.assert_called_once()
        self.assertEqual(self.initialize_observability.call_count, 1)
        self.start_gunicorn.assert_called_once()
        self.start_qcluster.assert_not_called()
        self.assertEqual(self.persist.call_args.args[0], "web")

    def test_web_migrate_failure_propagates_and_skips_serve(self):
        def boom(*args, **kwargs):
            if args and args[0] == "migrate":
                raise RuntimeError("migration failed")

        self.call_command.side_effect = boom

        with self.assertRaises(RuntimeError):
            self._run_main_with_env({"RUN_MIGRATIONS": "true"})

        self.start_gunicorn.assert_not_called()
        self.start_qcluster.assert_not_called()

    def test_worker_schedules_and_starts_qcluster_without_migrate_or_check(self):
        # RUN_MIGRATIONS absent, BOOT_MODE absent -> legacy worker path.
        self._run_main_with_env({})

        names = _command_names(self.call_command)
        self.assertNotIn("migrate", names)
        self.assertNotIn("check", names)
        self.register_schedules.assert_called_once()
        self.assertEqual(self.initialize_observability.call_count, 1)
        self.start_qcluster.assert_called_once()
        self.start_gunicorn.assert_not_called()
        self.assertEqual(self.persist.call_args.args[0], "worker")

    def test_web_orders_migrate_reconcile_and_suppression_before_gunicorn(self):
        order = []

        def command(*args, **kwargs):
            if args:
                order.append(args[0])

        self.call_command.side_effect = command
        self.suppress_schedules.side_effect = lambda: order.append("suppress")
        self.start_gunicorn.side_effect = lambda *_: order.append("gunicorn")

        self._run_main_with_env({
            "RUN_MIGRATIONS": "true",
            "VERSION": "r1-test-tag",
        })

        self.assertLess(order.index("migrate"), order.index("reconcile_r1_expand"))
        self.assertLess(order.index("reconcile_r1_expand"), order.index("suppress"))
        self.assertLess(order.index("suppress"), order.index("gunicorn"))


class ServingBootSmokeCheckTest(SimpleTestCase):
    def test_truthy_env_defaults_when_missing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(entry._truthy_env("MISSING_FLAG", default=True))
            self.assertFalse(entry._truthy_env("MISSING_FLAG", default=False))

    def test_truthy_env_recognizes_enabled_values(self):
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"FLAG": value}, clear=True):
                    self.assertTrue(entry._truthy_env("FLAG"))

    def test_smoke_check_fails_when_production_email_is_disabled(self):
        with mock.patch.dict(os.environ, {"DEBUG": "false"}, clear=True):
            with self.assertRaisesMessage(
                RuntimeError, "Relay is not configured",
            ):
                entry._run_serving_boot_smoke_check()

    def test_smoke_check_passes_when_production_email_is_enabled(self):
        with mock.patch.dict(
            os.environ,
            {"DEBUG": "false", "RELAY_API_KEY": "test-relay-key"},
            clear=True,
        ):
            with mock.patch("builtins.print") as print_:
                entry._run_serving_boot_smoke_check()

        print_.assert_called_with("Serving boot smoke check passed", flush=True)

    def test_smoke_check_can_be_disabled_for_intentional_non_sending_env(self):
        with mock.patch.dict(
            os.environ,
            {
                "DEBUG": "false",
                "SERVING_BOOT_SMOKE_CHECK_ENABLED": "false",
            },
            clear=True,
        ):
            with mock.patch("builtins.print") as print_:
                entry._run_serving_boot_smoke_check()

        print_.assert_called_once()
        self.assertIn("Skipping serving boot smoke check", print_.call_args.args[0])
