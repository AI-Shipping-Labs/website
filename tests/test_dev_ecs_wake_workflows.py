import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "wake-dev-ecs" / "action.yml"
SCHEDULED_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "scheduled-playwright-dev.yml"
DEPLOY_DEV_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_PROD_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-prod.yml"
DEPLOY_SCRIPT_PATH = REPO_ROOT / "deploy" / "deploy_dev.sh"
POSTGRESQL_TAGGED_TESTS = (
    "uv run python manage.py test --tag=postgresql --verbosity 2"
)


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


@tag("core")
class DevEcsWakeActionTest(SimpleTestCase):
    def test_shared_action_scales_service_before_polling(self):
        action = _load_yaml(ACTION_PATH)
        run_script = action["runs"]["steps"][0]["run"]

        scale_index = run_script.index("aws ecs update-service")
        poll_index = run_script.index("readiness_poll_until_stable")
        validation_index = run_script.index("readiness_validate_configuration")

        self.assertLess(validation_index, scale_index)
        self.assertLess(scale_index, poll_index)
        self.assertIn('--cluster "${ECS_CLUSTER}"', run_script)
        self.assertIn('--service "${ECS_SERVICE}"', run_script)
        self.assertIn('--desired-count "${DESIRED_COUNT}"', run_script)
        self.assertIn('source "${GITHUB_ACTION_PATH}/../../../deploy/readiness_poll.sh"', run_script)
        self.assertIn('"${REQUIRED_CONSECUTIVE}"', run_script)
        self.assertNotIn("grep -Fq", run_script)

    def test_shared_action_exposes_required_configuration_inputs(self):
        action = _load_yaml(ACTION_PATH)
        inputs = action["inputs"]

        for name in (
            "aws-region",
            "ecs-cluster",
            "ecs-service",
            "desired-count",
            "ping-url",
            "expected-text",
            "timeout-seconds",
            "poll-seconds",
            "max-attempts",
            "required-consecutive",
        ):
            with self.subTest(name=name):
                self.assertIn(name, inputs)

        self.assertEqual(inputs["timeout-seconds"]["default"], "1500")
        self.assertEqual(inputs["poll-seconds"]["default"], "10")
        self.assertEqual(inputs["max-attempts"]["default"], "150")
        self.assertEqual(inputs["required-consecutive"]["default"], "3")
        self.assertIn(
            "exact response body",
            inputs["expected-text"]["description"],
        )


@tag("core")
class ScheduledPlaywrightDevWakeWorkflowTest(SimpleTestCase):
    def test_scheduled_workflow_wakes_dev_before_playwright_matrix(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        jobs = workflow["jobs"]

        wake_job = jobs["wake-dev"]
        playwright_job = jobs["playwright-dev"]
        notify_job = jobs["notify"]

        self.assertEqual(playwright_job["needs"], ["wake-dev"])
        self.assertEqual(notify_job["needs"], ["wake-dev", "playwright-dev"])
        self.assertEqual(wake_job["permissions"]["contents"], "read")
        self.assertEqual(wake_job["permissions"]["id-token"], "write")

        configure_step = next(
            step
            for step in wake_job["steps"]
            if step.get("uses") == "aws-actions/configure-aws-credentials@v6"
        )
        self.assertEqual(
            configure_step["with"]["role-to-assume"],
            "arn:aws:iam::387546586013:role/website-deploy",
        )
        self.assertEqual(configure_step["with"]["aws-region"], "eu-west-1")

        wake_step = next(
            step
            for step in wake_job["steps"]
            if step.get("uses") == "./.github/actions/wake-dev-ecs"
        )
        self.assertEqual(wake_step["with"]["aws-region"], "eu-west-1")
        self.assertEqual(wake_step["with"]["ecs-cluster"], "ai-shipping-labs")
        self.assertEqual(wake_step["with"]["ecs-service"], "ai-shipping-labs-dev")
        self.assertEqual(wake_step["with"]["desired-count"], "1")
        self.assertEqual(wake_step["with"]["ping-url"], "https://dev.aishippinglabs.com/ping")
        self.assertEqual(wake_step["with"]["timeout-seconds"], "300")
        self.assertEqual(wake_step["with"]["poll-seconds"], "10")
        self.assertEqual(wake_step["with"]["max-attempts"], "30")
        self.assertEqual(wake_step["with"]["required-consecutive"], "3")

    def test_playwright_matrix_and_marker_filter_remain_unchanged(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        playwright_job = workflow["jobs"]["playwright-dev"]

        self.assertEqual(
            [item["shard_name"] for item in playwright_job["strategy"]["matrix"]["include"]],
            ["shard 1/4", "shard 2/4", "shard 3/4", "shard 4/4"],
        )
        workflow_text = SCHEDULED_WORKFLOW_PATH.read_text()
        self.assertIn("uv run playwright install --with-deps chromium", workflow_text)
        self.assertIn(
            'uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" '
            '"${files[@]}" -v --durations=25',
            workflow_text,
        )
        self.assertIn("Open or update failure issue", workflow_text)

    def test_playwright_matrix_is_serialized_for_single_low_capacity_dev_task(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        strategy = workflow["jobs"]["playwright-dev"]["strategy"]

        self.assertFalse(strategy["fail-fast"])
        self.assertEqual(strategy["max-parallel"], 1)
        self.assertEqual(len(strategy["matrix"]["include"]), 4)


@tag("core")
class DeployDevWakeWorkflowTest(SimpleTestCase):
    def test_playwright_core_matrix_is_disjoint_complete_and_fail_fast_false(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        playwright_job = workflow["jobs"]["playwright-core"]
        matrix = playwright_job["strategy"]["matrix"]["include"]

        self.assertFalse(playwright_job["strategy"]["fail-fast"])
        self.assertEqual(
            matrix,
            [
                {"shard_name": "shard 1/4", "shard_index": 0, "shard_total": 4},
                {"shard_name": "shard 2/4", "shard_index": 1, "shard_total": 4},
                {"shard_name": "shard 3/4", "shard_index": 2, "shard_total": 4},
                {"shard_name": "shard 4/4", "shard_index": 3, "shard_total": 4},
            ],
        )
        self.assertEqual(
            playwright_job["name"],
            "Playwright Core E2E (${{ matrix.shard_name }})",
        )

        files = sorted(REPO_ROOT.glob("playwright_tests/test_*.py"))
        assigned = [
            path
            for item in matrix
            for position, path in enumerate(files)
            if position % item["shard_total"] == item["shard_index"]
        ]
        self.assertEqual(len(assigned), len(files))
        self.assertEqual(len(set(assigned)), len(files))
        self.assertEqual(set(assigned), set(files))

        selector = next(
            step["run"]
            for step in playwright_job["steps"]
            if step.get("name") == "Select Playwright core shard files"
        )
        self.assertIn(
            "find playwright_tests -maxdepth 1 -type f -name 'test_*.py' | sort",
            selector,
        )
        self.assertIn("'((NR - 1) % total) == shard'", selector)
        self.assertNotIn("test_account", selector)

    def test_playwright_core_shards_preserve_marker_browser_and_timeout_contracts(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        playwright_job = workflow["jobs"]["playwright-core"]

        self.assertEqual(playwright_job["timeout-minutes"], 30)
        browser_step = next(
            step
            for step in playwright_job["steps"]
            if step.get("name") == "Install Playwright browsers"
        )
        self.assertEqual(
            browser_step["run"],
            "uv run playwright install --with-deps chromium",
        )
        run_step = next(
            step
            for step in playwright_job["steps"]
            if step.get("name") == "Run Playwright core shard"
        )
        self.assertIn(
            'uv run pytest -m "core and not manual_visual and not slow_platform and not visual_regression" '
            '"${files[@]}" -v --durations=25',
            run_step["run"],
        )
        self.assertNotIn("pytest-xdist", DEPLOY_DEV_WORKFLOW_PATH.read_text())

    def test_deploy_waits_for_every_playwright_core_matrix_job(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        deploy_job = workflow["jobs"]["deploy"]

        self.assertEqual(
            deploy_job["needs"],
            ["checks", "test", "playwright-core", "postgres-verification"],
        )
        self.assertFalse(
            workflow["jobs"]["playwright-core"].get("continue-on-error", False)
        )

    def test_deploy_requires_non_skipping_postgresql_verification_gate(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        postgres_job = workflow["jobs"]["postgres-verification"]
        deploy_job = workflow["jobs"]["deploy"]

        self.assertNotIn("if", postgres_job)
        self.assertFalse(postgres_job.get("continue-on-error", False))
        self.assertEqual(postgres_job["name"], "PostgreSQL 16 Verification")
        self.assertIn("postgres-verification", deploy_job["needs"])
        self.assertEqual(postgres_job["services"]["postgres"]["image"], "postgres:16")
        migration_step = next(
            step
            for step in postgres_job["steps"]
            if step.get("name") == "Run serial production-baseline migration matrix"
        )
        self.assertEqual(
            migration_step["run"],
            "uv run python manage.py test tests.test_r1_migration_compatibility --verbosity 2",
        )
        standard_test_step = next(
            step
            for step in workflow["jobs"]["test"]["steps"]
            if step.get("name") == "Run unit and integration tests"
        )
        self.assertIn(
            "--exclude-tag=postgres_migration",
            standard_test_step["run"],
        )
        self.assertNotIn(
            "--exclude-tag=postgres_migration",
            migration_step["run"],
        )
        postgres_tests_step = next(
            step
            for step in postgres_job["steps"]
            if step.get("name") == "Run PostgreSQL-tagged verification tests"
        )
        self.assertEqual(postgres_tests_step["run"], POSTGRESQL_TAGGED_TESTS)

    def test_pull_request_requires_non_skipping_postgresql_race(self):
        workflow = _load_yaml(CI_WORKFLOW_PATH)
        postgres_job = workflow["jobs"]["postgres-verification"]

        self.assertNotIn("if", postgres_job)
        self.assertFalse(postgres_job.get("continue-on-error", False))
        self.assertEqual(postgres_job["name"], "PostgreSQL 16 Verification")
        self.assertEqual(postgres_job["services"]["postgres"]["image"], "postgres:16")
        postgres_tests_step = next(
            step
            for step in postgres_job["steps"]
            if step.get("name") == "Run PostgreSQL-tagged verification tests"
        )
        self.assertEqual(postgres_tests_step["run"], POSTGRESQL_TAGGED_TESTS)

    def test_deploy_verification_uses_shared_action_with_exact_version_match(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        deploy_job = workflow["jobs"]["deploy"]

        verify_step = next(step for step in deploy_job["steps"] if step.get("name") == "Verify deploy landed")

        self.assertEqual(verify_step["uses"], "./.github/actions/wake-dev-ecs")
        self.assertEqual(verify_step["with"]["aws-region"], "${{ env.AWS_REGION }}")
        self.assertEqual(verify_step["with"]["ecs-cluster"], "ai-shipping-labs")
        self.assertEqual(verify_step["with"]["ecs-service"], "ai-shipping-labs-dev")
        self.assertEqual(verify_step["with"]["desired-count"], "1")
        self.assertEqual(verify_step["with"]["ping-url"], "https://dev.aishippinglabs.com/ping")
        self.assertEqual(verify_step["with"]["expected-text"], "${{ env.TAG }}")

        self.assertNotIn("Polling https://dev.aishippinglabs.com/ping", DEPLOY_DEV_WORKFLOW_PATH.read_text())

    def test_deploy_script_and_verify_action_grace_windows_are_aligned(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        deploy_job = workflow["jobs"]["deploy"]
        deploy_env = deploy_job["env"]

        self.assertEqual(deploy_env["DEV_DEPLOY_GRACE_TIMEOUT_SECONDS"], "1500")
        self.assertEqual(deploy_env["DEV_DEPLOY_GRACE_POLL_SECONDS"], "10")
        self.assertEqual(deploy_env["DEV_DEPLOY_GRACE_ATTEMPTS"], "150")
        self.assertEqual(deploy_env["DEV_DEPLOY_GRACE_REQUIRED_MATCHES"], "3")

        deploy_step = next(step for step in deploy_job["steps"] if step.get("name") == "Deploy to Dev")
        self.assertEqual(
            deploy_step["env"]["DEPLOY_GRACE_TIMEOUT_SECONDS"],
            "${{ env.DEV_DEPLOY_GRACE_TIMEOUT_SECONDS }}",
        )
        self.assertEqual(
            deploy_step["env"]["DEPLOY_GRACE_POLL_SECONDS"],
            "${{ env.DEV_DEPLOY_GRACE_POLL_SECONDS }}",
        )
        self.assertEqual(
            deploy_step["env"]["DEPLOY_GRACE_ATTEMPTS"],
            "${{ env.DEV_DEPLOY_GRACE_ATTEMPTS }}",
        )
        self.assertEqual(
            deploy_step["env"]["DEPLOY_GRACE_REQUIRED_MATCHES"],
            "${{ env.DEV_DEPLOY_GRACE_REQUIRED_MATCHES }}",
        )

        verify_step = next(step for step in deploy_job["steps"] if step.get("name") == "Verify deploy landed")
        self.assertEqual(
            verify_step["with"]["timeout-seconds"],
            "${{ env.DEV_DEPLOY_GRACE_TIMEOUT_SECONDS }}",
        )
        self.assertEqual(
            verify_step["with"]["poll-seconds"],
            "${{ env.DEV_DEPLOY_GRACE_POLL_SECONDS }}",
        )
        self.assertEqual(
            verify_step["with"]["max-attempts"],
            "${{ env.DEV_DEPLOY_GRACE_ATTEMPTS }}",
        )
        self.assertEqual(
            verify_step["with"]["required-consecutive"],
            "${{ env.DEV_DEPLOY_GRACE_REQUIRED_MATCHES }}",
        )

        script = DEPLOY_SCRIPT_PATH.read_text()
        self.assertIn("DEFAULT_DEPLOY_GRACE_TIMEOUT_SECONDS=1500", script)
        self.assertIn("DEFAULT_DEPLOY_GRACE_POLL_SECONDS=10", script)
        self.assertIn("DEFAULT_DEPLOY_GRACE_ATTEMPTS=150", script)
        self.assertIn("DEFAULT_DEPLOY_GRACE_REQUIRED_MATCHES=3", script)
        self.assertIn("DataTalksClub/aws-infra#11", script)
        self.assertIn("not IntegrationSettings", script)

    def test_dev_deploy_script_sets_desired_count_for_dev_combined_service(self):
        script = DEPLOY_SCRIPT_PATH.read_text()

        self.assertIn('if [ "${ENV}" = "dev" ] || [ "${ROLE}" = "worker" ]; then', script)
        self.assertIn("--task-definition ${NEW_TASK_DEF_ARN}", script)
        self.assertIn("--desired-count 1", script)

    def test_production_workflow_does_not_use_dev_wake_action(self):
        prod_workflow = DEPLOY_PROD_WORKFLOW_PATH.read_text()

        self.assertNotIn("wake-dev", prod_workflow)
        self.assertNotIn("./.github/actions/wake-dev-ecs", prod_workflow)
        self.assertNotIn("ai-shipping-labs-dev", prod_workflow)


_BLOCKING_RUFF_RE = re.compile(r"(?:uv run ruff check \.|make lint(?:\s|$))")


def _blocking_ruff_steps(job):
    return [
        step
        for step in job.get("steps", [])
        if _BLOCKING_RUFF_RE.search(step.get("run", ""))
    ]


@tag("core")
class DeployDevRuffGateWorkflowTest(SimpleTestCase):
    def test_deploy_dev_runs_blocking_ruff_in_checks_without_error_suppression(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        checks_job = workflow["jobs"]["checks"]
        ruff_steps = _blocking_ruff_steps(checks_job)

        self.assertEqual(len(ruff_steps), 1)
        step = ruff_steps[0]
        command = step["run"]

        self.assertEqual(step["name"], "Lint (ruff)")
        self.assertIn("uv run ruff check .", command)
        self.assertNotIn("ruff-advisory.toml", command)
        self.assertNotIn("--exit-zero", command)
        self.assertNotIn("--fix", command)
        self.assertNotIn("continue-on-error", step)
        self.assertFalse(step.get("continue-on-error", False))
        self.assertNotIn("continue-on-error", checks_job)
        self.assertFalse(checks_job.get("continue-on-error", False))
        self.assertIn("checks", workflow["jobs"]["deploy"]["needs"])

    def test_blocking_ruff_is_not_duplicated_onto_deploy_shards(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)

        for job_name in ("test", "playwright-core", "postgres-verification"):
            with self.subTest(job=job_name):
                self.assertEqual(_blocking_ruff_steps(workflow["jobs"][job_name]), [])

    def test_ci_workflow_still_runs_blocking_ruff_on_pull_requests(self):
        workflow = _load_yaml(CI_WORKFLOW_PATH)
        ruff_steps = _blocking_ruff_steps(workflow["jobs"]["checks"])

        self.assertEqual(len(ruff_steps), 1)
        self.assertEqual(ruff_steps[0]["name"], "Lint (ruff)")
        self.assertIn("uv run ruff check .", ruff_steps[0]["run"])
        self.assertNotIn("ruff-advisory.toml", ruff_steps[0]["run"])
        self.assertNotIn("--exit-zero", ruff_steps[0]["run"])
        self.assertNotIn("--fix", ruff_steps[0]["run"])

    def test_make_lint_remains_blocking_ruff_check(self):
        makefile = (REPO_ROOT / "Makefile").read_text()

        self.assertRegex(makefile, r"(?m)^lint:\n\tuv run ruff check \.\n")
