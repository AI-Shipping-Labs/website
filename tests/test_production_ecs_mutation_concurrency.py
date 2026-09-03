from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DEPLOY_PROD_WORKFLOW_PATH = WORKFLOWS_DIR / "deploy-prod.yml"
WEB_ROLLBACK_WORKFLOW_PATH = WORKFLOWS_DIR / "prod-emergency-web-rollback.yml"
DIAGNOSTICS_WORKFLOW_PATH = WORKFLOWS_DIR / "prod-emergency-diagnostics.yml"
DEPLOY_DEV_WORKFLOW_PATH = WORKFLOWS_DIR / "deploy-dev.yml"
PRODUCTION_ECS_MUTATION_GROUP = "production-ecs-mutation"
REQUIRED_CONCURRENCY_BLOCK = (
    "concurrency:\n"
    "  group: production-ecs-mutation\n"
    "  cancel-in-progress: false\n"
)


def _load_yaml(path):
    return yaml.safe_load(path.read_text())


def _production_mutation_workflow_paths():
    names = {"deploy-prod.yml", "prod-emergency-web-rollback.yml"}
    for path in WORKFLOWS_DIR.glob("*.yml"):
        lowered = path.name.lower()
        if "rollback" in lowered and (
            lowered.startswith("prod-emergency") or "worker" in lowered
        ):
            names.add(path.name)
    return [WORKFLOWS_DIR / name for name in sorted(names)]


@tag("core")
class ProductionEcsMutationConcurrencyTest(SimpleTestCase):
    def test_mutation_workflows_share_non_cancelling_production_group(self):
        mutation_paths = _production_mutation_workflow_paths()
        self.assertIn(DEPLOY_PROD_WORKFLOW_PATH, mutation_paths)
        self.assertIn(WEB_ROLLBACK_WORKFLOW_PATH, mutation_paths)

        for path in mutation_paths:
            with self.subTest(path=path.name):
                workflow = _load_yaml(path)
                concurrency = workflow.get("concurrency")
                self.assertIsInstance(concurrency, dict)
                self.assertEqual(
                    concurrency.get("group"),
                    PRODUCTION_ECS_MUTATION_GROUP,
                )
                self.assertIn(
                    "cancel-in-progress",
                    concurrency,
                    msg=f"{path.name} omitted cancel-in-progress (GitHub defaults to true)",
                )
                self.assertIs(concurrency["cancel-in-progress"], False)
                self.assertIn(REQUIRED_CONCURRENCY_BLOCK, path.read_text())

        groups = {
            path.name: _load_yaml(path)["concurrency"]["group"]
            for path in mutation_paths
        }
        self.assertEqual(set(groups.values()), {PRODUCTION_ECS_MUTATION_GROUP})

    def test_non_mutation_workflows_do_not_join_the_production_group(self):
        mutation_names = {path.name for path in _production_mutation_workflow_paths()}
        for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
            workflow = _load_yaml(path)
            group = (workflow.get("concurrency") or {}).get("group")
            if path.name in mutation_names:
                continue
            with self.subTest(path=path.name):
                self.assertNotEqual(group, PRODUCTION_ECS_MUTATION_GROUP)

        diagnostics = _load_yaml(DIAGNOSTICS_WORKFLOW_PATH)
        self.assertEqual(
            diagnostics["concurrency"]["group"],
            "emergency-production-diagnostics",
        )

        deploy_dev = _load_yaml(DEPLOY_DEV_WORKFLOW_PATH)
        self.assertEqual(
            deploy_dev["concurrency"]["group"],
            "deploy-dev-${{ github.ref }}",
        )
        self.assertIs(deploy_dev["concurrency"]["cancel-in-progress"], True)
        self.assertNotIn(PRODUCTION_ECS_MUTATION_GROUP, DEPLOY_DEV_WORKFLOW_PATH.read_text())

    def test_web_rollback_no_longer_uses_a_private_concurrency_group(self):
        rollback_text = WEB_ROLLBACK_WORKFLOW_PATH.read_text()
        self.assertNotIn("emergency-production-web-rollback", rollback_text)

    def test_web_rollback_rereads_live_service_before_update_service(self):
        workflow = _load_yaml(WEB_ROLLBACK_WORKFLOW_PATH)
        rollback_job = workflow["jobs"]["rollback"]
        mutation_step = next(
            step
            for step in rollback_job["steps"]
            if step.get("name") == "Roll back production web service only"
        )
        run = mutation_step["run"]
        describe_index = run.index("aws ecs describe-services")
        update_index = run.index("aws ecs update-service")
        self.assertLess(describe_index, update_index)
        self.assertIn("deployments[?status=='PRIMARY']", run)
        self.assertIn("deployments[?status=='ACTIVE']", run)
        self.assertIn("not calling update-service", run)
        self.assertLess(
            run.index("deployments[?status=='ACTIVE']"),
            update_index,
        )
