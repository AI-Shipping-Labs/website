"""Workflow structure contracts for gated exact-head Coolify deployment."""

from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_DEV = ROOT / ".github" / "workflows" / "deploy-dev.yml"
COOLIFY_COMPAT = ROOT / ".github" / "workflows" / "deploy-coolify.yml"
DOCKERFILE = ROOT / "Dockerfile"
WATCHER = ROOT / "scripts" / "watch-ci.py"
RETIRED_POLLER = ROOT / "deploy" / "coolify" / "hetzner-auto-deploy.sh"


def load(path):
    return yaml.safe_load(path.read_text())


def step(job, name):
    return next(item for item in job["steps"] if item.get("name") == name)


@tag("core")
class GatedCoolifyWorkflowTest(SimpleTestCase):
    def test_deploy_job_keeps_all_eleven_validation_dependencies(self):
        workflow = load(DEPLOY_DEV)
        deploy = workflow["jobs"]["deploy"]

        self.assertEqual(
            deploy["needs"],
            ["checks", "test", "playwright-core", "postgres-verification", "coverage"],
        )
        self.assertEqual(workflow["jobs"]["test"]["strategy"]["matrix"]["shard"], [1, 2, 3, 4])
        self.assertEqual(len(workflow["jobs"]["playwright-core"]["strategy"]["matrix"]["include"]), 4)
        self.assertEqual(deploy["name"], "Deploy to Dev")
        self.assertIn('"Deploy to Dev"', WATCHER.read_text())

    def test_build_and_coolify_writes_exist_only_in_post_gate_job(self):
        workflow = load(DEPLOY_DEV)
        for job_name, job in workflow["jobs"].items():
            serialized = yaml.safe_dump(job)
            if job_name == "deploy":
                self.assertIn("docker/build-push-action@v6", serialized)
                self.assertIn("deploy.coolify.deploy_exact_release", serialized)
                continue
            self.assertNotIn("docker/build-push-action", serialized)
            self.assertNotIn("COOLIFY_TOKEN", serialized)
            self.assertNotIn("deploy_exact_release", serialized)

    def test_deploy_uses_environment_secrets_and_expected_app_variable(self):
        deploy = load(DEPLOY_DEV)["jobs"]["deploy"]
        environment = step(deploy, "Deploy to Dev")["env"]

        self.assertEqual(deploy["env"], {"IMAGE_REPOSITORY": "ghcr.io/ai-shipping-labs/website"})
        self.assertEqual(environment["COOLIFY_URL"], "${{ secrets.COOLIFY_URL }}")
        self.assertEqual(environment["COOLIFY_TOKEN"], "${{ secrets.COOLIFY_TOKEN }}")
        self.assertEqual(
            environment["COOLIFY_AISL_SERVICE_UUID"],
            "${{ secrets.COOLIFY_AISL_SERVICE_UUID }}",
        )
        self.assertEqual(
            environment["COOLIFY_AISL_WEB_APPLICATION_UUID"],
            "${{ vars.COOLIFY_AISL_WEB_APPLICATION_UUID }}",
        )
        self.assertEqual(
            environment["AISL_DEPLOY_VERIFICATION_API_TOKEN"],
            "${{ secrets.AISL_DEPLOY_VERIFICATION_API_TOKEN }}",
        )
        for item in deploy["steps"][:-1]:
            serialized = yaml.safe_dump(item)
            self.assertNotIn("COOLIFY_TOKEN", serialized)
            self.assertNotIn("AISL_DEPLOY_VERIFICATION_API_TOKEN", serialized)

    def test_digest_is_resolved_before_any_coolify_mutation(self):
        deploy = load(DEPLOY_DEV)["jobs"]["deploy"]
        digest_index = deploy["steps"].index(step(deploy, "Verify immutable image digest"))
        mutation_index = deploy["steps"].index(step(deploy, "Deploy to Dev"))
        digest_script = step(deploy, "Verify immutable image digest")["run"]

        self.assertLess(digest_index, mutation_index)
        self.assertIn("imagetools inspect", digest_script)
        self.assertIn('test "${resolved}" = "${EXPECTED_DIGEST}"', digest_script)

    def test_image_bakes_full_runtime_and_oci_revision(self):
        dockerfile = DOCKERFILE.read_text()
        build = step(load(DEPLOY_DEV)["jobs"]["deploy"], "Build and push exact-head image")

        self.assertIn('ARG VERSION=""', dockerfile)
        self.assertIn("ENV VERSION=${VERSION}", dockerfile)
        self.assertIn("LABEL org.opencontainers.image.revision=${VERSION}", dockerfile)
        self.assertIn("VERSION=${{ github.sha }}", build["with"]["build-args"])
        self.assertIn("org.opencontainers.image.revision=${{ github.sha }}", build["with"]["labels"])

    def test_manual_compatibility_workflow_only_dispatches_authoritative_workflow(self):
        workflow = load(COOLIFY_COMPAT)
        request_job = workflow["jobs"]["request-deploy-dev"]
        serialized = yaml.safe_dump(workflow)

        self.assertEqual(len(request_job["steps"]), 1)
        self.assertIn("gh workflow run deploy-dev.yml", request_job["steps"][0]["run"])
        self.assertNotIn("docker/build-push-action", serialized)
        self.assertNotIn("COOLIFY_TOKEN", serialized)
        self.assertNotIn("deploy_exact_release", serialized)
        self.assertNotIn("push:", COOLIFY_COMPAT.read_text())
        self.assertFalse(RETIRED_POLLER.exists())
