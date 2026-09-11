"""Deterministic policy coverage for local Playwright capacity admission."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import SimpleTestCase

from playwright_tests import conftest
from playwright_tests.local_capacity import (
    CAPACITY_ENV_VAR,
    WAIT_ENV_VAR,
    CapacityAdmissionError,
    CapacityConfigurationError,
    HostHeadroom,
    LocalPlaywrightCapacity,
    _validated_holder_metadata,
    common_git_directory,
    configured_capacity,
    configured_wait_seconds,
)


def _healthy_sample():
    return HostHeadroom(
        platform="Linux",
        logical_cpus=12,
        load_average_1m=1.0,
        load_average_5m=2.0,
        load_average_15m=3.0,
        available_memory_bytes=32 * 1024**3,
        swap_total_bytes=8 * 1024**3,
        swap_used_bytes=1024**3,
        cpu_pressure="some avg10=1.00 avg60=1.00 avg300=1.00 total=1",
        memory_pressure="full avg10=1.00 avg60=1.00 avg300=1.00 total=1",
        memory_full_avg60=1.0,
    )


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleeps = []
        self.on_sleep = None

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.value += seconds
        callback, self.on_sleep = self.on_sleep, None
        if callback is not None:
            callback()


class CapacityPolicyTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="playwright-capacity-1605-")
        self.root = Path(self.temporary_directory.name)
        self.common = self.root / "common.git"
        self.common.mkdir()

    def tearDown(self):
        self.temporary_directory.cleanup()
        super().tearDown()

    def coordinator(self, name, slots, *, clock=None, **kwargs):
        clock = clock or FakeClock()
        parameters = {
            "common_dir": self.common,
            "worktree_root": self.root / name,
            "requested_slots": slots,
            "capacity": 4,
            "max_wait_seconds": 20,
            "sample_interval": 1,
            "required_samples": 1,
            "headroom_sampler": _healthy_sample,
            "monotonic": clock.monotonic,
            "sleeper": clock.sleep,
            "output": io.StringIO(),
        }
        parameters.update(kwargs)
        return LocalPlaywrightCapacity(
            **parameters,
        )

    def test_default_width_waits_then_acquires_after_release(self):
        holder = self.coordinator("first", 4)
        holder.acquire()
        clock = FakeClock()
        claimant = self.coordinator("second", 4, clock=clock)
        partial_widths = []

        def release_holder():
            partial_widths.append(len(claimant._slot_files))
            holder.release()

        clock.on_sleep = release_holder

        grant = claimant.acquire()
        try:
            self.assertEqual(grant.requested_slots, 4)
            self.assertEqual(grant.wait_seconds, 1.0)
            self.assertIn("requested=4 capacity=4", claimant.output.getvalue())
            self.assertIn(f'"pid": {os.getpid()}', claimant.output.getvalue())
            self.assertEqual(partial_widths, [0])
            self.assertEqual(len(claimant._slot_files), 4)
        finally:
            claimant.release()

    def test_two_reduced_width_runs_share_budget_and_third_waits(self):
        first = self.coordinator("first", 2)
        second = self.coordinator("second", 2)
        first.acquire()
        second.acquire()
        self.assertEqual(len(first._slot_files) + len(second._slot_files), 4)

        clock = FakeClock()
        third = self.coordinator("third", 1, clock=clock)
        clock.on_sleep = first.release
        try:
            grant = third.acquire()
            self.assertEqual(grant.requested_slots, 1)
            self.assertEqual(grant.wait_seconds, 1.0)
        finally:
            third.release()
            second.release()

    def test_independent_repositories_do_not_share_capacity(self):
        first = self.coordinator("first", 4)
        first.acquire()
        other_common = self.root / "other.git"
        other_common.mkdir()
        other = LocalPlaywrightCapacity(
            common_dir=other_common,
            worktree_root=self.root / "other-worktree",
            requested_slots=4,
            capacity=4,
            required_samples=1,
            headroom_sampler=_healthy_sample,
            output=io.StringIO(),
        )
        try:
            other.acquire()
            self.assertNotEqual(first.directory, other.directory)
        finally:
            other.release()
            first.release()

    def test_busy_host_requires_three_consecutive_passing_samples(self):
        busy_cpu = HostHeadroom(**{**_healthy_sample().__dict__, "load_average_1m": 11.0})
        low_memory = HostHeadroom(**{**_healthy_sample().__dict__, "available_memory_bytes": 5 * 1024**3})
        pressured = HostHeadroom(**{**_healthy_sample().__dict__, "memory_full_avg60": 12.0})
        samples = iter([busy_cpu, low_memory, pressured, _healthy_sample(), _healthy_sample(), _healthy_sample()])
        clock = FakeClock()
        coordinator = self.coordinator(
            "busy",
            4,
            clock=clock,
            required_samples=3,
            headroom_sampler=lambda: next(samples),
        )

        grant = coordinator.acquire()
        try:
            self.assertEqual(grant.wait_seconds, 5.0)
            self.assertEqual(clock.sleeps, [1, 1, 1, 1, 1])
            output = coordinator.output.getvalue()
            self.assertIn("load1 11.00 + request 4 > CPUs 12", output)
            self.assertIn("memory full avg60 12.00% > 10.00%", output)
        finally:
            coordinator.release()

    def test_wait_expiry_is_admission_error_before_any_test_starts(self):
        holder = self.coordinator("first", 4)
        holder.acquire()
        clock = FakeClock()
        claimant = self.coordinator("second", 4, clock=clock, max_wait_seconds=2)
        try:
            with self.assertRaises(CapacityAdmissionError) as raised:
                claimant.acquire()
        finally:
            holder.release()

        message = str(raised.exception)
        self.assertIn("expired before test collection", message)
        self.assertIn("requested=4 capacity=4", message)
        self.assertIn("Zero tests started", message)
        self.assertEqual(claimant._slot_files, [])

    def test_request_wider_than_capacity_is_rejected_without_sleeping(self):
        clock = FakeClock()
        claimant = self.coordinator("too-wide", 5, clock=clock)
        with self.assertRaises(CapacityAdmissionError) as raised:
            claimant.acquire()
        self.assertIn("requested width 5 exceeds repository capacity 4", str(raised.exception))
        self.assertEqual(clock.sleeps, [])

    def test_unavailable_linux_metrics_fall_back_to_repository_capacity(self):
        unavailable = HostHeadroom(
            platform="Linux",
            logical_cpus=None,
            load_average_1m=None,
            load_average_5m=None,
            load_average_15m=None,
            available_memory_bytes=None,
            swap_total_bytes=None,
            swap_used_bytes=None,
            cpu_pressure=None,
            memory_pressure=None,
            memory_full_avg60=None,
        )
        coordinator = self.coordinator(
            "unsupported-metrics",
            1,
            headroom_sampler=lambda: unavailable,
        )
        grant = coordinator.acquire()
        try:
            self.assertEqual(grant.headroom.diagnostic()["logical_cpus"], "unavailable")
        finally:
            coordinator.release()

    def test_non_linux_falls_back_without_a_three_sample_delay(self):
        sample = HostHeadroom(
            platform="Darwin",
            logical_cpus=8,
            load_average_1m=None,
            load_average_5m=None,
            load_average_15m=None,
            available_memory_bytes=None,
            swap_total_bytes=None,
            swap_used_bytes=None,
            cpu_pressure=None,
            memory_pressure=None,
            memory_full_avg60=None,
        )
        clock = FakeClock()
        coordinator = self.coordinator(
            "non-linux",
            1,
            clock=clock,
            required_samples=3,
            headroom_sampler=lambda: sample,
        )
        coordinator.acquire()
        try:
            self.assertEqual(clock.sleeps, [])
        finally:
            coordinator.release()

    def test_os_releases_killed_holder_and_stale_metadata_cannot_block(self):
        script = """
import sys
from pathlib import Path
from playwright_tests.local_capacity import LocalPlaywrightCapacity
from tests.test_playwright_local_capacity import _healthy_sample
holder = LocalPlaywrightCapacity(
    common_dir=Path(sys.argv[1]), worktree_root=Path(sys.argv[2]),
    requested_slots=4, capacity=4, required_samples=1,
    headroom_sampler=_healthy_sample, output=sys.stderr,
)
holder.acquire()
print("READY", flush=True)
sys.stdin.read()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.common), str(self.root / "killed")],
            cwd=Path(__file__).resolve().parent.parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(process.stdout.readline().strip(), "READY")
            process.kill()
            process.wait(timeout=10)
            replacement = self.coordinator("replacement", 4)
            replacement.acquire()
            replacement.release()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    def test_holder_metadata_requires_matching_pid_start_and_redacts_command(self):
        metadata = {
            "pid": os.getpid(),
            "process_start_id": "expected",
            "command": "pytest --token=secret postgresql://user:pass@db.example/app",
            "worktree": str(self.root),
        }
        with mock.patch(
            "playwright_tests.local_capacity._process_start_id",
            return_value="expected",
        ):
            sanitized = _validated_holder_metadata(metadata)
        self.assertNotIn("secret", json.dumps(sanitized))
        self.assertNotIn("user:pass", json.dumps(sanitized))
        self.assertIn("--token=<redacted>", sanitized["command"])

        with mock.patch(
            "playwright_tests.local_capacity._process_start_id",
            return_value="reused-pid",
        ):
            self.assertIsNone(_validated_holder_metadata(metadata))


class CapacityConfigurationTests(SimpleTestCase):
    def test_capacity_and_wait_overrides_are_bounded(self):
        self.assertEqual(configured_capacity({}), 4)
        self.assertEqual(configured_capacity({CAPACITY_ENV_VAR: "2"}), 2)
        self.assertEqual(configured_wait_seconds({}), 7_200.0)
        self.assertEqual(configured_wait_seconds({WAIT_ENV_VAR: "0.25"}), 0.25)
        for raw in ("0", "65", "nope"):
            with self.subTest(raw=raw):
                with self.assertRaises(CapacityConfigurationError):
                    configured_capacity({CAPACITY_ENV_VAR: raw})

    def test_common_git_directory_is_shared_by_main_and_this_worktree(self):
        worktree_common = common_git_directory(Path(__file__).resolve().parent.parent)
        main_common = common_git_directory(worktree_common.parent)
        self.assertEqual(worktree_common, main_common)

    def test_controller_requests_exact_width_and_remote_or_worker_claims_nothing(self):
        class FakeCapacity:
            @classmethod
            def for_current_worktree(cls, requested_slots):
                events.append(("create", requested_slots))
                return cls()

            def acquire(self):
                events.append(("acquire", None))

        for raw, expected in ((None, 1), (0, 1), (2, 2), (4, 4)):
            with self.subTest(raw=raw):
                events = []
                with (
                    mock.patch.object(conftest, "LocalPlaywrightCapacity", FakeCapacity),
                    mock.patch.object(
                        conftest,
                        "_resolved_base_url",
                        return_value="http://127.0.0.1:8000",
                    ),
                    mock.patch.object(conftest, "_base_url_is_local", return_value=True),
                    mock.patch.dict(os.environ),
                ):
                    os.environ.pop("PYTEST_XDIST_WORKER", None)
                    config = SimpleNamespace(option=SimpleNamespace(numprocesses=raw, collectonly=False))
                    conftest._claim_playwright_local_capacity(config)
                self.assertEqual(events, [("create", expected), ("acquire", None)])

        with (
            mock.patch.object(conftest, "_resolved_base_url", return_value="https://dev.aishippinglabs.com"),
            mock.patch.object(conftest, "_base_url_is_local", return_value=False),
        ):
            self.assertIsNone(
                conftest._claim_playwright_local_capacity(
                    SimpleNamespace(option=SimpleNamespace(numprocesses=4, collectonly=False))
                )
            )

        with mock.patch.dict(os.environ, {"PYTEST_XDIST_WORKER": "gw0"}):
            self.assertIsNone(
                conftest._claim_playwright_local_capacity(
                    SimpleNamespace(option=SimpleNamespace(numprocesses=4, collectonly=False))
                )
            )

    def test_same_worktree_guard_is_claimed_before_capacity_wait(self):
        events = []
        with (
            mock.patch.object(
                conftest, "_claim_playwright_worktree_guard", side_effect=lambda config: events.append("guard")
            ),
            mock.patch.object(
                conftest, "_claim_playwright_local_capacity", side_effect=lambda config: events.append("capacity")
            ),
        ):
            conftest._claim_local_playwright_admission(SimpleNamespace())
        self.assertEqual(events, ["guard", "capacity"])

    def test_capacity_error_releases_same_worktree_guard_and_exits_nonzero(self):
        config = SimpleNamespace(option=SimpleNamespace(numprocesses=4, collectonly=False))
        with (
            mock.patch.object(conftest, "_resolved_base_url", return_value="http://127.0.0.1:8000"),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
            mock.patch.object(
                conftest.LocalPlaywrightCapacity,
                "for_current_worktree",
                side_effect=CapacityAdmissionError("Zero tests started"),
            ),
            mock.patch.object(conftest, "_release_playwright_worktree_guard") as release,
        ):
            with self.assertRaises(pytest.exit.Exception) as raised:
                conftest._claim_playwright_local_capacity(config)
        release.assert_called_once_with(config)
        self.assertEqual(raised.exception.returncode, 2)

    def test_session_finish_releases_capacity_before_worktree_guard(self):
        events = []

        class Resource:
            def __init__(self, name):
                self.name = name

            def release(self):
                events.append(self.name)

        config = SimpleNamespace(
            _playwright_local_capacity=Resource("capacity"),
            _playwright_worktree_guard=Resource("worktree"),
        )
        conftest.pytest_sessionfinish(SimpleNamespace(config=config), 1)
        self.assertEqual(events, ["capacity", "worktree"])

    def test_xdist_worker_receives_grant_evidence_without_a_second_claim(self):
        state = {"requested_slots": 4, "capacity": 4, "granted": True, "wait_seconds": 10.0}
        capacity = SimpleNamespace(diagnostic_state=lambda: state)
        node = SimpleNamespace(
            config=SimpleNamespace(_playwright_local_capacity=capacity),
            workerinput={},
        )
        conftest.pytest_configure_node(node)
        self.assertEqual(node.workerinput["playwright_capacity"], state)
