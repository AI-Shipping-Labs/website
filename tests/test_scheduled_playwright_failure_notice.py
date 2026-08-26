from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import yaml
from django.test import SimpleTestCase, tag

from scripts import scheduled_playwright_failure_notice as notice

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEDULED_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "scheduled-playwright.yml"


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


@tag("core")
class ScheduledPlaywrightFailureNoticeTest(SimpleTestCase):
    def test_extracts_deduplicated_node_ids_and_bounded_assertion_context(self):
        raw_log = dedent(
            """
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:02Z =================================== FAILURES ===================================
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:02Z >       assert page.get_by_text("Studio Event Renamed").first.is_visible()
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:02Z E       assert False
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:02Z playwright_tests/test_studio_event_create.py:375: AssertionError
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:03Z =========================== short test summary info ============================
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:03Z FAILED playwright_tests/test_studio_event_create.py::TestScenario5OriginEditGate::test_studio_editable_github_readonly - assert False
            Playwright Full Suite (shard 2/4)\tRun full Playwright shard\t2026-07-08T19:58:03Z FAILED playwright_tests/test_studio_event_create.py::TestScenario5OriginEditGate::test_studio_editable_github_readonly - assert False
            """
        )
        lines = notice.extract_log_lines(raw_log)

        diagnostic = notice.build_job_diagnostic(
            notice.FailedJob(
                name="Playwright Full Suite (shard 2/4)",
                url="https://github.com/AI-Shipping-Labs/website/actions/runs/1/job/2",
                job_id="2",
            ),
            lines,
        )

        self.assertEqual(
            diagnostic.node_ids,
            [
                "playwright_tests/test_studio_event_create.py::"
                "TestScenario5OriginEditGate::test_studio_editable_github_readonly"
            ],
        )
        context = "\n".join(diagnostic.context_lines)
        self.assertIn('assert page.get_by_text("Studio Event Renamed").first.is_visible()', context)
        self.assertIn("FAILED playwright_tests/test_studio_event_create.py", context)
        self.assertLessEqual(len(diagnostic.context_lines), notice.MAX_CONTEXT_LINES)
        self.assertLessEqual(len(context), notice.MAX_CONTEXT_CHARS)

    def test_format_groups_multiple_failed_jobs_and_manual_excluded_suite(self):
        body = notice.format_failure_body(
            branch="main",
            run_url="https://github.com/AI-Shipping-Labs/website/actions/runs/123",
            commit_sha="abc123",
            event_name="workflow_dispatch",
            diagnostics=[
                notice.JobDiagnostic(
                    name="Playwright Full Suite (shard 1/4)",
                    url="https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/1",
                    node_ids=["playwright_tests/test_one.py::test_a"],
                    context_lines=["FAILED playwright_tests/test_one.py::test_a - assert False"],
                ),
                notice.JobDiagnostic(
                    name="Playwright Excluded Marker Suites",
                    url="https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/2",
                    node_ids=["playwright_tests/test_manual_visual.py::test_b"],
                    context_lines=["FAILED playwright_tests/test_manual_visual.py::test_b - TimeoutError"],
                ),
            ],
        )

        self.assertIn("Scheduled Playwright failed on `main`.", body)
        self.assertIn("Run: https://github.com/AI-Shipping-Labs/website/actions/runs/123", body)
        self.assertIn("Commit: abc123", body)
        self.assertIn("Event: workflow_dispatch", body)
        self.assertIn("- Playwright Full Suite (shard 1/4)", body)
        self.assertIn("- Playwright Excluded Marker Suites", body)
        self.assertIn("Job: https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/2", body)
        self.assertIn("`playwright_tests/test_one.py::test_a`", body)
        self.assertIn("`playwright_tests/test_manual_visual.py::test_b`", body)
        self.assertIn("### Playwright Full Suite (shard 1/4)", body)
        self.assertIn("### Playwright Excluded Marker Suites", body)

    def test_collect_diagnostics_falls_back_when_failed_logs_are_unavailable(self):
        calls = []

        def command_runner(args):
            calls.append(list(args))
            if "--json" in args:
                return json.dumps(
                    {
                        "jobs": [
                            {
                                "name": "Playwright Full Suite (shard 3/4)",
                                "conclusion": "failure",
                                "databaseId": 97095974428,
                                "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/3",
                            }
                        ]
                    }
                )
            raise notice.GhCommandError("simulated log lookup failure")

        diagnostics, fallback_note = notice.collect_failed_job_diagnostics(
            "123",
            repo="AI-Shipping-Labs/website",
            command_runner=command_runner,
        )
        body = notice.format_failure_body(
            branch="main",
            run_url="https://github.com/AI-Shipping-Labs/website/actions/runs/123",
            commit_sha="abc123",
            event_name="schedule",
            diagnostics=diagnostics,
            fallback_note=fallback_note,
        )

        self.assertEqual(fallback_note, "")
        self.assertIn(
            ["api", "repos/AI-Shipping-Labs/website/actions/jobs/97095974428/logs"],
            calls,
        )
        self.assertIn("Run: https://github.com/AI-Shipping-Labs/website/actions/runs/123", body)
        self.assertIn("Commit: abc123", body)
        self.assertIn("- Playwright Full Suite (shard 3/4)", body)
        self.assertIn("Job: https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/3", body)
        self.assertIn("Failing tests: could not extract pytest node IDs from failed logs.", body)
        self.assertIn("Diagnostics note: Failed logs were not available when this notification ran.", body)
        self.assertIn("Failure context: unavailable.", body)

    def test_per_job_log_fetch_names_the_failing_pytest_node_id(self):
        """Issue #1462: the run-scoped archive is unavailable mid-run, so the
        notice fetches each failed job's own log and names the failing test."""
        node_id = (
            "playwright_tests/test_dashboard.py::TestIssue1211MobileDashboard::"
            "test_mobile_dashboard_links_have_no_overflow_and_navigate"
        )
        job_log = dedent(
            f"""
            2026-08-22T21:40:01.1234567Z > css:build
            2026-08-22T21:40:02.1234567Z > tailwindcss -c tailwind.config.js --minify
            2026-08-22T21:41:00.1234567Z playwright_tests/test_newsletter.py::TestScenario5::test_a PASSED [ 57%]
            2026-08-22T21:42:00.1234567Z playwright_tests/test_newsletter.py::TestScenario5::test_b PASSED [ 58%]
            2026-08-22T21:43:00.1234567Z playwright_tests/test_newsletter.py::TestScenario5::test_c PASSED [ 59%]
            2026-08-22T21:48:02.1234567Z =================================== FAILURES ===================================
            2026-08-22T21:48:02.1234567Z >       box = page.locator(selector).first.bounding_box()
            2026-08-22T21:48:02.1234567Z E       playwright._impl._errors.TimeoutError: Locator.bounding_box: Timeout 30000ms exceeded.
            2026-08-22T21:48:03.1234567Z =========================== short test summary info ============================
            2026-08-22T21:48:03.1234567Z FAILED {node_id} - playwright._impl._errors.TimeoutError
            2026-08-22T21:48:03.1234567Z ==== 1 failed, 591 passed, 41 deselected, 16 warnings in 902.53s (0:15:02) =====
            """
        )
        calls = []

        def command_runner(args):
            calls.append(list(args))
            if "--json" in args:
                return json.dumps(
                    {
                        "jobs": [
                            {
                                "name": "Playwright Full Suite (shard 2/4)",
                                "conclusion": "failure",
                                "databaseId": 97095974428,
                                "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/2",
                            },
                            {
                                "name": "Playwright Full Suite (shard 1/4)",
                                "conclusion": "success",
                                "databaseId": 97095974427,
                                "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/1",
                            },
                        ]
                    }
                )
            return job_log

        diagnostics, fallback_note = notice.collect_failed_job_diagnostics(
            "123",
            repo="AI-Shipping-Labs/website",
            command_runner=command_runner,
        )
        body = notice.format_failure_body(
            branch="main",
            run_url="https://github.com/AI-Shipping-Labs/website/actions/runs/123",
            commit_sha="e317c65a",
            event_name="schedule",
            diagnostics=diagnostics,
            fallback_note=fallback_note,
        )

        self.assertEqual(
            calls[1],
            ["api", "repos/AI-Shipping-Labs/website/actions/jobs/97095974428/logs"],
        )
        self.assertEqual(len(calls), 2, "Only the failed job's log should be fetched.")
        self.assertNotIn("--log-failed", [flag for call in calls for flag in call])
        self.assertIn("### Playwright Full Suite (shard 2/4)", body)
        self.assertIn(f"- `{node_id}`", body)
        self.assertNotIn("could not extract pytest node IDs from failed logs", body)
        self.assertIn("Locator.bounding_box: Timeout 30000ms exceeded.", body)
        # Setup-step echoes from before the FAILURES banner are not failure
        # detail and must not crowd out the bounded context.
        self.assertNotIn("tailwindcss -c tailwind.config.js", body)

    def test_job_scoped_log_lines_without_job_step_prefix_are_kept(self):
        """Job-scoped logs have no ``job\\tstep\\t`` prefix, and pytest output
        can itself contain tabs — neither shape may be dropped."""
        lines = notice.extract_log_lines(
            "2026-08-22T21:48:03.1234567Z FAILED playwright_tests/test_dashboard.py::TestA::test_b - assert False\n"
            "2026-08-22T21:48:03.1234567Z E\tassert\tFalse\n"
        )

        self.assertEqual(
            lines,
            [
                "FAILED playwright_tests/test_dashboard.py::TestA::test_b - assert False",
                "E\tassert\tFalse",
            ],
        )
        self.assertEqual(
            notice.extract_pytest_node_ids(lines),
            ["playwright_tests/test_dashboard.py::TestA::test_b"],
        )

    def test_job_scoped_context_redacts_secrets_and_respects_caps(self):
        job_log = "\n".join(
            [
                "2026-08-22T21:48:01.1234567Z E   AssertionError: dashboard link missing",
                "2026-08-22T21:48:02.1234567Z GH_TOKEN: ghp_supersecretvalue",
            ]
            + [
                f"2026-08-22T21:48:{index:02d}.1234567Z E   AssertionError: {'filler ' * 40}{index}"
                for index in range(3, 40)
            ]
            + [
                "2026-08-22T21:48:59.1234567Z FAILED playwright_tests/test_dashboard.py::TestA::test_b - assert False",
            ]
        )

        diagnostic = notice.build_job_diagnostic(
            notice.FailedJob(name="Playwright Full Suite (shard 2/4)", job_id="7"),
            notice.extract_log_lines(job_log),
        )
        context = "\n".join(diagnostic.context_lines)

        self.assertIn("[redacted sensitive log line]", context)
        self.assertNotIn("ghp_supersecretvalue", context)
        self.assertLessEqual(len(diagnostic.context_lines), notice.MAX_CONTEXT_LINES)
        self.assertLessEqual(len(context), notice.MAX_CONTEXT_CHARS)

    def test_formats_existing_failed_job_summary_when_job_lookup_fails(self):
        body = notice.format_failure_body(
            branch="main",
            run_url="https://github.com/AI-Shipping-Labs/website/actions/runs/123",
            commit_sha="abc123",
            event_name="schedule",
            diagnostics=[],
            fallback_note="Failure details were not available when this notification ran.",
        )

        self.assertIn("Run: https://github.com/AI-Shipping-Labs/website/actions/runs/123", body)
        self.assertIn("Commit: abc123", body)
        self.assertIn("Event: schedule", body)
        self.assertIn("- Failure details were not available when this notification ran.", body)

    def test_context_redacts_sensitive_and_environment_lines(self):
        context = notice.sanitize_context_lines(
            [
                "env:",
                "  GH_TOKEN: not-for-issues",
                "  AWS_SECRET_ACCESS_KEY: not-for-issues",
                "FAILED playwright_tests/test_example.py::test_failure - assert False",
            ]
        )

        self.assertIn("[omitted environment block]", context)
        self.assertIn("FAILED playwright_tests/test_example.py::test_failure - assert False", context)
        self.assertNotIn("not-for-issues", "\n".join(context))


@tag("core")
class ScheduledPlaywrightWorkflowNotificationTest(SimpleTestCase):
    def test_notify_job_uses_helper_with_fallback_and_unchanged_gate(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        notify_job = workflow["jobs"]["notify"]

        self.assertEqual(notify_job["needs"], ["changes", "playwright-full", "playwright-excluded-markers"])
        self.assertEqual(notify_job["if"], "always() && needs.changes.outputs.skip != 'true'")
        self.assertEqual(notify_job["permissions"]["actions"], "read")
        self.assertEqual(notify_job["permissions"]["contents"], "read")
        self.assertEqual(notify_job["permissions"]["issues"], "write")

        checkout_step = next(step for step in notify_job["steps"] if step.get("name") == "Checkout code for notification helper")
        self.assertEqual(checkout_step["uses"], "actions/checkout@v5")
        self.assertTrue(checkout_step["continue-on-error"])

        notify_step = next(step for step in notify_job["steps"] if step.get("name") == "Open or update failure issue")
        self.assertIn("scripts/scheduled_playwright_failure_notice.py --output", notify_step["run"])
        self.assertIn("Failure details were not available when this notification ran.", notify_step["run"])
        self.assertIn("gh issue comment", notify_step["run"])
        self.assertIn("gh issue create", notify_step["run"])

    def test_recovery_comment_names_the_recovering_commit(self):
        """Issue #1462: a green run on a later commit is not evidence that the
        commit which failed is fixed, so the close comment must name the SHA."""
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        notify_job = workflow["jobs"]["notify"]

        # Change-gate no-op runs must never close a failure issue.
        self.assertEqual(notify_job["if"], "always() && needs.changes.outputs.skip != 'true'")

        close_step = next(
            step for step in notify_job["steps"] if step.get("name") == "Close recovered failure issue"
        )
        self.assertEqual(close_step["if"], "${{ !contains(toJSON(needs.*.result), 'failure') }}")
        self.assertIn("Commit: %s", close_step["run"])
        self.assertIn("${GITHUB_SHA}", close_step["run"])
        self.assertIn("gh issue close", close_step["run"])

    def test_scheduled_playwright_cadence_and_test_commands_remain_unchanged(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        workflow_text = SCHEDULED_WORKFLOW_PATH.read_text()

        self.assertIn("cron: '0 */3 * * *'", workflow_text)
        self.assertEqual(workflow["concurrency"]["group"], "scheduled-playwright")
        self.assertEqual(
            [item["shard_name"] for item in workflow["jobs"]["playwright-full"]["strategy"]["matrix"]["include"]],
            ["shard 1/4", "shard 2/4", "shard 3/4", "shard 4/4"],
        )
        self.assertIn("PLAYWRIGHT_DEFAULT_MARKERS: not manual_visual and not slow_platform", workflow_text)
        self.assertIn(
            'uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" '
            '"${files[@]}" -v --durations=25',
            workflow_text,
        )
        self.assertIn(
            'uv run pytest -m "manual_visual or slow_platform" '
            'playwright_tests/ -v --durations=25',
            workflow_text,
        )
