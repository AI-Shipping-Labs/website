"""Workflow-wiring contract for the Deploy Dev 85% coverage gate (#1512)."""

from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DEV_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"
WATCH_CI_PATH = REPO_ROOT / "scripts" / "watch-ci.py"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

SHARD_INDEXES = [1, 2, 3, 4]
TEST_EXCLUSIONS = (
    "--exclude-tag=visual_regression",
    "--exclude-tag=postgres_migration",
)


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


@tag("core")
class DeployDevCoverageGateWorkflowTest(SimpleTestCase):
    def test_test_shards_collect_coverage_for_the_existing_split(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        test_job = workflow["jobs"]["test"]
        run_step = _step(test_job, "Run unit and integration tests")
        command = run_step["run"]

        self.assertEqual(test_job["strategy"]["matrix"]["shard"], SHARD_INDEXES)
        self.assertEqual(test_job["env"]["TEST_SHARD_COUNT"], "4")
        self.assertEqual(test_job["env"]["TEST_SHARD_INDEX"], "${{ matrix.shard }}")
        self.assertIn("uv run coverage run", command)
        self.assertIn("--parallel-mode", command)
        self.assertIn("--concurrency=multiprocessing", command)
        self.assertIn("manage.py test", command)
        self.assertIn("--parallel 4", command)
        for flag in TEST_EXCLUSIONS:
            self.assertIn(flag, command)
        self.assertIn("--keepdb", command)
        self.assertNotIn("make coverage", command)
        self.assertNotRegex(command, r"(?m)^\s*uv run python manage.py test")

        upload_step = _step(test_job, "Upload coverage data")
        self.assertEqual(upload_step["uses"], "actions/upload-artifact@v4")
        self.assertEqual(
            upload_step["with"]["name"],
            "coverage-shard-${{ matrix.shard }}",
        )
        self.assertEqual(upload_step["with"]["path"], "coverage-data")
        self.assertTrue(upload_step["with"]["include-hidden-files"])
        self.assertEqual(upload_step["with"]["if-no-files-found"], "error")
        self.assertNotIn("continue-on-error", upload_step)
        self.assertFalse(upload_step.get("continue-on-error", False))

        stage_step = _step(test_job, "Stage coverage data")
        self.assertIn("coverage-data", stage_step["run"])
        self.assertIn(".coverage", stage_step["run"])
        self.assertIn('coverage-data/$(basename "$f")', stage_step["run"])
        self.assertNotIn("sed 's/^\\.//'", stage_step["run"])
        self.assertNotIn("continue-on-error", stage_step)
        self.assertFalse(stage_step.get("continue-on-error", False))

    def test_coverage_job_combines_shard_artifacts_and_fails_under_85(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        coverage_job = workflow["jobs"]["coverage"]
        report_step = _step(coverage_job, "Combine and report coverage")
        download_step = _step(coverage_job, "Download shard coverage data")
        command = report_step["run"]

        self.assertEqual(coverage_job["name"], "Combined coverage (fail-under 85)")
        self.assertEqual(_as_list(coverage_job.get("needs")), ["test"])
        self.assertNotIn("continue-on-error", coverage_job)
        self.assertFalse(coverage_job.get("continue-on-error", False))
        self.assertNotIn("continue-on-error", report_step)
        self.assertFalse(report_step.get("continue-on-error", False))
        self.assertNotIn("continue-on-error", download_step)
        self.assertFalse(download_step.get("continue-on-error", False))

        self.assertEqual(download_step["uses"], "actions/download-artifact@v4")
        self.assertEqual(download_step["with"]["pattern"], "coverage-shard-*")
        self.assertTrue(download_step["with"]["merge-multiple"])
        self.assertEqual(download_step["with"]["path"], "coverage-data")

        self.assertIn("coverage combine", command)
        self.assertIn("coverage report --fail-under=85", command)
        self.assertNotIn("make coverage", command)
        self.assertNotIn("manage.py test", command)
        self.assertNotIn("continue-on-error", command)

        workflow_text = DEPLOY_DEV_WORKFLOW_PATH.read_text()
        self.assertNotIn("make coverage", workflow_text)

    def test_deploy_needs_coverage_and_cannot_skip_a_miss(self):
        workflow = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        deploy_job = workflow["jobs"]["deploy"]
        coverage_job = workflow["jobs"]["coverage"]

        self.assertIn("coverage", _as_list(deploy_job.get("needs")))
        self.assertEqual(
            _as_list(deploy_job.get("needs")),
            ["checks", "test", "playwright-core", "postgres-verification", "coverage"],
        )
        self.assertNotIn("continue-on-error", coverage_job)
        self.assertFalse(coverage_job.get("continue-on-error", False))
        for step in coverage_job["steps"]:
            with self.subTest(step=step.get("name")):
                self.assertNotIn("continue-on-error", step)
                self.assertFalse(step.get("continue-on-error", False))

        self.assertIn(
            f'"{coverage_job["name"]}"',
            WATCH_CI_PATH.read_text(),
        )

    def test_make_coverage_remains_the_optional_local_full_run(self):
        makefile = MAKEFILE_PATH.read_text()
        self.assertRegex(
            makefile,
            r"(?m)^coverage:\n"
            r"\tuv run coverage erase\n"
            r"\tuv run coverage run manage.py test\n"
            r"\tuv run coverage report --fail-under=85\n",
        )
        self.assertNotIn("make coverage", DEPLOY_DEV_WORKFLOW_PATH.read_text())
