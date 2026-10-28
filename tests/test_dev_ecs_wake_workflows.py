import re
from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEDULED_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "scheduled-playwright-dev.yml"
DEPLOY_DEV_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
DEPLOY_PROD_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-prod.yml"
POSTGRESQL_TAGGED_TESTS = (
    "uv run python manage.py test --tag=postgresql --verbosity 2"
)


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


@tag("core")
class ScheduledPlaywrightDevReadinessWorkflowTest(SimpleTestCase):
    def test_scheduled_workflow_gates_playwright_on_contabo_readiness(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        jobs = workflow["jobs"]

        readiness_job = jobs["dev-readiness"]
        playwright_job = jobs["playwright-dev"]
        notify_job = jobs["notify"]

        self.assertEqual(playwright_job["needs"], ["dev-readiness"])
        self.assertEqual(notify_job["needs"], ["dev-readiness", "playwright-dev"])
        self.assertEqual(readiness_job["timeout-minutes"], 15)
        self.assertEqual(readiness_job["permissions"], {"contents": "read"})

        readiness_step = next(
            step
            for step in readiness_job["steps"]
            if step.get("name") == "Wait for Contabo dev endpoint"
        )
        self.assertIn("source deploy/readiness_poll.sh", readiness_step["run"])
        self.assertIn('${PLAYWRIGHT_BASE_URL%/}/ping', readiness_step["run"])
        self.assertIn(
            'readiness_poll_until_stable "${ping_url}" "" 540 10 60 3',
            readiness_step["run"],
        )
        self.assertNotIn("continue-on-error", readiness_step)

        warm_step = next(
            step
            for step in readiness_job["steps"]
            if step.get("name") == "Warm dev application endpoints"
        )
        self.assertEqual(warm_step["timeout-minutes"], 5)
        self.assertIn('${PLAYWRIGHT_BASE_URL%/}${endpoint}', warm_step["run"])
        self.assertIn("/courses", warm_step["run"])
        self.assertIn("/sitemap.xml", warm_step["run"])

    def test_scheduled_workflow_has_no_aws_or_ecs_dependency(self):
        workflow_text = SCHEDULED_WORKFLOW_PATH.read_text().lower()

        for forbidden in ("aws", "ecs", "id-token", "role-to-assume", "wake-dev"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow_text)

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

    def test_playwright_matrix_is_serialized_for_shared_contabo_service(self):
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
            ["checks", "test", "playwright-core", "postgres-verification", "coverage"],
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

    def test_deploy_uses_exact_head_coolify_helper(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        deploy_job = workflow["jobs"]["deploy"]
        deploy_step = next(step for step in deploy_job["steps"] if step.get("name") == "Deploy to Dev")
        self.assertIn("deploy.coolify.deploy_exact_release", deploy_step["run"])
        self.assertIn("steps.identity.outputs.image", deploy_step["run"])
        self.assertIn("steps.image.outputs.digest", deploy_step["run"])
        self.assertIn('sha "${GITHUB_SHA}"', deploy_step["run"])
        self.assertEqual(deploy_job["name"], "Deploy to Dev")
        self.assertEqual(deploy_job["environment"], "coolify-staging")

    def test_deploy_builds_one_amd64_ghcr_image_with_full_revision(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        deploy_job = workflow["jobs"]["deploy"]
        build = next(step for step in deploy_job["steps"] if step.get("id") == "image")
        identity = next(step for step in deploy_job["steps"] if step.get("id") == "identity")

        self.assertEqual(build["uses"], "docker/build-push-action@v6")
        self.assertEqual(build["with"]["platforms"], "linux/amd64")
        self.assertTrue(build["with"]["push"])
        self.assertIn("VERSION=${{ github.sha }}", build["with"]["build-args"])
        self.assertIn("org.opencontainers.image.revision=${{ github.sha }}", build["with"]["labels"])
        self.assertIn("GITHUB_RUN_ID", identity["run"])
        self.assertIn("GITHUB_RUN_ATTEMPT", identity["run"])
        self.assertIn("GITHUB_SHA", identity["run"])

    def test_deploy_dev_contains_no_retired_aws_runtime_steps(self):
        deploy_job = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)["jobs"]["deploy"]
        text = yaml.safe_dump(deploy_job)

        for retired in ("aws-actions", "aws ecr", "aws ecs", "REPO_URI", "wake-dev-ecs"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)

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

        for job_name in ("test", "playwright-core", "postgres-verification", "coverage"):
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
