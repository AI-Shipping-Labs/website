"""Production workflow keeps confirmation and uses the current release owner."""

import subprocess
from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

WORKFLOW = Path(__file__).resolve().parent.parent / ".github/workflows/deploy-prod.yml"


@tag("core")
class ProductionWorkflowTest(SimpleTestCase):
    def test_confirmation_precedes_checkout_credentials_and_evidence_requests(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
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
        for retired in ("aws-actions/", "aws ecs", "deploy/deploy_prod.sh", "git push", ".prod-versions", "predeployMigrateCheck"):
            with self.subTest(retired=retired):
                self.assertNotIn(retired, text)
        self.assertIn("confirmProdDeploy:", text)
        self.assertIn("default: false", text)
