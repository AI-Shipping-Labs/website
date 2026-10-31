"""Production workflow keeps confirmation and uses the current release owner."""

import subprocess
from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/deploy-prod.yml"
REPO_ROOT = WORKFLOW.parents[2]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
COOLIFY_DEPLOY_DOC = REPO_ROOT / "_docs" / "coolify-deployment.md"
SETUP_DOC = REPO_ROOT / "_docs" / "setup.md"
RETIRED_EMERGENCY_WORKFLOWS = (
    "prod-emergency-web-rollback.yml",
    "prod-emergency-worker-rollback.yml",
    "prod-emergency-diagnostics.yml",
)


@tag("core")
class ProductionWorkflowTest(SimpleTestCase):
    def test_confirmation_precedes_checkout_credentials_and_evidence_requests(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.assertEqual(
            workflow["concurrency"],
            {"group": "production-coolify-attestation", "cancel-in-progress": False},
        )
        job = workflow["jobs"]["deploy"]
        first = job["steps"][0]
        self.assertEqual(first["env"]["CONFIRM_DEPLOY"], "${{ inputs.confirmProdDeploy }}")
        for value, expected in (("false", 1), ("true", 0)):
            result = subprocess.run(["bash", "-c", first["run"]], env={"CONFIRM_DEPLOY": value}, check=False)
            self.assertEqual(result.returncode, expected)
        self.assertEqual(job["environment"], "coolify-staging")
        self.assertEqual(job["permissions"], {"contents": "read", "packages": "read", "actions": "read"})
        helper = next(step for step in job["steps"] if step["name"] == "Attest green DEV release in production")
        self.assertEqual(helper["run"], "uv run python -m deploy.coolify.attest_production")
        self.assertEqual(helper["env"]["REQUESTED_TAG"], "${{ inputs.deployTag }}")

    def test_no_retired_runtime_or_unnecessary_write_is_invoked(self):
        text = WORKFLOW.read_text()
        for retired in (
            "aws-actions/",
            "aws ecs",
            "deploy/deploy_prod.sh",
            "git push",
            ".prod-versions",
            "predeployMigrateCheck",
            "production-ecs-mutation",
        ):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)
        self.assertIn("confirmProdDeploy:", text)
        self.assertIn("default: false", text)

    def test_retired_ecs_emergency_workflows_are_removed_and_coolify_runbook_exists(self):
        for name in RETIRED_EMERGENCY_WORKFLOWS:
            with self.subTest(workflow=name):
                self.assertFalse((WORKFLOWS_DIR / name).exists())

        coolify_doc = COOLIFY_DEPLOY_DOC.read_text(encoding="utf-8")
        for marker in (
            "## Emergency diagnostics",
            "## Emergency rollback",
            "deploy/coolify/verify_dual_runtime.py",
            "deploy.coolify.deploy_exact_release",
            "/api/diagnostics/boot-timing",
            "Do not treat `https://aishippinglabs.com/ping` alone as worker recovery proof",
        ):
            with self.subTest(coolify_marker=marker):
                self.assertIn(marker, coolify_doc)

        setup_doc = SETUP_DOC.read_text(encoding="utf-8")
        for marker in (
            "aisl-staging",
            "aishl-production-rolling-web",
            "ghcr.io/ai-shipping-labs/website",
            "production-coolify-attestation",
            "deploy/coolify/verify_dual_runtime.py",
            "deploy.coolify.deploy_exact_release",
        ):
            with self.subTest(setup_marker=marker):
                self.assertIn(marker, setup_doc)
        self.assertNotIn("production-ecs-mutation", setup_doc)
        self.assertNotIn("387546586013.dkr.ecr.eu-west-1.amazonaws.com", setup_doc)
