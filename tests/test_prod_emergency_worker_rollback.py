from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
WORKER_ROLLBACK_WORKFLOW_PATH = WORKFLOWS_DIR / "prod-emergency-worker-rollback.yml"
WEB_ROLLBACK_WORKFLOW_PATH = WORKFLOWS_DIR / "prod-emergency-web-rollback.yml"
DEPLOY_PROD_WORKFLOW_PATH = WORKFLOWS_DIR / "deploy-prod.yml"
DIAGNOSTICS_WORKFLOW_PATH = WORKFLOWS_DIR / "prod-emergency-diagnostics.yml"
DEPLOY_DEV_WORKFLOW_PATH = WORKFLOWS_DIR / "deploy-dev.yml"
SETUP_DOC_PATH = REPO_ROOT / "_docs" / "setup.md"
PRODUCTION_ECS_MUTATION_GROUP = "production-ecs-mutation"
REQUIRED_CONCURRENCY_BLOCK = (
    "concurrency:\n"
    "  group: production-ecs-mutation\n"
    "  cancel-in-progress: false\n"
)
WORKER_FAMILY_REGEX = r"^ai-shipping-labs-worker-prod:[1-9][0-9]*$"
EXPECTED_TAG_REGEX = r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{7,40}$"
WORKER_TASK_ARN_PREFIX = (
    "arn:aws:ecs:${AWS_REGION}:${AWS_ACCOUNT_ID}:task-definition/${TASK_DEFINITION}"
)


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


def _step(job, name):
    return next(step for step in job["steps"] if step.get("name") == name)


def _step_index(job, name):
    return next(i for i, step in enumerate(job["steps"]) if step.get("name") == name)


@tag("core")
class ProdEmergencyWorkerRollbackWorkflowTest(SimpleTestCase):
    def test_worker_rollback_workflow_exists_with_required_inputs(self):
        self.assertTrue(WORKER_ROLLBACK_WORKFLOW_PATH.is_file())
        workflow = _load_yaml(WORKER_ROLLBACK_WORKFLOW_PATH)
        self.assertEqual(workflow["name"], "Emergency Production Worker Rollback")

        on_block = workflow.get("on", workflow.get(True))
        dispatch = on_block["workflow_dispatch"]
        inputs = dispatch["inputs"]
        self.assertEqual(set(inputs), {"confirmRollback", "taskDefinition", "expectedTag"})
        self.assertTrue(inputs["confirmRollback"]["required"])
        self.assertEqual(inputs["confirmRollback"]["type"], "boolean")
        self.assertIs(inputs["confirmRollback"]["default"], False)
        self.assertTrue(inputs["taskDefinition"]["required"])
        self.assertEqual(inputs["taskDefinition"]["type"], "string")
        self.assertIn("ai-shipping-labs-worker-prod", inputs["taskDefinition"]["description"])
        self.assertTrue(inputs["expectedTag"]["required"])
        self.assertEqual(inputs["expectedTag"]["type"], "string")
        self.assertNotIn("choice", dispatch)
        self.assertNotIn("serviceRole", inputs)

        job = workflow["jobs"]["rollback"]
        self.assertEqual(job["name"], "Rollback Production Worker Service")
        self.assertEqual(job["timeout-minutes"], 30)
        self.assertEqual(job["permissions"]["contents"], "read")
        self.assertEqual(job["permissions"]["id-token"], "write")
        self.assertEqual(job["env"]["AWS_REGION"], "eu-west-1")
        self.assertEqual(job["env"]["AWS_ACCOUNT_ID"], "387546586013")
        self.assertEqual(job["env"]["ECS_CLUSTER"], "ai-shipping-labs")
        self.assertEqual(job["env"]["ECS_WORKER_SERVICE"], "ai-shipping-labs-worker-prod")
        self.assertNotIn("PROD_PING_URL", job["env"])

    def test_invalid_or_unconfirmed_inputs_fail_before_aws_mutation(self):
        workflow = _load_yaml(WORKER_ROLLBACK_WORKFLOW_PATH)
        job = workflow["jobs"]["rollback"]
        validate = _step(job, "Validate emergency rollback inputs")
        run = validate["run"]

        self.assertIn('"${CONFIRM_ROLLBACK}" != "true"', run)
        self.assertIn(WORKER_FAMILY_REGEX, run)
        self.assertIn(EXPECTED_TAG_REGEX, run)
        self.assertLess(
            _step_index(job, "Validate emergency rollback inputs"),
            _step_index(job, "Configure AWS credentials (OIDC)"),
        )
        self.assertLess(
            _step_index(job, "Validate emergency rollback inputs"),
            _step_index(job, "Roll back production worker service only"),
        )

        configure = _step(job, "Configure AWS credentials (OIDC)")
        self.assertEqual(
            configure["uses"],
            "aws-actions/configure-aws-credentials@v6",
        )
        self.assertEqual(
            configure["with"]["role-to-assume"],
            "arn:aws:iam::387546586013:role/website-deploy",
        )
        self.assertEqual(configure["with"]["aws-region"], "${{ env.AWS_REGION }}")

    def test_resolved_arn_and_expected_tag_must_match_before_update_service(self):
        workflow = _load_yaml(WORKER_ROLLBACK_WORKFLOW_PATH)
        job = workflow["jobs"]["rollback"]
        resolve = _step(job, "Resolve exact task definition")
        run = resolve["run"]

        self.assertIn(WORKER_TASK_ARN_PREFIX, run)
        self.assertIn("aws ecs describe-task-definition", run)
        self.assertIn("taskDefinition.taskDefinitionArn", run)
        self.assertIn("containerDefinitions[].image", run)
        self.assertIn("environment[?name=='VERSION'].value", run)
        self.assertIn("image tag or VERSION does not equal expectedTag", run)
        self.assertIn("not calling update-service", run)
        self.assertLess(
            _step_index(job, "Resolve exact task definition"),
            _step_index(job, "Roll back production worker service only"),
        )

    def test_mutation_targets_worker_with_desired_count_and_not_web(self):
        workflow = _load_yaml(WORKER_ROLLBACK_WORKFLOW_PATH)
        job = workflow["jobs"]["rollback"]
        mutation = _step(job, "Roll back production worker service only")
        run = mutation["run"]

        self.assertIn("${ECS_WORKER_SERVICE}", run)
        self.assertIn("ai-shipping-labs-worker-prod", job["env"]["ECS_WORKER_SERVICE"])
        self.assertIn("--desired-count 1", run)
        self.assertIn("aws ecs update-service", run)
        self.assertIn("aws ecs wait services-stable", run)
        self.assertIn("deployments[?status=='PRIMARY']", run)
        self.assertIn("deployments[?status=='ACTIVE']", run)
        self.assertIn("not calling update-service", run)
        self.assertLess(run.index("aws ecs describe-services"), run.index("aws ecs update-service"))
        self.assertLess(
            run.index("deployments[?status=='ACTIVE']"),
            run.index("aws ecs update-service"),
        )
        self.assertLess(run.index("aws ecs update-service"), run.index("aws ecs wait services-stable"))
        self.assertNotIn("--service ai-shipping-labs-prod", run)
        self.assertNotIn("ai-shipping-labs-prod", run)
        self.assertNotIn("ECS_WEB_SERVICE", run)

        web_mutation = _step(
            _load_yaml(WEB_ROLLBACK_WORKFLOW_PATH)["jobs"]["rollback"],
            "Roll back production web service only",
        )
        self.assertNotIn("ai-shipping-labs-worker-prod", web_mutation["run"])
        self.assertNotIn("--desired-count 1", web_mutation["run"])

    def test_verification_requires_primary_running_containers_not_ping(self):
        workflow = _load_yaml(WORKER_ROLLBACK_WORKFLOW_PATH)
        job = workflow["jobs"]["rollback"]
        mutation = _step(job, "Roll back production worker service only")
        verify = _step(job, "Verify recovered worker ECS state")
        mutation_run = mutation["run"]
        verify_run = verify["run"]
        workflow_text = WORKER_ROLLBACK_WORKFLOW_PATH.read_text()

        self.assertIn("deployments[?status=='PRIMARY']", mutation_run)
        self.assertIn("runningCount", mutation_run)
        self.assertIn("desiredCount", mutation_run)
        self.assertIn("aws ecs describe-services", mutation_run)
        self.assertIn("aws ecs list-tasks", verify_run)
        self.assertIn("--desired-status RUNNING", verify_run)
        self.assertIn("aws ecs describe-tasks", verify_run)
        self.assertIn("taskDefinitionArn", verify_run)
        self.assertIn("lastStatus", verify_run)
        self.assertIn("containers all RUNNING", verify_run)
        self.assertIn("verify_recovery_ecs_state", verify_run)
        self.assertNotIn("https://aishippinglabs.com/ping", workflow_text)
        self.assertNotIn("/ping", verify_run)
        self.assertNotIn("PROD_PING_URL", workflow_text)
        self.assertNotIn(".prod-versions", workflow_text)
        self.assertIn("do not reverse migrations", verify_run.lower())

        web_text = WEB_ROLLBACK_WORKFLOW_PATH.read_text()
        self.assertIn("https://aishippinglabs.com/ping", web_text)
        self.assertNotIn("ai-shipping-labs-worker-prod", web_text)

    def test_shared_non_cancelling_concurrency_excludes_diagnostics_and_dev(self):
        for path in (
            WORKER_ROLLBACK_WORKFLOW_PATH,
            WEB_ROLLBACK_WORKFLOW_PATH,
            DEPLOY_PROD_WORKFLOW_PATH,
        ):
            with self.subTest(path=path.name):
                workflow = _load_yaml(path)
                self.assertEqual(
                    workflow["concurrency"]["group"],
                    PRODUCTION_ECS_MUTATION_GROUP,
                )
                self.assertIs(workflow["concurrency"]["cancel-in-progress"], False)
                self.assertIn(REQUIRED_CONCURRENCY_BLOCK, path.read_text())

        diagnostics = _load_yaml(DIAGNOSTICS_WORKFLOW_PATH)
        self.assertEqual(
            diagnostics["concurrency"]["group"],
            "emergency-production-diagnostics",
        )
        self.assertNotIn(
            PRODUCTION_ECS_MUTATION_GROUP,
            DIAGNOSTICS_WORKFLOW_PATH.read_text(),
        )

        deploy_dev = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        self.assertEqual(
            deploy_dev["concurrency"]["group"],
            "deploy-dev-${{ github.ref }}",
        )
        self.assertNotIn(PRODUCTION_ECS_MUTATION_GROUP, DEPLOY_DEV_WORKFLOW_PATH.read_text())

    def test_setup_docs_document_worker_emergency_rollback(self):
        self.assertTrue(SETUP_DOC_PATH.is_file())
        docs = SETUP_DOC_PATH.read_text()
        self.assertIn("Emergency Production Worker Rollback", docs)
        self.assertIn("prod-emergency-worker-rollback.yml", docs)
        self.assertIn("ai-shipping-labs-worker-prod", docs)
        self.assertIn("ai-shipping-labs-worker-prod:<revision>", docs)
        self.assertIn("--desired-count 1", docs)
        self.assertIn("PRIMARY", docs)
        self.assertIn("runningCount >= desiredCount", docs)
        self.assertIn("https://aishippinglabs.com/ping", docs)
        self.assertIn("Do not treat `https://aishippinglabs.com/ping` as worker proof", docs)
        self.assertIn("production-ecs-mutation", docs)
        self.assertIn("cancel-in-progress: false", docs)
        self.assertIn("Do not reverse migrations", docs)
