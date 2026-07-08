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

from django.test import SimpleTestCase

from scripts.entrypoint_init import _emit_timing, _timed

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
