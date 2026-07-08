"""Unit tests for scripts/boot_profile_report.py.

Pure-Python parser tests — no Docker, no live boot required, so they run in CI.
The module is loaded by path (it lives in scripts/, not an importable package),
mirroring tests/test_update_task_def.py.
"""

import importlib.util
from pathlib import Path

from django.test import SimpleTestCase


def _load_report_module():
    module_path = Path(__file__).resolve().parent.parent / "scripts" / "boot_profile_report.py"
    spec = importlib.util.spec_from_file_location("boot_profile_report", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load_report_module()


# A realistic single-boot stdout blob with interleaved non-timing log noise.
SAMPLE_OFF_BOOT = """\
Apply database migrations
BOOT_TIMING phase=django_setup seconds=1.200
Operations to perform:
BOOT_TIMING phase=migrate seconds=0.400
Database migrations applied successfully.
Run Django system checks (fail on Error level)
BOOT_TIMING phase=check seconds=0.300
Register recurring job schedules
BOOT_TIMING phase=setup_schedules seconds=0.100
BOOT_TIMING phase=total seconds=2.000
Starting server
"""

SAMPLE_ON_BOOT = """\
BOOT_TIMING phase=django_setup seconds=1.700
BOOT_TIMING phase=migrate seconds=0.400
BOOT_TIMING phase=check seconds=0.300
BOOT_TIMING phase=setup_schedules seconds=0.100
BOOT_TIMING phase=total seconds=2.500
Starting server
"""


class ParseBootTimingTest(SimpleTestCase):
    def test_parses_phases_in_order_and_ignores_noise(self):
        parsed = report.parse_boot_timing(SAMPLE_OFF_BOOT)
        self.assertEqual(
            list(parsed.keys()),
            ["django_setup", "migrate", "check", "setup_schedules", "total"],
        )
        self.assertEqual(parsed["django_setup"], 1.2)
        self.assertEqual(parsed["total"], 2.0)

    def test_malformed_lines_are_skipped(self):
        blob = (
            "BOOT_TIMING phase=django_setup seconds=1.000\n"
            "BOOT_TIMING phase=migrate seconds=notanumber\n"  # bad float
            "BOOT_TIMING phase=check\n"                       # missing seconds
            "BOOT_TIMING seconds=0.5\n"                       # missing phase
            "random log line seconds=9.9\n"                   # not a BOOT_TIMING line
            "BOOT_TIMING phase=total seconds=2.000\n"
        )
        parsed = report.parse_boot_timing(blob)
        self.assertEqual(list(parsed.keys()), ["django_setup", "total"])
        self.assertNotIn("migrate", parsed)
        self.assertNotIn("check", parsed)

    def test_last_value_wins_on_duplicate_phase(self):
        blob = (
            "BOOT_TIMING phase=migrate seconds=0.100\n"
            "BOOT_TIMING phase=migrate seconds=0.900\n"
        )
        self.assertEqual(report.parse_boot_timing(blob)["migrate"], 0.9)

    def test_empty_blob_yields_no_phases(self):
        self.assertEqual(dict(report.parse_boot_timing("")), {})


class AggregatePhasesTest(SimpleTestCase):
    def test_min_and_median_over_iterations(self):
        captures = [
            {"django_setup": 1.0, "total": 2.0},
            {"django_setup": 3.0, "total": 4.0},
            {"django_setup": 2.0, "total": 3.0},
        ]
        agg = report.aggregate_phases(captures)
        self.assertEqual(agg["django_setup"]["min"], 1.0)
        self.assertEqual(agg["django_setup"]["median"], 2.0)  # median of 1,2,3
        self.assertEqual(agg["django_setup"]["n"], 3)
        self.assertEqual(agg["total"]["min"], 2.0)
        self.assertEqual(agg["total"]["median"], 3.0)

    def test_even_count_median_averages_two_middle(self):
        captures = [{"total": 2.0}, {"total": 4.0}]
        self.assertEqual(report.aggregate_phases(captures)["total"]["median"], 3.0)

    def test_preserves_first_seen_phase_order(self):
        captures = [{"django_setup": 1.0, "migrate": 0.5, "total": 2.0}]
        self.assertEqual(
            list(report.aggregate_phases(captures).keys()),
            ["django_setup", "migrate", "total"],
        )

    def test_phase_missing_from_some_captures(self):
        captures = [{"total": 2.0}, {"total": 3.0, "extra": 5.0}]
        agg = report.aggregate_phases(captures)
        self.assertEqual(agg["extra"]["n"], 1)
        self.assertEqual(agg["extra"]["median"], 5.0)


class LogfireDeltaTest(SimpleTestCase):
    def test_positive_tax_delta(self):
        off = [report.parse_boot_timing(SAMPLE_OFF_BOOT)]
        on = [report.parse_boot_timing(SAMPLE_ON_BOOT)]
        delta = report.compute_logfire_delta(off, on)
        self.assertEqual(delta["phase"], "django_setup")
        self.assertEqual(delta["off_median"], 1.2)
        self.assertEqual(delta["on_median"], 1.7)
        self.assertAlmostEqual(delta["delta"], 0.5)

    def test_delta_uses_medians_over_multiple_captures(self):
        off = [{"django_setup": 1.0}, {"django_setup": 2.0}, {"django_setup": 3.0}]
        on = [{"django_setup": 2.0}, {"django_setup": 3.0}, {"django_setup": 4.0}]
        delta = report.compute_logfire_delta(off, on)
        self.assertEqual(delta["off_median"], 2.0)
        self.assertEqual(delta["on_median"], 3.0)
        self.assertAlmostEqual(delta["delta"], 1.0)

    def test_missing_side_yields_none_delta(self):
        delta = report.compute_logfire_delta([{"migrate": 1.0}], [{"django_setup": 2.0}])
        self.assertIsNone(delta["off_median"])
        self.assertEqual(delta["on_median"], 2.0)
        self.assertIsNone(delta["delta"])


class RenderTest(SimpleTestCase):
    def test_build_report_contains_all_sections(self):
        off = [report.parse_boot_timing(SAMPLE_OFF_BOOT)]
        on = [report.parse_boot_timing(SAMPLE_ON_BOOT)]
        text = report.build_report(warm_off=off, warm_on=on)
        self.assertIn("Logfire off", text)
        self.assertIn("django_setup", text)
        self.assertIn("Faithfulness caveats", text)
        # The delta line reports the positive tax (0.500).
        self.assertIn("0.500", text)

    def test_aggregate_table_has_min_and_median_columns(self):
        agg = report.aggregate_phases([{"total": 2.0}, {"total": 4.0}])
        table = report.render_aggregate_table(agg, "warm")
        self.assertIn("min", table)
        self.assertIn("median", table)
        self.assertIn("2.000", table)
        self.assertIn("3.000", table)

    def test_caveats_mention_rds_and_relative(self):
        caveats = report.render_caveats()
        self.assertIn("RDS", caveats)
        self.assertIn("RELATIVE", caveats)
