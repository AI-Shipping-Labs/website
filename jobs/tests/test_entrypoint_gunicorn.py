"""Gunicorn serving settings for ``scripts/entrypoint_init.py``.

Issue #1141 Phase 2C made ``--workers`` a deploy-time env value. Issue #1854
adds ``gthread`` threads, explicit worker/graceful timeouts, and a web-only
Postgres ``statement_timeout`` installed right before the gunicorn handoff.
"""

import os
import sys
from unittest import mock

from django.db.utils import ConnectionHandler
from django.test import SimpleTestCase

import scripts.entrypoint_init as entry
from scripts import gunicorn_runtime
from scripts.gunicorn_runtime import apply_web_statement_timeout

RUNTIME_LOGGER = "scripts.gunicorn_runtime"


class GunicornWorkerCountTest(SimpleTestCase):
    def test_defaults_to_three_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(entry._gunicorn_worker_count(), 3)

    def test_reads_positive_integer(self):
        with mock.patch.dict(os.environ, {"GUNICORN_WORKERS": "2"}, clear=True):
            self.assertEqual(entry._gunicorn_worker_count(), 2)

    def test_invalid_values_fall_back_to_three_with_warning(self):
        for bad in ("abc", "0", "-4"):
            with self.subTest(value=bad):
                env = {"GUNICORN_WORKERS": bad}
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertLogs(RUNTIME_LOGGER, level="WARNING") as cm:
                        self.assertEqual(entry._gunicorn_worker_count(), 3)
                self.assertIn("GUNICORN_WORKERS", cm.output[0])


class GunicornThreadCountTest(SimpleTestCase):
    def test_defaults_to_four_when_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gunicorn_runtime.gunicorn_thread_count(), 4)

    def test_reads_positive_integer(self):
        with mock.patch.dict(os.environ, {"GUNICORN_THREADS": "8"}, clear=True):
            self.assertEqual(gunicorn_runtime.gunicorn_thread_count(), 8)

    def test_invalid_values_fall_back_to_four_with_warning(self):
        for bad in ("many", "0", "-1"):
            with self.subTest(value=bad):
                env = {"GUNICORN_THREADS": bad}
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertLogs(RUNTIME_LOGGER, level="WARNING") as cm:
                        self.assertEqual(gunicorn_runtime.gunicorn_thread_count(), 4)
                self.assertIn("GUNICORN_THREADS", cm.output[0])


class WebStatementTimeoutEnvTest(SimpleTestCase):
    def test_defaults_to_ten_seconds(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(gunicorn_runtime.web_statement_timeout_ms(), 10_000)

    def test_zero_disables(self):
        env = {"WEB_DB_STATEMENT_TIMEOUT_MS": "0"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(gunicorn_runtime.web_statement_timeout_ms(), 0)

    def test_invalid_values_fall_back_to_default(self):
        for bad in ("soon", "-5"):
            with self.subTest(value=bad):
                env = {"WEB_DB_STATEMENT_TIMEOUT_MS": bad}
                with mock.patch.dict(os.environ, env, clear=True):
                    with self.assertLogs(RUNTIME_LOGGER, level="WARNING"):
                        self.assertEqual(
                            gunicorn_runtime.web_statement_timeout_ms(), 10_000,
                        )


class StartGunicornTest(SimpleTestCase):
    """``_start_gunicorn`` caps web SQL, then execs gthread gunicorn."""

    def _start(self, env, workers=2):
        order = []
        saved_argv = sys.argv
        try:
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
                entry,
                "apply_web_statement_timeout",
                side_effect=lambda ms: order.append(("timeout", ms)),
            ), mock.patch(
                "gunicorn.app.wsgiapp.run",
                side_effect=lambda: order.append(("gunicorn", list(sys.argv))),
            ):
                entry._start_gunicorn(workers)
        finally:
            sys.argv = saved_argv
        return order

    @staticmethod
    def _flag(argv, name):
        return argv[argv.index(name) + 1]

    def test_argv_uses_gthread_with_configured_threads_and_timeouts(self):
        order = self._start({"GUNICORN_THREADS": "6"})
        argv = order[-1][1]

        self.assertEqual(argv[:2], ["gunicorn", "website.wsgi:application"])
        self.assertEqual(self._flag(argv, "--worker-class"), "gthread")
        self.assertEqual(self._flag(argv, "--workers"), "2")
        self.assertEqual(self._flag(argv, "--threads"), "6")
        self.assertEqual(self._flag(argv, "--timeout"), "30")
        self.assertEqual(self._flag(argv, "--graceful-timeout"), "20")
        self.assertEqual(self._flag(argv, "--keep-alive"), "0")
        self.assertIn("--preload", argv)

    def test_statement_timeout_is_applied_before_gunicorn_starts(self):
        order = self._start({"WEB_DB_STATEMENT_TIMEOUT_MS": "7000"})

        self.assertEqual(order[0], ("timeout", 7000))
        self.assertEqual(order[1][0], "gunicorn")

    def test_worker_handoff_does_not_cap_statements(self):
        with mock.patch.object(entry, "apply_web_statement_timeout") as apply_, \
                mock.patch("django.core.management.call_command"), \
                mock.patch.dict(os.environ, {}, clear=True):
            entry._start_qcluster()

        apply_.assert_not_called()


def _handler(pg_options=None):
    postgres = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "aisl",
        "HOST": "db.internal",
    }
    if pg_options is not None:
        postgres["OPTIONS"] = pg_options
    return ConnectionHandler({
        "default": postgres,
        "lite": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    })


class ApplyWebStatementTimeoutTest(SimpleTestCase):
    def test_postgres_connections_start_with_the_timeout(self):
        handler = _handler()

        applied = apply_web_statement_timeout(10_000, handler)

        self.assertEqual(applied, ["default"])
        params = handler["default"].get_connection_params()
        self.assertEqual(params["options"], "-c statement_timeout=10000")

    def test_existing_libpq_options_are_preserved(self):
        handler = _handler({"options": "-c search_path=public"})

        apply_web_statement_timeout(5_000, handler)

        self.assertEqual(
            handler["default"].get_connection_params()["options"],
            "-c search_path=public -c statement_timeout=5000",
        )

    def test_sqlite_is_untouched(self):
        handler = _handler()

        apply_web_statement_timeout(10_000, handler)

        self.assertNotIn("options", handler["lite"].settings_dict["OPTIONS"])

    def test_zero_leaves_postgres_unlimited(self):
        handler = _handler()

        self.assertEqual(apply_web_statement_timeout(0, handler), [])
        self.assertNotIn("options", handler["default"].get_connection_params())
