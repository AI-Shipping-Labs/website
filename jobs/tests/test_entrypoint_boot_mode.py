"""Tests for the ``BOOT_MODE`` dispatch in ``scripts/entrypoint_init.py``.

Issue #1141 Phase 2A: the entrypoint dispatches on the ``BOOT_MODE`` env var
to untangle web-vs-worker role dispatch from migrate-on-boot:

* ``predeploy`` -> ``django.setup`` -> ``migrate`` -> ``check`` -> exit 0
  (no schedules, no gunicorn, no qcluster). A failing migrate/check propagates.
* ``web`` -> ``django.setup`` -> schedules -> gunicorn (SKIPS migrate + check).
* ``worker`` -> ``django.setup`` -> schedules -> qcluster (SKIPS migrate + check).
* absent -> exactly the legacy ``RUN_MIGRATIONS`` behavior (backward-compat).

Phase 2C: gunicorn ``--workers`` is read from ``os.environ['GUNICORN_WORKERS']``.

We drive ``main()`` with the blocking handoffs (``_start_gunicorn`` /
``_start_qcluster``), ``django.setup``, ``_register_schedules`` and
``persist_boot_timing`` patched so no real boot or DB write happens, and assert
exactly which phases run in each mode. ``call_command`` (used by the migrate /
check helpers) is patched so we can assert what was and was NOT invoked.
"""

import os
import sys
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
            mock.patch.object(entry, "persist_boot_timing"),
            mock.patch("django.core.management.call_command"),
        ):
            p.start()
            self.addCleanup(p.stop)

        # After patching, the module attributes ARE the mocks.
        self.start_gunicorn = entry._start_gunicorn
        self.start_qcluster = entry._start_qcluster
        self.register_schedules = entry._register_schedules
        self.persist = entry.persist_boot_timing
        # The migrate/check helpers import call_command lazily from this path.
        import django.core.management as mgmt

        self.call_command = mgmt.call_command

    def _run_main_with_env(self, env):
        with mock.patch.dict(os.environ, env, clear=True):
            entry.main()


class PredeployModeTest(BootModeDispatchTestBase):
    def test_predeploy_runs_migrate_and_check_then_exits(self):
        self._run_main_with_env({"BOOT_MODE": "predeploy"})

        names = _command_names(self.call_command)
        self.assertIn("migrate", names)
        self.assertIn("check", names)

        # The #529 gate runs with --fail-level ERROR.
        check_call = next(
            c for c in self.call_command.call_args_list
            if c.args and c.args[0] == "check"
        )
        self.assertEqual(check_call.args, ("check", "--fail-level", "ERROR"))

        # No serving handoffs, no schedules.
        self.start_gunicorn.assert_not_called()
        self.start_qcluster.assert_not_called()
        self.register_schedules.assert_not_called()

    def test_predeploy_does_not_persist_boot_timing(self):
        # A predeploy task is not a serving container; it must not overwrite
        # the web/worker boot-timing diagnostics payload.
        self._run_main_with_env({"BOOT_MODE": "predeploy"})
        self.persist.assert_not_called()

    def test_predeploy_migrate_failure_propagates_and_skips_check_and_serve(self):
        def boom(*args, **kwargs):
            if args and args[0] == "migrate":
                raise RuntimeError("migration failed")

        self.call_command.side_effect = boom

        with self.assertRaises(RuntimeError):
            self._run_main_with_env({"BOOT_MODE": "predeploy"})

        names = _command_names(self.call_command)
        self.assertIn("migrate", names)
        # check must NOT run after migrate blew up.
        self.assertNotIn("check", names)
        self.start_gunicorn.assert_not_called()
        self.start_qcluster.assert_not_called()

    def test_predeploy_check_failure_propagates(self):
        def boom(*args, **kwargs):
            if args and args[0] == "check":
                raise RuntimeError("email_app.E001")

        self.call_command.side_effect = boom

        with self.assertRaises(RuntimeError):
            self._run_main_with_env({"BOOT_MODE": "predeploy"})

        # Propagating the failure is what fails the pre-deploy ECS task, which
        # aborts the deploy without rolling the service.
        self.start_gunicorn.assert_not_called()
        self.start_qcluster.assert_not_called()


class WebModeTest(BootModeDispatchTestBase):
    def test_web_registers_schedules_and_serves_without_migrate_or_check(self):
        self._run_main_with_env({"BOOT_MODE": "web"})

        names = _command_names(self.call_command)
        self.assertNotIn("migrate", names)
        self.assertNotIn("check", names)

        self.register_schedules.assert_called_once()
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


class WorkerModeTest(BootModeDispatchTestBase):
    def test_worker_registers_schedules_and_starts_qcluster_only(self):
        self._run_main_with_env({"BOOT_MODE": "worker"})

        names = _command_names(self.call_command)
        self.assertNotIn("migrate", names)
        self.assertNotIn("check", names)

        self.register_schedules.assert_called_once()
        self.start_qcluster.assert_called_once()
        self.start_gunicorn.assert_not_called()
        self.persist.assert_called_once()
        self.assertEqual(self.persist.call_args.args[0], "worker")


class LegacyBackwardCompatTest(BootModeDispatchTestBase):
    """BOOT_MODE absent must behave EXACTLY as before this change."""

    def test_legacy_web_migrates_checks_schedules_and_serves(self):
        # RUN_MIGRATIONS=true, BOOT_MODE absent -> the old web path.
        self._run_main_with_env({"RUN_MIGRATIONS": "true"})

        names = _command_names(self.call_command)
        self.assertIn("migrate", names)
        self.assertIn("check", names)
        self.register_schedules.assert_called_once()
        self.start_gunicorn.assert_called_once()
        self.start_qcluster.assert_not_called()
        self.assertEqual(self.persist.call_args.args[0], "web")

    def test_legacy_worker_checks_schedules_and_starts_qcluster_no_migrate(self):
        # RUN_MIGRATIONS absent, BOOT_MODE absent -> the old worker path.
        self._run_main_with_env({})

        names = _command_names(self.call_command)
        self.assertNotIn("migrate", names)
        self.assertIn("check", names)
        self.register_schedules.assert_called_once()
        self.start_qcluster.assert_called_once()
        self.start_gunicorn.assert_not_called()
        self.assertEqual(self.persist.call_args.args[0], "worker")

    def test_legacy_web_ordering_migrate_before_check(self):
        self._run_main_with_env({"RUN_MIGRATIONS": "true"})
        names = _command_names(self.call_command)
        self.assertLess(names.index("migrate"), names.index("check"))


class GunicornWorkerCountTest(SimpleTestCase):
    def test_defaults_to_three_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(entry._gunicorn_worker_count(), 3)

    def test_reads_positive_integer(self):
        with mock.patch.dict(os.environ, {"GUNICORN_WORKERS": "2"}, clear=True):
            self.assertEqual(entry._gunicorn_worker_count(), 2)

    def test_non_integer_falls_back_to_three_with_warning(self):
        with mock.patch.dict(os.environ, {"GUNICORN_WORKERS": "abc"}, clear=True):
            with self.assertLogs("scripts.entrypoint_init", level="WARNING") as cm:
                self.assertEqual(entry._gunicorn_worker_count(), 3)
        self.assertTrue(any("GUNICORN_WORKERS" in m for m in cm.output))

    def test_zero_or_negative_falls_back_to_three_with_warning(self):
        for bad in ("0", "-4"):
            with self.subTest(value=bad):
                with mock.patch.dict(
                    os.environ, {"GUNICORN_WORKERS": bad}, clear=True
                ):
                    with self.assertLogs(
                        "scripts.entrypoint_init", level="WARNING"
                    ):
                        self.assertEqual(entry._gunicorn_worker_count(), 3)


class StartGunicornArgvTest(SimpleTestCase):
    """``_start_gunicorn`` builds the gunicorn argv with the given worker count."""

    def test_argv_contains_worker_count(self):
        saved_argv = sys.argv
        try:
            with mock.patch("gunicorn.app.wsgiapp.run") as run:
                entry._start_gunicorn(2)
                run.assert_called_once()
                built = sys.argv
        finally:
            sys.argv = saved_argv

        self.assertEqual(built[0], "gunicorn")
        self.assertIn("website.wsgi:application", built)
        # --workers is followed by the count as a string.
        self.assertIn("--workers", built)
        self.assertEqual(built[built.index("--workers") + 1], "2")
        self.assertIn("--preload", built)
        self.assertIn("--config", built)
        self.assertEqual(
            built[built.index("--config") + 1],
            "python:website.gunicorn_conf",
        )

    def test_qcluster_initializes_logfire_after_boot_timing_path(self):
        with mock.patch(
            "integrations.services.observability.init_logfire_once"
        ) as init_logfire, mock.patch(
            "django.core.management.call_command"
        ) as call_command:
            entry._start_qcluster()

        init_logfire.assert_called_once()
        call_command.assert_called_once_with("qcluster", verbosity=1)
