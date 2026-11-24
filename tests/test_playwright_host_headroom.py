"""Host-scaled browser admission on small CI runners (#2133)."""

from dataclasses import replace
from unittest import mock

from django.test import SimpleTestCase

from playwright_tests.local_capacity import GIB, sample_host_headroom
from tests.test_playwright_local_capacity import _healthy_sample


class HostHeadroomTests(SimpleTestCase):
    def runner_sample(self, **changes):
        sample = replace(
            _healthy_sample(),
            logical_cpus=2,
            load_average_1m=0.5,
            total_memory_bytes=4 * GIB,
            available_memory_bytes=24 * GIB // 10,
        )
        return replace(sample, **changes)

    def test_four_gib_runner_admits_one_browser(self):
        self.assertEqual(self.runner_sample().admits(1), (True, []))

    def test_memory_budget_retains_reserve_and_one_gib_per_browser(self):
        for total_gib, slots, required_gib in ((4, 1, 2), (8, 2, 4), (16, 4, 8), (64, 4, 8)):
            with self.subTest(total_gib=total_gib, slots=slots):
                sample = self.runner_sample(
                    logical_cpus=12,
                    total_memory_bytes=total_gib * GIB,
                    available_memory_bytes=required_gib * GIB,
                )
                self.assertEqual(sample.admits(slots), (True, []))
                low_memory = replace(sample, available_memory_bytes=required_gib * GIB - 1)
                admitted, failures = low_memory.admits(slots)
                self.assertFalse(admitted)
                self.assertEqual(len(failures), 1)
                self.assertIn(f"required {required_gib * GIB} bytes", failures[0])

    def test_unknown_or_invalid_total_retains_conservative_reserve(self):
        for total in (None, 0, -1):
            with self.subTest(total=total):
                sample = self.runner_sample(total_memory_bytes=total)
                admitted, failures = sample.admits(1)
                self.assertFalse(admitted)
                self.assertIn(f"required {5 * GIB} bytes", failures[0])

    def test_small_runner_still_rejects_cpu_and_memory_pressure(self):
        for changes, reason in (
            ({"load_average_1m": 1.1}, "load1 1.10 + request 1 > CPUs 2"),
            ({"memory_full_avg60": 10.1}, "memory full avg60 10.10% > 10.00%"),
        ):
            with self.subTest(changes=changes):
                self.assertEqual(self.runner_sample(**changes).admits(1), (False, [reason]))

    def test_host_sampler_reads_total_available_and_swap_from_same_sample(self):
        meminfo = "MemTotal: 4194304 kB\nMemAvailable: 2516582 kB\nSwapTotal: 1048576 kB\nSwapFree: 524288 kB\n"
        with (
            mock.patch("playwright_tests.local_capacity.platform.system", return_value="Linux"),
            mock.patch("playwright_tests.local_capacity.Path.read_text", return_value=meminfo),
        ):
            sample = sample_host_headroom()
        self.assertEqual(sample.total_memory_bytes, 4 * GIB)
        self.assertEqual(sample.available_memory_bytes, 2516582 * 1024)
        self.assertEqual(sample.swap_total_bytes, GIB)
        self.assertEqual(sample.swap_used_bytes, GIB // 2)
        self.assertEqual(sample.diagnostic()["total_memory_bytes"], 4 * GIB)

    def test_missing_or_unreadable_total_is_reported_as_unavailable(self):
        for meminfo, error in (
            ("MemAvailable: 2516582 kB\n", None),
            ("MemTotal: malformed kB\nMemAvailable: 2516582 kB\n", None),
            ("", OSError("unavailable")),
        ):
            with (
                self.subTest(error=error),
                mock.patch("playwright_tests.local_capacity.platform.system", return_value="Linux"),
                mock.patch("playwright_tests.local_capacity.Path.read_text", return_value=meminfo, side_effect=error),
            ):
                sample = sample_host_headroom()
                self.assertIsNone(sample.total_memory_bytes)
                self.assertEqual(sample.diagnostic()["total_memory_bytes"], "unavailable")
