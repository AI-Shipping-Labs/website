from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION_PATH = REPO_ROOT / ".github" / "actions" / "wake-dev-ecs" / "action.yml"
SCHEDULED_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "scheduled-playwright-dev.yml"
DEPLOY_DEV_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"
DEPLOY_PROD_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "deploy-prod.yml"
DEPLOY_SCRIPT_PATH = REPO_ROOT / "deploy" / "deploy_dev.sh"


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


@tag("core")
class DevEcsWakeActionTest(SimpleTestCase):
    def test_shared_action_scales_service_before_polling(self):
        action = _load_yaml(ACTION_PATH)
        run_script = action["runs"]["steps"][0]["run"]

        scale_index = run_script.index("aws ecs update-service")
        poll_index = run_script.index("curl -fsSL")

        self.assertLess(scale_index, poll_index)
        self.assertIn('--cluster "${ECS_CLUSTER}"', run_script)
        self.assertIn('--service "${ECS_SERVICE}"', run_script)
        self.assertIn('--desired-count "${DESIRED_COUNT}"', run_script)
        self.assertIn('grep -Fq -- "${EXPECTED_TEXT}"', run_script)

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
            "max-attempts",
            "sleep-seconds",
        ):
            with self.subTest(name=name):
                self.assertIn(name, inputs)


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
        self.assertEqual(wake_step["with"]["max-attempts"], "30")
        self.assertEqual(wake_step["with"]["sleep-seconds"], "10")

    def test_playwright_matrix_and_marker_filter_remain_unchanged(self):
        workflow = _load_yaml(SCHEDULED_WORKFLOW_PATH)
        playwright_job = workflow["jobs"]["playwright-dev"]

        self.assertEqual(
            [item["shard_name"] for item in playwright_job["strategy"]["matrix"]["include"]],
            ["shard 1/4", "shard 2/4", "shard 3/4", "shard 4/4"],
        )
        workflow_text = SCHEDULED_WORKFLOW_PATH.read_text()
        self.assertIn("uv run playwright install --with-deps chromium", workflow_text)
        self.assertIn('uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" "${files[@]}" -v', workflow_text)
        self.assertIn("Open or update failure issue", workflow_text)


@tag("core")
class DeployDevWakeWorkflowTest(SimpleTestCase):
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
