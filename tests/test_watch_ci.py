"""Deterministic offline tests for the on-call CI watcher (`scripts/watch-ci.py`).

The watcher's filename is hyphenated (matching PocketShell), so it is loaded via
importlib rather than a normal import. Every test drives the watcher with a fake
`gh` runner and a virtual clock — no subprocess, no network, no real sleeping.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from textwrap import dedent

from django.test import SimpleTestCase, tag

MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "watch-ci.py"
_spec = importlib.util.spec_from_file_location("watch_ci", MODULE_PATH)
assert _spec and _spec.loader
watch_ci = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve the module's __dict__ during
# class processing (the hyphenated filename cannot be a normal import).
sys.modules["watch_ci"] = watch_ci
_spec.loader.exec_module(watch_ci)

REQUIRED = watch_ci.DEPLOY_DEV_REQUIRED_CHECKS


def job(name, *, status="completed", conclusion="success", url=""):
    return {"name": name, "status": status, "conclusion": conclusion, "url": url}


def run_payload(
    *,
    status,
    conclusion,
    jobs,
    run_id="100",
    workflow="Deploy Dev",
    branch="main",
    created_at="2026-07-25T10:00:00Z",
):
    return {
        "databaseId": run_id,
        "status": status,
        "conclusion": conclusion,
        "workflowName": workflow,
        "headBranch": branch,
        "createdAt": created_at,
        "jobs": jobs,
    }


def all_seven_success():
    return [job(name) for name in REQUIRED]


class FakeGh:
    """Injectable `gh` runner backed by scripted JSON / log responses."""

    def __init__(self, *, views=None, view_provider=None, run_list=None, log_failed=""):
        self._views = list(views or [])
        self._view_provider = view_provider
        self._view_index = 0
        self._run_list = run_list if run_list is not None else []
        self._log_failed = log_failed
        self.calls: list[list[str]] = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if "run" in args and "list" in args:
            return json.dumps(self._run_list)
        if "run" in args and "view" in args and "--log-failed" in args:
            return self._log_failed
        if "run" in args and "view" in args:
            return json.dumps(self._next_view())
        raise AssertionError(f"unexpected gh args: {args}")

    def _next_view(self):
        if self._view_provider is not None:
            payload = self._view_provider(self._view_index)
            self._view_index += 1
            return payload
        if not self._views:
            raise AssertionError("no views configured")
        payload = self._views[min(self._view_index, len(self._views) - 1)]
        self._view_index += 1
        return payload


class AlwaysFailingGh:
    def __init__(self):
        self.calls = 0

    def __call__(self, args):
        self.calls += 1
        raise watch_ci.GhError("simulated gh failure")


def make_watcher(runner, **kwargs):
    defaults = dict(
        runner=runner,
        clock=watch_ci.VirtualClock(),
        workflow="Deploy Dev",
        branch="main",
        interval=15.0,
        no_progress_timeout=900.0,
        max_wall_clock=5400.0,
        gh_retry_budget=5,
    )
    defaults.update(kwargs)
    return watch_ci.CIWatcher(**defaults)


@tag("core")
class WatcherExitCodeTest(SimpleTestCase):
    def test_green_after_all_seven_required_jobs_succeed(self):
        gh = FakeGh(views=[run_payload(status="completed", conclusion="success", jobs=all_seven_success())])
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.GREEN)
        self.assertEqual(verdict.exit_code, 0)
        self.assertEqual(verdict.required, list(REQUIRED))
        self.assertEqual(verdict.failing_jobs, [])
        # Verdict is reached without any second GitHub query by the caller: the
        # final JSON already carries every decision field.
        self.assertNotIn(["run", "view", "100", "--log-failed"], gh.calls)

    def test_failed_shard_names_job_and_captures_evidence_not_green(self):
        jobs = [
            job(REQUIRED[0]),
            job(REQUIRED[1]),
            job(REQUIRED[2]),
            job(REQUIRED[3], conclusion="failure"),  # shard 3/4 fails
            job(REQUIRED[4], status="completed", conclusion="cancelled"),  # shard 4/4 cancelled
            job(REQUIRED[5], status="completed", conclusion="cancelled"),  # Playwright cancelled
            job(REQUIRED[6], status="completed", conclusion="skipped"),  # deploy skipped
        ]
        log = dedent(
            """
            Unit & Integration Tests (shard 3/4)\tRun tests\t2026-07-25T10:05:00Z E   AssertionError: 3 != 4
            Unit & Integration Tests (shard 3/4)\tRun tests\t2026-07-25T10:05:00Z FAILED accounts/tests/test_x.py::test_y
            """
        )
        gh = FakeGh(
            views=[run_payload(status="completed", conclusion="failure", jobs=jobs)],
            log_failed=log,
        )
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.FAILED)
        self.assertEqual(verdict.exit_code, 1)
        self.assertEqual(verdict.failing_jobs, ["Unit & Integration Tests (shard 3/4)"])
        self.assertIsNotNone(verdict.signature)
        self.assertIn("AssertionError: 3 != 4", verdict.signature)
        self.assertFalse(verdict.likely_infra)

    def test_missing_matrix_job_is_never_green_and_names_missing_job(self):
        # Deploy Gates, three shards, Playwright, and deploy pass; shard 4/4 absent.
        present = [REQUIRED[0], REQUIRED[1], REQUIRED[2], REQUIRED[4], REQUIRED[5], REQUIRED[6]]
        jobs = [job(name) for name in present]
        gh = FakeGh(views=[run_payload(status="completed", conclusion="success", jobs=jobs)])
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertNotEqual(verdict.result, watch_ci.GREEN)
        self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
        self.assertEqual(verdict.exit_code, 5)
        self.assertIn("shard 3/4", verdict.reason)

    def test_no_progress_deadline_returns_hang(self):
        stalled = run_payload(
            status="in_progress",
            conclusion="",
            jobs=[job(name, status="queued", conclusion="") for name in REQUIRED],
        )
        gh = FakeGh(view_provider=lambda i: stalled)
        watcher = make_watcher(gh, interval=15.0, no_progress_timeout=60.0, max_wall_clock=100000.0)
        verdict = watcher.watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.HANG)
        self.assertEqual(verdict.exit_code, 2)
        self.assertIn("progress", verdict.reason)

    def test_max_wall_clock_deadline_returns_hang_when_state_keeps_changing(self):
        def provider(i):
            # Fingerprint changes every poll (progress), so only the wall clock
            # can release the watcher.
            flip = "in_progress" if i % 2 == 0 else "queued"
            jobs = [job(REQUIRED[0], status=flip, conclusion="")]
            jobs += [job(name, status="queued", conclusion="") for name in REQUIRED[1:]]
            return run_payload(status="in_progress", conclusion="", jobs=jobs)

        gh = FakeGh(view_provider=provider)
        watcher = make_watcher(gh, interval=15.0, no_progress_timeout=100000.0, max_wall_clock=100.0)
        verdict = watcher.watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.HANG)
        self.assertEqual(verdict.exit_code, 2)
        self.assertIn("wall-clock", verdict.reason)

    def test_gh_failures_beyond_budget_return_unresolved(self):
        gh = AlwaysFailingGh()
        watcher = make_watcher(
            gh,
            gh_retry_budget=2,
            no_progress_timeout=100000.0,
            max_wall_clock=100000.0,
        )
        verdict = watcher.watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.UNRESOLVED)
        self.assertEqual(verdict.exit_code, 3)
        self.assertNotIn(verdict.result, {watch_ci.GREEN, watch_ci.FAILED})

    def test_unresolvable_branch_returns_unresolved(self):
        gh = FakeGh(run_list=[])
        verdict = make_watcher(gh, branch="main").watch(run_id=None)

        self.assertEqual(verdict.result, watch_ci.UNRESOLVED)
        self.assertEqual(verdict.exit_code, 3)

    def test_cancelled_run_with_newer_run_is_superseded(self):
        cancelled = run_payload(
            status="completed",
            conclusion="cancelled",
            run_id="100",
            created_at="2026-07-25T10:00:00Z",
            jobs=[job(REQUIRED[0]), job(REQUIRED[1], status="completed", conclusion="cancelled")],
        )
        newer_list = [
            {"databaseId": "200", "createdAt": "2026-07-25T11:00:00Z", "status": "in_progress"},
            {"databaseId": "100", "createdAt": "2026-07-25T10:00:00Z", "status": "completed"},
        ]
        gh = FakeGh(views=[cancelled], run_list=newer_list)
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.SUPERSEDED)
        self.assertEqual(verdict.exit_code, 4)
        self.assertEqual(verdict.newer_run_id, "200")
        self.assertIn("200", verdict.reason)

    def test_cancelled_run_without_newer_run_is_no_verdict(self):
        cancelled = run_payload(
            status="completed",
            conclusion="cancelled",
            run_id="100",
            jobs=[job(REQUIRED[0], status="completed", conclusion="cancelled")],
        )
        # Only the current run exists — nothing newer.
        gh = FakeGh(
            views=[cancelled],
            run_list=[{"databaseId": "100", "createdAt": "2026-07-25T10:00:00Z", "status": "completed"}],
        )
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.NO_VERDICT)
        self.assertEqual(verdict.exit_code, 5)
        self.assertIsNone(verdict.signature)  # no fabricated failure evidence
        self.assertIn("no verdict", verdict.reason.lower())

    def test_rerun_reset_counts_as_progress_and_reaches_green(self):
        running = run_payload(
            status="in_progress",
            conclusion="",
            jobs=[job(REQUIRED[0]), job(REQUIRED[1], status="in_progress", conclusion="")]
            + [job(name, status="queued", conclusion="") for name in REQUIRED[2:]],
        )
        reset = run_payload(
            status="in_progress",
            conclusion="",
            jobs=[job(name, status="queued", conclusion="") for name in REQUIRED],
        )
        done = run_payload(status="completed", conclusion="success", jobs=all_seven_success())
        gh = FakeGh(views=[running, reset, done])
        watcher = make_watcher(gh, interval=15.0, no_progress_timeout=1000.0)
        verdict = watcher.watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.GREEN)
        self.assertEqual(verdict.exit_code, 0)
        self.assertGreaterEqual(verdict.polls, 3)

    def test_genuine_required_failure_wins_over_later_cancellation(self):
        jobs = [
            job(REQUIRED[0]),
            job(REQUIRED[1], conclusion="failure"),  # shard 1/4 genuinely failed
            job(REQUIRED[2], status="completed", conclusion="cancelled"),
            job(REQUIRED[3], status="completed", conclusion="cancelled"),
            job(REQUIRED[4], status="completed", conclusion="cancelled"),
            job(REQUIRED[5], status="completed", conclusion="cancelled"),
            job(REQUIRED[6], status="completed", conclusion="skipped"),
        ]
        # Run itself was cancelled, but the failed required job must win.
        gh = FakeGh(
            views=[run_payload(status="completed", conclusion="cancelled", jobs=jobs)],
            log_failed="",
        )
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.FAILED)
        self.assertEqual(verdict.failing_jobs, ["Unit & Integration Tests (shard 1/4)"])
        self.assertIsNone(verdict.newer_run_id)


@tag("core")
class SkippedGatingTest(SimpleTestCase):
    def test_skipped_required_job_is_green_when_nothing_failed(self):
        jobs = [job(name) for name in REQUIRED[:-1]]
        jobs.append(job(REQUIRED[6], status="completed", conclusion="skipped"))  # deploy skipped
        gh = FakeGh(views=[run_payload(status="completed", conclusion="success", jobs=jobs)])
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.GREEN)

    def test_skipped_required_job_gated_by_a_failure_reports_failure(self):
        jobs = [job(name) for name in REQUIRED[:-1]]
        # Non-required PostgreSQL job fails and gates the required deploy to skip.
        jobs.append(job("PostgreSQL 16 Verification", status="completed", conclusion="failure"))
        jobs.append(job(REQUIRED[6], status="completed", conclusion="skipped"))
        gh = FakeGh(
            views=[run_payload(status="completed", conclusion="failure", jobs=jobs)],
            log_failed="",
        )
        verdict = make_watcher(gh).watch(run_id="100")

        self.assertEqual(verdict.result, watch_ci.FAILED)
        self.assertIn("PostgreSQL 16 Verification", verdict.failing_jobs)


@tag("core")
class SignatureSafetyTest(SimpleTestCase):
    def test_ignores_echoed_shell_source_and_flags_captured_infra(self):
        log = dedent(
            """
            Deploy to Dev\tBuild Docker image\t2026-07-25T10:05:00Z + echo "Error: about to build"
            Deploy to Dev\tBuild Docker image\t2026-07-25T10:05:01Z echo "Error: this is source not evidence"
            Deploy to Dev\tBuild Docker image\t2026-07-25T10:05:02Z The runner has received a shutdown signal
            """
        )
        signature, likely_infra = watch_ci.extract_signature(log)
        self.assertTrue(likely_infra)
        self.assertIn("shutdown signal", signature)
        self.assertNotIn("this is source", signature)

    def test_django_assertion_is_captured_but_not_infra(self):
        log = dedent(
            """
            Unit & Integration Tests (shard 1/4)\tRun tests\t2026-07-25T10:05:00Z echo "Error: preamble source"
            Unit & Integration Tests (shard 1/4)\tRun tests\t2026-07-25T10:05:01Z Traceback (most recent call last):
            Unit & Integration Tests (shard 1/4)\tRun tests\t2026-07-25T10:05:02Z E   AssertionError: 200 != 302
            """
        )
        signature, likely_infra = watch_ci.extract_signature(log)
        self.assertFalse(likely_infra)
        self.assertIsNotNone(signature)
        self.assertIn("Traceback", signature)

    def test_echoed_only_log_produces_no_fabricated_signature(self):
        log = 'Deploy to Dev\tStep\t2026-07-25T10:05:00Z + echo "Error: nothing really failed"\n'
        signature, likely_infra = watch_ci.extract_signature(log)
        self.assertIsNone(signature)
        self.assertFalse(likely_infra)


@tag("core")
class CompactOutputTest(SimpleTestCase):
    def _green_verdict_and_run(self):
        run = watch_ci.parse_run_payload(
            run_payload(status="completed", conclusion="success", jobs=all_seven_success())
        )
        verdict = watch_ci.classify(run, list(REQUIRED))
        verdict.polls = 6
        verdict.elapsed_s = 512.0
        return verdict, run

    def test_summary_plus_json_stays_under_25_lines_with_all_seven(self):
        verdict, run = self._green_verdict_and_run()
        summary = watch_ci.render_summary(verdict, run)
        total_lines = len(summary) + 1  # + final JSON line
        self.assertLess(total_lines, 25)
        # Every required job is named in the summary.
        summary_text = "\n".join(summary)
        for name in REQUIRED:
            self.assertIn(name, summary_text)

    def test_final_json_is_single_line_and_carries_all_decision_fields(self):
        verdict, _run = self._green_verdict_and_run()
        json_line = watch_ci.render_json_line(verdict)
        self.assertNotIn("\n", json_line)
        payload = json.loads(json_line)
        for field_name in (
            "result",
            "exit_code",
            "run_id",
            "required",
            "failing_jobs",
            "signature",
            "likely_infra",
            "reason",
            "polls",
            "elapsed_s",
        ):
            self.assertIn(field_name, payload)
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["required"], list(REQUIRED))


@tag("core")
class RequiredCheckDefaultsTest(SimpleTestCase):
    def test_deploy_dev_defaults_to_the_seven_exact_website_job_names(self):
        self.assertEqual(
            list(REQUIRED),
            [
                "Deploy Gates (migrations / OpenAPI / system check / static)",
                "Unit & Integration Tests (shard 1/4)",
                "Unit & Integration Tests (shard 2/4)",
                "Unit & Integration Tests (shard 3/4)",
                "Unit & Integration Tests (shard 4/4)",
                "Playwright Core E2E",
                "Deploy to Dev",
            ],
        )

    def test_explicit_required_check_override_replaces_defaults(self):
        watcher = watch_ci.CIWatcher(
            runner=FakeGh(),
            clock=watch_ci.VirtualClock(),
            workflow="Some Other Workflow",
            required_checks=["Only This Job"],
        )
        self.assertEqual(watcher.required, ["Only This Job"])

    def test_non_deploy_dev_workflow_without_override_has_no_website_defaults(self):
        watcher = watch_ci.CIWatcher(
            runner=FakeGh(),
            clock=watch_ci.VirtualClock(),
            workflow="Scheduled Playwright",
        )
        self.assertEqual(watcher.required, [])
