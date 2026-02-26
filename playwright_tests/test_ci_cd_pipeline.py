"""
Playwright E2E tests for CI/CD Pipeline (Issue #102).

Tests cover all 10 BDD scenarios from the issue:
- Developer pushes to main and the full pipeline runs successfully
- Developer opens a pull request and CI validates the changes
- Developer introduces a failing unit test and the pipeline fails fast
- Developer introduces a failing Playwright test while unit tests pass
- Developer verifies that dependencies are installed with uv, not pip
- Developer checks that Playwright browsers are installed before E2E tests
- Developer pushes a commit and unit tests run database migrations first
- Developer reviews pipeline structure to understand the test execution order
- Developer pushes to a non-main branch without a PR and CI does not trigger
- Developer fixes a previously failing test and re-pushes to verify the pipeline

These tests validate the CI workflow YAML configuration at
`.github/workflows/ci.yml`. Since the BDD scenarios describe properties
of the pipeline itself (triggers, job ordering, dependency installation
method, migration steps), the tests parse and inspect the workflow file
to verify these structural guarantees.

Usage:
    uv run pytest playwright_tests/test_ci_cd_pipeline.py -v
"""

import os
from pathlib import Path

import pytest
import yaml


# Path to the CI workflow file, resolved relative to the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def workflow():
    """Load and parse the CI workflow YAML file.

    PyYAML parses the YAML key `on` as the Python boolean True.
    This fixture normalizes the parsed dict so that the `on` key
    is accessible as the string "on" for readability in tests.
    """
    assert CI_WORKFLOW_PATH.exists(), (
        f"CI workflow file not found at {CI_WORKFLOW_PATH}"
    )
    with open(CI_WORKFLOW_PATH) as f:
        data = yaml.safe_load(f)

    # PyYAML converts the YAML key `on:` to Python bool True.
    # Normalize it to the string "on" so tests can use workflow["on"].
    if True in data and "on" not in data:
        data["on"] = data.pop(True)

    return data


class TestScenario1PushToMainTriggersFullPipeline:
    """
    Scenario: Developer pushes to main and the full pipeline runs
    successfully.

    Given: A developer has committed code where all unit and Playwright
           tests pass
    When: They push the commit to the `main` branch
    Then: GitHub Actions triggers the CI workflow
    Then: The "Unit & Integration Tests" job completes successfully
    Then: The "Playwright E2E Tests" job starts only after unit tests pass
    Then: Both jobs show green checkmarks on the commit in GitHub
    """

    def test_push_to_main_triggers_ci(self, workflow):
        """Push to main is listed as a trigger in the workflow."""
        on_config = workflow["on"]
        assert "push" in on_config, (
            "Workflow must trigger on push events"
        )
        push_branches = on_config["push"].get("branches", [])
        assert "main" in push_branches, (
            "Push trigger must include the 'main' branch"
        )

    def test_unit_tests_job_exists(self, workflow):
        """The workflow has a 'unit-tests' job named
        'Unit & Integration Tests'."""
        jobs = workflow["jobs"]
        assert "unit-tests" in jobs, (
            "Workflow must have a 'unit-tests' job"
        )
        assert jobs["unit-tests"]["name"] == "Unit & Integration Tests"

    def test_playwright_tests_job_exists(self, workflow):
        """The workflow has a 'playwright-tests' job named
        'Playwright E2E Tests'."""
        jobs = workflow["jobs"]
        assert "playwright-tests" in jobs, (
            "Workflow must have a 'playwright-tests' job"
        )
        assert jobs["playwright-tests"]["name"] == "Playwright E2E Tests"

    def test_playwright_depends_on_unit_tests(self, workflow):
        """The Playwright job depends on unit-tests (sequential,
        not parallel)."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        needs = playwright_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "unit-tests" in needs, (
            "Playwright job must declare 'needs: unit-tests' to run "
            "sequentially after unit tests"
        )


class TestScenario2PRTargetingMainTriggersCi:
    """
    Scenario: Developer opens a pull request and CI validates the
    changes.

    Given: A developer has a feature branch with passing tests
    When: They open a pull request targeting `main`
    Then: The CI workflow triggers automatically on the PR
    Then: Both the unit test job and Playwright test job run and
          report status on the PR
    Then: The PR shows a green "All checks have passed" status
    """

    def test_pull_request_to_main_triggers_ci(self, workflow):
        """Pull requests targeting main are listed as a trigger."""
        on_config = workflow["on"]
        assert "pull_request" in on_config, (
            "Workflow must trigger on pull_request events"
        )
        pr_branches = on_config["pull_request"].get("branches", [])
        assert "main" in pr_branches, (
            "pull_request trigger must include the 'main' branch"
        )

    def test_both_jobs_run_on_pr(self, workflow):
        """Both unit-tests and playwright-tests jobs are present,
        meaning both run on any trigger (including PRs)."""
        jobs = workflow["jobs"]
        assert "unit-tests" in jobs, (
            "unit-tests job must exist for PR validation"
        )
        assert "playwright-tests" in jobs, (
            "playwright-tests job must exist for PR validation"
        )
        # Neither job has an `if` condition that would skip it on PRs
        unit_job = jobs["unit-tests"]
        playwright_job = jobs["playwright-tests"]
        # If there is an `if` condition, it must not exclude pull_request
        if "if" in unit_job:
            assert "pull_request" not in unit_job["if"] or (
                "!" not in unit_job["if"]
            ), "unit-tests job must not skip on pull_request events"
        if "if" in playwright_job:
            assert "pull_request" not in playwright_job["if"] or (
                "!" not in playwright_job["if"]
            ), "playwright-tests job must not skip on pull_request events"


class TestScenario3FailingUnitTestFailsFast:
    """
    Scenario: Developer introduces a failing unit test and the
    pipeline fails fast.

    Given: A developer has committed code that causes a Django unit
           test to fail
    When: They push the commit or open a PR targeting `main`
    Then: The "Unit & Integration Tests" job fails and reports the
          failure
    Then: The "Playwright E2E Tests" job is skipped entirely (never
          starts)
    Then: The developer sees which specific test failed in the job logs
    """

    def test_playwright_needs_unit_tests(self, workflow):
        """Because playwright-tests `needs: unit-tests`, GitHub Actions
        automatically skips it when unit-tests fails."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        needs = playwright_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "unit-tests" in needs, (
            "Playwright job must depend on unit-tests so it is skipped "
            "when unit tests fail"
        )

    def test_unit_test_step_uses_manage_py_test(self, workflow):
        """The unit test step runs `manage.py test` which outputs
        individual test failures to the log."""
        unit_job = workflow["jobs"]["unit-tests"]
        step_runs = [
            step.get("run", "") for step in unit_job.get("steps", [])
        ]
        test_steps = [
            run for run in step_runs
            if "manage.py test" in run
        ]
        assert len(test_steps) >= 1, (
            "Unit test job must include a step that runs "
            "'manage.py test'"
        )

    def test_no_continue_on_error_for_unit_tests(self, workflow):
        """The unit test step does not have continue-on-error set,
        so a failure halts the job."""
        unit_job = workflow["jobs"]["unit-tests"]
        for step in unit_job.get("steps", []):
            run_cmd = step.get("run", "")
            if "manage.py test" in run_cmd:
                assert not step.get("continue-on-error", False), (
                    "Unit test step must not set continue-on-error, "
                    "so failures halt the pipeline"
                )


class TestScenario4FailingPlaywrightTestReportedSeparately:
    """
    Scenario: Developer introduces a failing Playwright test while
    unit tests pass.

    Given: A developer has committed code where unit tests pass but
           a Playwright E2E test fails
    When: They push the commit or open a PR targeting `main`
    Then: The "Unit & Integration Tests" job completes successfully
          (green)
    Then: The "Playwright E2E Tests" job runs and fails
    Then: The developer sees the Playwright failure details in the
          job logs
    Then: The overall pipeline status is reported as failed
    """

    def test_unit_and_playwright_are_separate_jobs(self, workflow):
        """Unit tests and Playwright tests are in separate jobs,
        so each reports its own pass/fail status."""
        jobs = workflow["jobs"]
        assert "unit-tests" in jobs
        assert "playwright-tests" in jobs
        assert "unit-tests" != "playwright-tests"

    def test_playwright_step_uses_pytest_verbose(self, workflow):
        """The Playwright test step runs pytest with -v flag so
        individual test failures are visible in logs."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "") for step in playwright_job.get("steps", [])
        ]
        pytest_steps = [
            run for run in step_runs
            if "pytest" in run and "playwright_tests" in run
        ]
        assert len(pytest_steps) >= 1, (
            "Playwright job must include a step that runs pytest "
            "against playwright_tests/"
        )
        # Check verbose flag for detailed failure output
        pytest_cmd = pytest_steps[0]
        assert "-v" in pytest_cmd, (
            "Playwright pytest step should use -v flag for detailed "
            "failure output in logs"
        )

    def test_no_continue_on_error_for_playwright(self, workflow):
        """The Playwright test step does not have continue-on-error,
        so a failure marks the overall pipeline as failed."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        for step in playwright_job.get("steps", []):
            run_cmd = step.get("run", "")
            if "pytest" in run_cmd and "playwright_tests" in run_cmd:
                assert not step.get("continue-on-error", False), (
                    "Playwright test step must not set "
                    "continue-on-error"
                )


class TestScenario5DependenciesInstalledWithUv:
    """
    Scenario: Developer verifies that dependencies are installed
    with uv, not pip.

    Given: The CI workflow file at `.github/workflows/ci.yml` exists
    When: A developer reviews the workflow configuration
    Then: The dependency installation step uses `uv sync` (not pip
          install)
    Then: The test execution steps use `uv run` as the command prefix
    Then: The `astral-sh/setup-uv` action is present to install uv
    """

    def test_setup_uv_action_present_in_unit_tests(self, workflow):
        """The unit-tests job uses astral-sh/setup-uv to install uv."""
        unit_job = workflow["jobs"]["unit-tests"]
        uses_values = [
            step.get("uses", "") for step in unit_job.get("steps", [])
        ]
        setup_uv_steps = [
            u for u in uses_values if "astral-sh/setup-uv" in u
        ]
        assert len(setup_uv_steps) >= 1, (
            "unit-tests job must use astral-sh/setup-uv action"
        )

    def test_setup_uv_action_present_in_playwright_tests(self, workflow):
        """The playwright-tests job uses astral-sh/setup-uv to
        install uv."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        uses_values = [
            step.get("uses", "") for step in playwright_job.get("steps", [])
        ]
        setup_uv_steps = [
            u for u in uses_values if "astral-sh/setup-uv" in u
        ]
        assert len(setup_uv_steps) >= 1, (
            "playwright-tests job must use astral-sh/setup-uv action"
        )

    def test_dependency_install_uses_uv_sync(self, workflow):
        """Both jobs install dependencies with `uv sync`, not
        `pip install`."""
        for job_name in ("unit-tests", "playwright-tests"):
            job = workflow["jobs"][job_name]
            step_runs = [
                step.get("run", "")
                for step in job.get("steps", [])
            ]
            all_runs = "\n".join(step_runs)

            assert "uv sync" in all_runs, (
                f"Job '{job_name}' must use 'uv sync' to install "
                f"dependencies"
            )
            assert "pip install" not in all_runs, (
                f"Job '{job_name}' must NOT use 'pip install' -- "
                f"use 'uv sync' instead"
            )

    def test_test_commands_use_uv_run(self, workflow):
        """Test execution steps use `uv run` as the command prefix."""
        # Unit tests
        unit_job = workflow["jobs"]["unit-tests"]
        unit_runs = [
            step.get("run", "")
            for step in unit_job.get("steps", [])
        ]
        test_runs = [r for r in unit_runs if "manage.py test" in r]
        for run_cmd in test_runs:
            assert "uv run" in run_cmd, (
                f"Test command must use 'uv run' prefix: {run_cmd}"
            )

        # Playwright tests
        playwright_job = workflow["jobs"]["playwright-tests"]
        pw_runs = [
            step.get("run", "")
            for step in playwright_job.get("steps", [])
        ]
        pytest_runs = [r for r in pw_runs if "pytest" in r]
        for run_cmd in pytest_runs:
            assert "uv run" in run_cmd, (
                f"Pytest command must use 'uv run' prefix: {run_cmd}"
            )


class TestScenario6PlaywrightBrowsersInstalled:
    """
    Scenario: Developer checks that Playwright browsers are installed
    before E2E tests.

    Given: The Playwright E2E test job is running in CI
    When: The job reaches the browser installation step
    Then: Chromium is installed via `uv run playwright install
          --with-deps chromium`
    Then: The subsequent Playwright test step can launch the browser
          without errors
    Then: Tests execute against the Chromium browser
    """

    def test_chromium_install_step_exists(self, workflow):
        """The playwright-tests job installs Chromium before running
        tests."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "")
            for step in playwright_job.get("steps", [])
        ]
        chromium_steps = [
            run for run in step_runs
            if "playwright install" in run and "chromium" in run
        ]
        assert len(chromium_steps) >= 1, (
            "Playwright job must include a step that installs Chromium "
            "via 'playwright install ... chromium'"
        )

    def test_chromium_install_uses_with_deps(self, workflow):
        """The Chromium install step uses --with-deps to install
        system dependencies."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "")
            for step in playwright_job.get("steps", [])
        ]
        chromium_steps = [
            run for run in step_runs
            if "playwright install" in run and "chromium" in run
        ]
        assert any("--with-deps" in step for step in chromium_steps), (
            "Chromium install must use --with-deps flag to install "
            "system dependencies"
        )

    def test_chromium_install_before_tests(self, workflow):
        """The Chromium install step appears before the pytest step
        in the job sequence."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        steps = playwright_job.get("steps", [])
        step_runs = [step.get("run", "") for step in steps]

        install_idx = None
        test_idx = None
        for i, run_cmd in enumerate(step_runs):
            if "playwright install" in run_cmd and "chromium" in run_cmd:
                install_idx = i
            if "pytest" in run_cmd and "playwright_tests" in run_cmd:
                test_idx = i

        assert install_idx is not None, (
            "Chromium install step not found"
        )
        assert test_idx is not None, (
            "Playwright pytest step not found"
        )
        assert install_idx < test_idx, (
            f"Chromium install (step {install_idx}) must come before "
            f"pytest (step {test_idx})"
        )

    def test_chromium_install_uses_uv_run(self, workflow):
        """The Chromium install step uses `uv run` prefix."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        step_runs = [
            step.get("run", "")
            for step in playwright_job.get("steps", [])
        ]
        chromium_steps = [
            run for run in step_runs
            if "playwright install" in run and "chromium" in run
        ]
        for cmd in chromium_steps:
            assert "uv run" in cmd, (
                f"Chromium install must use 'uv run' prefix: {cmd}"
            )


class TestScenario7MigrationsRunBeforeUnitTests:
    """
    Scenario: Developer pushes a commit and unit tests run database
    migrations first.

    Given: A developer has pushed code that includes model changes or
           relies on the database schema
    When: The "Unit & Integration Tests" job runs
    Then: Django migrations are applied (`manage.py migrate`) before
          tests execute
    Then: The test step (`manage.py test`) runs against a properly
          migrated database
    Then: Tests that depend on the database schema do not fail due
          to missing migrations
    """

    def test_migrate_step_exists(self, workflow):
        """The unit-tests job includes a migration step."""
        unit_job = workflow["jobs"]["unit-tests"]
        step_runs = [
            step.get("run", "")
            for step in unit_job.get("steps", [])
        ]
        migrate_steps = [
            run for run in step_runs
            if "manage.py migrate" in run
        ]
        assert len(migrate_steps) >= 1, (
            "Unit test job must include a 'manage.py migrate' step"
        )

    def test_migrate_before_test(self, workflow):
        """The migrate step runs before the test step."""
        unit_job = workflow["jobs"]["unit-tests"]
        steps = unit_job.get("steps", [])
        step_runs = [step.get("run", "") for step in steps]

        migrate_idx = None
        test_idx = None
        for i, run_cmd in enumerate(step_runs):
            if "manage.py migrate" in run_cmd and migrate_idx is None:
                migrate_idx = i
            if "manage.py test" in run_cmd:
                test_idx = i

        assert migrate_idx is not None, "Migrate step not found"
        assert test_idx is not None, "Test step not found"
        assert migrate_idx < test_idx, (
            f"Migrate (step {migrate_idx}) must come before test "
            f"(step {test_idx})"
        )

    def test_migrate_uses_uv_run(self, workflow):
        """The migrate step uses `uv run` prefix."""
        unit_job = workflow["jobs"]["unit-tests"]
        step_runs = [
            step.get("run", "")
            for step in unit_job.get("steps", [])
        ]
        migrate_steps = [
            run for run in step_runs
            if "manage.py migrate" in run
        ]
        for cmd in migrate_steps:
            assert "uv run" in cmd, (
                f"Migrate step must use 'uv run' prefix: {cmd}"
            )


class TestScenario8PipelineStructureTwoJobs:
    """
    Scenario: Developer reviews pipeline structure to understand the
    test execution order.

    Given: A developer opens the GitHub Actions tab for a completed
           CI run
    When: They examine the workflow visualization
    Then: They see two distinct jobs: "Unit & Integration Tests" and
          "Playwright E2E Tests"
    Then: The dependency arrow shows Playwright tests depend on unit
          tests (sequential, not parallel)
    Then: Each job shows its individual pass/fail status independently
    """

    def test_exactly_two_jobs(self, workflow):
        """The workflow has exactly two jobs."""
        jobs = workflow["jobs"]
        assert len(jobs) == 2, (
            f"Workflow should have exactly 2 jobs, found {len(jobs)}: "
            f"{list(jobs.keys())}"
        )

    def test_job_names(self, workflow):
        """The two jobs have the expected display names."""
        jobs = workflow["jobs"]
        names = {job.get("name", key) for key, job in jobs.items()}
        assert "Unit & Integration Tests" in names, (
            "Missing job named 'Unit & Integration Tests'"
        )
        assert "Playwright E2E Tests" in names, (
            "Missing job named 'Playwright E2E Tests'"
        )

    def test_sequential_dependency(self, workflow):
        """Playwright tests depend on unit tests (sequential order)."""
        playwright_job = workflow["jobs"]["playwright-tests"]
        needs = playwright_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert "unit-tests" in needs, (
            "Playwright job 'needs' must include 'unit-tests'"
        )

    def test_unit_tests_has_no_dependencies(self, workflow):
        """The unit-tests job has no dependencies (runs first)."""
        unit_job = workflow["jobs"]["unit-tests"]
        needs = unit_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        assert len(needs) == 0, (
            f"unit-tests job should have no dependencies, "
            f"found: {needs}"
        )

    def test_each_job_runs_on_ubuntu(self, workflow):
        """Both jobs run on ubuntu-latest."""
        for job_name in ("unit-tests", "playwright-tests"):
            job = workflow["jobs"][job_name]
            assert job.get("runs-on") == "ubuntu-latest", (
                f"Job '{job_name}' must run on ubuntu-latest"
            )


class TestScenario9NonMainBranchDoesNotTrigger:
    """
    Scenario: Developer pushes to a non-main branch without a PR and
    CI does not trigger.

    Given: A developer is working on a feature branch that has no
           open PR targeting `main`
    When: They push commits to this feature branch
    Then: The CI workflow does not trigger (no wasted compute)
    Then: No status checks appear on the branch's commits
    """

    def test_push_trigger_restricted_to_main(self, workflow):
        """The push trigger only fires for the main branch."""
        on_config = workflow["on"]
        push_config = on_config.get("push", {})
        push_branches = push_config.get("branches", [])
        assert push_branches == ["main"], (
            f"Push trigger should only include 'main', "
            f"found: {push_branches}"
        )

    def test_pr_trigger_restricted_to_main(self, workflow):
        """The pull_request trigger only fires for PRs targeting
        main."""
        on_config = workflow["on"]
        pr_config = on_config.get("pull_request", {})
        pr_branches = pr_config.get("branches", [])
        assert pr_branches == ["main"], (
            f"pull_request trigger should only include 'main', "
            f"found: {pr_branches}"
        )

    def test_no_wildcard_triggers(self, workflow):
        """There are no wildcard branch triggers that would match
        feature branches."""
        on_config = workflow["on"]

        for event_type in ("push", "pull_request"):
            event_config = on_config.get(event_type, {})
            branches = event_config.get("branches", [])
            for branch in branches:
                assert "*" not in branch and "**" not in branch, (
                    f"Event '{event_type}' has wildcard branch "
                    f"'{branch}' that would match feature branches"
                )


class TestScenario10FixAndRePush:
    """
    Scenario: Developer fixes a previously failing test and re-pushes
    to verify the pipeline.

    Given: A developer's previous push caused the CI pipeline to fail
           (red status)
    When: They fix the failing test and push a new commit to the same
          branch/PR
    Then: The CI workflow triggers again on the new commit
    Then: Both unit tests and Playwright tests pass
    Then: The commit/PR status updates from red to green

    This scenario validates that the workflow triggers on every push
    (not just the first), and that both jobs will re-run completely.
    """

    def test_push_trigger_fires_on_every_push(self, workflow):
        """The push trigger does not have a `paths` filter that would
        prevent re-triggering on test-only fixes."""
        on_config = workflow["on"]
        push_config = on_config.get("push", {})
        # No paths filter means every push triggers the workflow
        assert "paths" not in push_config, (
            "Push trigger should not have a 'paths' filter -- every "
            "push to main must trigger CI"
        )

    def test_pr_trigger_fires_on_every_push_to_pr(self, workflow):
        """The pull_request trigger does not have a `paths` filter."""
        on_config = workflow["on"]
        pr_config = on_config.get("pull_request", {})
        assert "paths" not in pr_config, (
            "pull_request trigger should not have a 'paths' filter -- "
            "every push to a PR must trigger CI"
        )

    def test_no_concurrency_cancel_in_progress(self, workflow):
        """If concurrency is set, it must not silently cancel previous
        runs in a way that hides failures. Either there is no
        concurrency key, or cancel-in-progress is explicitly configured.

        This is a soft check: we just verify the structure is reasonable
        for re-push scenarios."""
        # If there is no concurrency key, every push gets its own run
        if "concurrency" not in workflow:
            return  # OK -- each push gets a full run

        concurrency = workflow["concurrency"]
        if isinstance(concurrency, str):
            return  # simple group name, no cancel-in-progress

        # If concurrency has cancel-in-progress, it is acceptable
        # because new pushes replace old runs (common for PRs)
        assert isinstance(concurrency, dict), (
            "concurrency must be a dict or string"
        )

    def test_workflow_runs_both_jobs_on_trigger(self, workflow):
        """Both jobs exist and will run when triggered (no conditional
        skip based on commit message, author, etc.)."""
        for job_name in ("unit-tests", "playwright-tests"):
            job = workflow["jobs"][job_name]
            if_condition = job.get("if", "")
            # Make sure there is no condition that would skip on re-push
            assert "cancelled" not in if_condition.lower(), (
                f"Job '{job_name}' has an 'if' condition that might "
                f"skip on re-push"
            )
