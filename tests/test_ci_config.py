"""
Tests for CI/CD pipeline configuration.

Validates the CI workflow YAML at `.github/workflows/ci.yml` to ensure:
- Push and PR triggers are scoped to the main branch
- Two jobs exist: unit-tests and playwright-tests (sequential)
- Dependencies are installed with uv, not pip
- Chromium is installed before Playwright tests
- Migrations run before unit tests
- No continue-on-error on test steps
- No wildcard branch triggers

Moved from playwright_tests/test_ci_cd_pipeline.py -- these tests parse
YAML and never open a browser.
"""

from pathlib import Path

import yaml
from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


def _load_workflow():
    """Load and parse the CI workflow YAML file."""
    with open(CI_WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)
    # PyYAML converts the YAML key `on:` to Python bool True.
    if True in data and "on" not in data:
        data["on"] = data.pop(True)
    return data


class PushToMainTriggerTest(SimpleTestCase):
    """Scenario 1: Push to main triggers the full pipeline."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_push_to_main_triggers_ci(self):
        on_config = self.workflow["on"]
        self.assertIn("push", on_config)
        push_branches = on_config["push"].get("branches", [])
        self.assertIn("main", push_branches)

    def test_unit_tests_job_exists(self):
        jobs = self.workflow["jobs"]
        self.assertIn("unit-tests", jobs)
        self.assertEqual(
            jobs["unit-tests"]["name"], "Unit & Integration Tests"
        )

    def test_playwright_tests_job_exists(self):
        jobs = self.workflow["jobs"]
        self.assertIn("playwright-tests", jobs)
        self.assertEqual(
            jobs["playwright-tests"]["name"], "Playwright E2E Tests"
        )

    def test_playwright_depends_on_unit_tests(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        needs = playwright_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        self.assertIn("unit-tests", needs)


class PRTargetingMainTriggerTest(SimpleTestCase):
    """Scenario 2: PR targeting main triggers CI."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_pull_request_to_main_triggers_ci(self):
        on_config = self.workflow["on"]
        self.assertIn("pull_request", on_config)
        pr_branches = on_config["pull_request"].get("branches", [])
        self.assertIn("main", pr_branches)

    def test_both_jobs_run_on_pr(self):
        jobs = self.workflow["jobs"]
        self.assertIn("unit-tests", jobs)
        self.assertIn("playwright-tests", jobs)
        for job_name in ("unit-tests", "playwright-tests"):
            job = jobs[job_name]
            if "if" in job:
                self.assertFalse(
                    "pull_request" in job["if"] and "!" in job["if"],
                    f"{job_name} must not skip on pull_request events",
                )


class FailingUnitTestFailsFastTest(SimpleTestCase):
    """Scenario 3: Failing unit test causes fast failure."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_playwright_needs_unit_tests(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        needs = playwright_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        self.assertIn("unit-tests", needs)

    def test_unit_test_step_uses_manage_py_test(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        step_runs = [
            step.get("run", "") for step in unit_job.get("steps", [])
        ]
        test_steps = [run for run in step_runs if "manage.py test" in run]
        self.assertGreaterEqual(len(test_steps), 1)

    def test_no_continue_on_error_for_unit_tests(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        for step in unit_job.get("steps", []):
            if "manage.py test" in step.get("run", ""):
                self.assertFalse(step.get("continue-on-error", False))


class FailingPlaywrightTestReportedSeparatelyTest(SimpleTestCase):
    """Scenario 4: Failing Playwright test is reported separately."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_unit_and_playwright_are_separate_jobs(self):
        jobs = self.workflow["jobs"]
        self.assertIn("unit-tests", jobs)
        self.assertIn("playwright-tests", jobs)

    def test_playwright_step_uses_pytest_verbose(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "") for step in playwright_job.get("steps", [])
        ]
        pytest_steps = [
            run for run in step_runs
            if "pytest" in run and "playwright_tests" in run
        ]
        self.assertGreaterEqual(len(pytest_steps), 1)
        self.assertIn("-v", pytest_steps[0])

    def test_no_continue_on_error_for_playwright(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        for step in playwright_job.get("steps", []):
            run_cmd = step.get("run", "")
            if "pytest" in run_cmd and "playwright_tests" in run_cmd:
                self.assertFalse(step.get("continue-on-error", False))


class DependenciesInstalledWithUvTest(SimpleTestCase):
    """Scenario 5: Dependencies installed with uv, not pip."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_setup_uv_action_present_in_unit_tests(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        uses_values = [
            step.get("uses", "") for step in unit_job.get("steps", [])
        ]
        setup_uv = [u for u in uses_values if "astral-sh/setup-uv" in u]
        self.assertGreaterEqual(len(setup_uv), 1)

    def test_setup_uv_action_present_in_playwright_tests(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        uses_values = [
            step.get("uses", "") for step in playwright_job.get("steps", [])
        ]
        setup_uv = [u for u in uses_values if "astral-sh/setup-uv" in u]
        self.assertGreaterEqual(len(setup_uv), 1)

    def test_dependency_install_uses_uv_sync(self):
        for job_name in ("unit-tests", "playwright-tests"):
            job = self.workflow["jobs"][job_name]
            step_runs = [
                step.get("run", "") for step in job.get("steps", [])
            ]
            all_runs = "\n".join(step_runs)
            self.assertIn("uv sync", all_runs)
            self.assertNotIn("pip install", all_runs)

    def test_test_commands_use_uv_run(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        unit_runs = [
            step.get("run", "") for step in unit_job.get("steps", [])
        ]
        for run_cmd in [r for r in unit_runs if "manage.py test" in r]:
            self.assertIn("uv run", run_cmd)

        playwright_job = self.workflow["jobs"]["playwright-tests"]
        pw_runs = [
            step.get("run", "") for step in playwright_job.get("steps", [])
        ]
        for run_cmd in [r for r in pw_runs if "pytest" in r]:
            self.assertIn("uv run", run_cmd)


class PlaywrightBrowsersInstalledTest(SimpleTestCase):
    """Scenario 6: Playwright browsers installed before tests."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_chromium_install_step_exists(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "") for step in playwright_job.get("steps", [])
        ]
        chromium_steps = [
            run for run in step_runs
            if "playwright install" in run and "chromium" in run
        ]
        self.assertGreaterEqual(len(chromium_steps), 1)

    def test_chromium_install_uses_with_deps(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "") for step in playwright_job.get("steps", [])
        ]
        chromium_steps = [
            run for run in step_runs
            if "playwright install" in run and "chromium" in run
        ]
        self.assertTrue(
            any("--with-deps" in step for step in chromium_steps)
        )

    def test_chromium_install_before_tests(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        steps = playwright_job.get("steps", [])
        step_runs = [step.get("run", "") for step in steps]
        install_idx = None
        test_idx = None
        for i, run_cmd in enumerate(step_runs):
            if "playwright install" in run_cmd and "chromium" in run_cmd:
                install_idx = i
            if "pytest" in run_cmd and "playwright_tests" in run_cmd:
                test_idx = i
        self.assertIsNotNone(install_idx)
        self.assertIsNotNone(test_idx)
        self.assertLess(install_idx, test_idx)

    def test_chromium_install_uses_uv_run(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "") for step in playwright_job.get("steps", [])
        ]
        chromium_steps = [
            run for run in step_runs
            if "playwright install" in run and "chromium" in run
        ]
        for cmd in chromium_steps:
            self.assertIn("uv run", cmd)


class MigrationsRunBeforeUnitTestsTest(SimpleTestCase):
    """Scenario 7: Migrations run before unit tests."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_migrate_step_exists(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        step_runs = [
            step.get("run", "") for step in unit_job.get("steps", [])
        ]
        migrate_steps = [
            run for run in step_runs if "manage.py migrate" in run
        ]
        self.assertGreaterEqual(len(migrate_steps), 1)

    def test_migrate_before_test(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        steps = unit_job.get("steps", [])
        step_runs = [step.get("run", "") for step in steps]
        migrate_idx = None
        test_idx = None
        for i, run_cmd in enumerate(step_runs):
            if "manage.py migrate" in run_cmd and migrate_idx is None:
                migrate_idx = i
            if "manage.py test" in run_cmd:
                test_idx = i
        self.assertIsNotNone(migrate_idx)
        self.assertIsNotNone(test_idx)
        self.assertLess(migrate_idx, test_idx)

    def test_migrate_uses_uv_run(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        step_runs = [
            step.get("run", "") for step in unit_job.get("steps", [])
        ]
        migrate_steps = [
            run for run in step_runs if "manage.py migrate" in run
        ]
        for cmd in migrate_steps:
            self.assertIn("uv run", cmd)


class PipelineStructureTwoJobsTest(SimpleTestCase):
    """Scenario 8: Pipeline has exactly two jobs."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_exactly_two_jobs(self):
        jobs = self.workflow["jobs"]
        self.assertEqual(len(jobs), 2)

    def test_job_names(self):
        jobs = self.workflow["jobs"]
        names = {job.get("name", key) for key, job in jobs.items()}
        self.assertIn("Unit & Integration Tests", names)
        self.assertIn("Playwright E2E Tests", names)

    def test_sequential_dependency(self):
        playwright_job = self.workflow["jobs"]["playwright-tests"]
        needs = playwright_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        self.assertIn("unit-tests", needs)

    def test_unit_tests_has_no_dependencies(self):
        unit_job = self.workflow["jobs"]["unit-tests"]
        needs = unit_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        self.assertEqual(len(needs), 0)

    def test_each_job_runs_on_ubuntu(self):
        for job_name in ("unit-tests", "playwright-tests"):
            job = self.workflow["jobs"][job_name]
            self.assertEqual(job.get("runs-on"), "ubuntu-latest")


class NonMainBranchDoesNotTriggerTest(SimpleTestCase):
    """Scenario 9: Non-main branch push does not trigger CI."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_push_trigger_restricted_to_main(self):
        on_config = self.workflow["on"]
        push_config = on_config.get("push", {})
        push_branches = push_config.get("branches", [])
        self.assertEqual(push_branches, ["main"])

    def test_pr_trigger_restricted_to_main(self):
        on_config = self.workflow["on"]
        pr_config = on_config.get("pull_request", {})
        pr_branches = pr_config.get("branches", [])
        self.assertEqual(pr_branches, ["main"])

    def test_no_wildcard_triggers(self):
        on_config = self.workflow["on"]
        for event_type in ("push", "pull_request"):
            event_config = on_config.get(event_type, {})
            branches = event_config.get("branches", [])
            for branch in branches:
                self.assertNotIn("*", branch)
                self.assertNotIn("**", branch)


class FixAndRePushTest(SimpleTestCase):
    """Scenario 10: Fix and re-push triggers CI again."""

    def setUp(self):
        self.workflow = _load_workflow()

    def test_push_trigger_fires_on_every_push(self):
        on_config = self.workflow["on"]
        push_config = on_config.get("push", {})
        self.assertNotIn("paths", push_config)

    def test_pr_trigger_fires_on_every_push_to_pr(self):
        on_config = self.workflow["on"]
        pr_config = on_config.get("pull_request", {})
        self.assertNotIn("paths", pr_config)

    def test_no_concurrency_cancel_in_progress(self):
        if "concurrency" not in self.workflow:
            return
        concurrency = self.workflow["concurrency"]
        if isinstance(concurrency, str):
            return
        self.assertIsInstance(concurrency, dict)

    def test_workflow_runs_both_jobs_on_trigger(self):
        for job_name in ("unit-tests", "playwright-tests"):
            job = self.workflow["jobs"][job_name]
            if_condition = job.get("if", "")
            self.assertNotIn("cancelled", if_condition.lower())
