"""Tests for the boot instrumentation in ``scripts/entrypoint_init.py``.

Issue #1141 Phase 1: each boot phase (``django.setup``, ``migrate``,
``check``, ``setup_schedules``, and the serve/qcluster handoff) is wrapped
in a timing helper that emits a flushed ``BOOT_TIMING phase=<name>
seconds=<float>`` line so CloudWatch shows a per-phase breakdown on every
real container boot.

We test the importable helpers ``_timed`` / ``_emit_timing`` directly rather
than ``main`` (which ends by handing off to a blocking gunicorn / qcluster),
following the pattern in ``test_entrypoint_schedules.py``.
"""

import io
import re
from contextlib import redirect_stdout
from unittest import mock

from django.core.cache import caches
from django.test import SimpleTestCase, TestCase

from scripts.entrypoint_init import _emit_timing, _timed, persist_boot_timing

_LINE_RE = re.compile(
    r"^BOOT_TIMING phase=(?P<phase>\S+) seconds=(?P<seconds>[0-9]+\.[0-9]+)$"
)


class EmitTimingFormatTest(SimpleTestCase):
    """``_emit_timing`` prints the stable greppable line format."""

    def test_line_format_prefix_phase_and_numeric_seconds(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            _emit_timing("django_setup", 0.347)

        line = buf.getvalue().strip()
        match = _LINE_RE.match(line)
        self.assertIsNotNone(
            match, f"line does not match BOOT_TIMING format: {line!r}"
        )
        self.assertEqual(match.group("phase"), "django_setup")
        # seconds must be parseable as a float and reflect the value passed.
        self.assertEqual(float(match.group("seconds")), 0.347)

    def test_output_is_flushed(self):
        """Flush is mandatory so the line reaches CloudWatch before a crash."""
        with mock.patch("builtins.print") as mock_print:
            _emit_timing("check", 1.5)

        mock_print.assert_called_once()
        _args, kwargs = mock_print.call_args
        self.assertTrue(
            kwargs.get("flush"),
            "BOOT_TIMING line must be printed with flush=True",
        )


class TimedWrapperTest(SimpleTestCase):
    """``_timed`` measures elapsed time and passes the result through."""

    def test_returns_wrapped_call_result(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _timed("migrate", lambda: "sentinel-return")

        self.assertEqual(result, "sentinel-return")
        self.assertIn("BOOT_TIMING phase=migrate seconds=", buf.getvalue())

    def test_measures_elapsed_delta(self):
        """The emitted seconds is the perf_counter delta across the call."""
        with mock.patch(
            "scripts.entrypoint_init.time.perf_counter",
            side_effect=[10.0, 10.5],
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _timed("django_setup", lambda: None)

        self.assertIn(
            "BOOT_TIMING phase=django_setup seconds=0.500", buf.getvalue()
        )

    def test_emits_line_and_propagates_when_phase_raises(self):
        """A raising phase still records timing AND re-raises unchanged.

        Crash semantics must be preserved: the timing line is additive and
        emitted from a finally block, but the original exception propagates
        so ECS still marks a genuinely broken boot as failed.
        """
        def boom():
            raise ValueError("phase blew up")

        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(ValueError):
                _timed("check", boom)

        self.assertIn("BOOT_TIMING phase=check seconds=", buf.getvalue())


class TimedRecordAccumulatorTest(SimpleTestCase):
    """``_timed`` records the SAME elapsed value it emits into ``record``.

    Single source of truth (issue #1142): the number persisted for a phase
    must be exactly the one ``_emit_timing`` prints -- no recomputation.
    """

    def test_records_the_emitted_value_into_the_dict(self):
        record = {}
        with mock.patch(
            "scripts.entrypoint_init.time.perf_counter",
            side_effect=[10.0, 10.5],
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                _timed("django_setup", lambda: None, record=record)

        # The emitted line and the accumulator hold the identical delta.
        self.assertIn(
            "BOOT_TIMING phase=django_setup seconds=0.500", buf.getvalue()
        )
        self.assertEqual(record, {"django_setup": 0.5})

    def test_records_elapsed_even_when_phase_raises(self):
        record = {}
        with mock.patch(
            "scripts.entrypoint_init.time.perf_counter",
            side_effect=[3.0, 3.25],
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                with self.assertRaises(ValueError):
                    _timed("check", lambda: (_ for _ in ()).throw(ValueError()),
                           record=record)

        self.assertEqual(record, {"check": 0.25})

    def test_no_record_keeps_backward_compatible_signature(self):
        # Called without ``record`` (the Phase 1 signature) must still work.
        buf = io.StringIO()
        with redirect_stdout(buf):
            result = _timed("migrate", lambda: "ok")
        self.assertEqual(result, "ok")


class PersistBootTimingTest(TestCase):
    """``persist_boot_timing`` writes to the shared ``django_q`` cache."""

    def setUp(self):
        caches["django_q"].clear()

    def test_writes_expected_payload_under_role_key(self):
        phases = {"django_setup": 4.2, "check": 1.3, "total": 5.5}
        with mock.patch.dict(
            "os.environ", {"VERSION": "20260708-ab12cd3"}, clear=False,
        ):
            persist_boot_timing("web", phases)

        stored = caches["django_q"].get("boot_timing:web")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["tag"], "20260708-ab12cd3")
        self.assertEqual(stored["role"], "web")
        self.assertEqual(stored["phases"], phases)
        self.assertIn("recorded_at", stored)

    def test_tag_falls_back_to_unknown_when_version_unset(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            persist_boot_timing("worker", {"total": 1.0})

        stored = caches["django_q"].get("boot_timing:worker")
        self.assertEqual(stored["tag"], "unknown")
        self.assertEqual(stored["role"], "worker")

    def test_worker_and_web_use_distinct_keys(self):
        persist_boot_timing("web", {"total": 2.0})
        persist_boot_timing("worker", {"total": 1.0})

        self.assertEqual(
            caches["django_q"].get("boot_timing:web")["role"], "web",
        )
        self.assertEqual(
            caches["django_q"].get("boot_timing:worker")["role"], "worker",
        )

    def test_cache_set_error_is_swallowed_not_propagated(self):
        """The key risk: a store failure at boot must NEVER crash the tier."""
        with mock.patch.object(
            caches["django_q"], "set", side_effect=RuntimeError("db down"),
        ):
            with self.assertLogs(
                "scripts.entrypoint_init", level="ERROR",
            ) as logs:
                # Must NOT raise.
                persist_boot_timing("web", {"total": 1.0})

        self.assertTrue(
            any("persist_boot_timing failed" in m for m in logs.output),
        )
