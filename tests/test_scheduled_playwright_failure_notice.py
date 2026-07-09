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
        grouped, ungrouped = notice.split_failed_log_by_job(raw_log)
        self.assertEqual(ungrouped, [])

        diagnostic = notice.build_job_diagnostic(
            notice.FailedJob(
                name="Playwright Full Suite (shard 2/4)",
                url="https://github.com/AI-Shipping-Labs/website/actions/runs/1/job/2",
            ),
            grouped["Playwright Full Suite (shard 2/4)"],
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
        def command_runner(args):
            if "--json" in args:
                return json.dumps(
                    {
                        "jobs": [
                            {
                                "name": "Playwright Full Suite (shard 3/4)",
                                "conclusion": "failure",
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
        self.assertIn("- Playwright Full Suite (shard 3/4)", body)
        self.assertIn("Job: https://github.com/AI-Shipping-Labs/website/actions/runs/123/job/3", body)
        self.assertIn("Failing tests: could not extract pytest node IDs from failed logs.", body)
        self.assertIn("Diagnostics note: Failed logs were not available when this notification ran.", body)
        self.assertIn("Failure context: unavailable.", body)

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
        self.assertIn('uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" "${files[@]}" -v', workflow_text)
        self.assertIn('uv run pytest -m "manual_visual or slow_platform" playwright_tests/ -v', workflow_text)
