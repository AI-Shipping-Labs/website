"""Executed offline evidence and no-mutation checks for production confirmation."""

import copy
import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, tag

from deploy.coolify import attest_production as attestation
from deploy.coolify import production_identity as identity
from deploy.coolify.errors import ReleaseError
from tests.test_coolify_release_api import RELEASE_ENVIRONMENT
from tests.test_coolify_release_contracts import DIGEST, IMAGE, SHA, release_config
from tests.test_coolify_release_orchestrator import FakeCoolify, FakeDiagnostics


def green_jobs():
    jobs = []
    for index, name in enumerate(sorted(identity.REQUIRED_JOBS), start=1):
        jobs.append({"id": index, "name": name, "status": "completed", "conclusion": "success"})
    return {"total_count": len(jobs), "jobs": jobs}


def green_run():
    return {
        "id": 123, "run_attempt": 1, "head_sha": SHA, "head_branch": "main",
        "path": ".github/workflows/deploy-dev.yml", "status": "completed",
        "conclusion": "success", "event": "push",
    }


def deployed_coolify():
    coolify = FakeCoolify()
    coolify.app.update(docker_registry_image_name=IMAGE.split(":")[0], docker_registry_image_tag=IMAGE.split(":")[1])
    for item in coolify.service["applications"]:
        item.update(image=IMAGE, status="running:healthy")
    deployer = attestation.ReleaseDeployer(release_config(), coolify, FakeDiagnostics(), lambda _url: SHA)
    coolify.service["docker_compose_raw"] = deployer.preflight().source_compose
    coolify.calls.clear()
    return coolify


@tag("core")
class ProductionIdentityTest(SimpleTestCase):
    def test_accepts_only_default_or_exact_current_release(self):
        release_tag = IMAGE.rsplit(":", 1)[1]
        for requested in ("", SHA, release_tag):
            self.assertEqual(identity.release_attempt(release_tag, SHA, requested), (123, 1))
        for release_tag, sha, requested in (("latest", SHA, ""), (release_tag, "b" * 40, ""), (release_tag, SHA, "stale")):
            with self.subTest(tag=release_tag, sha=sha, requested=requested):
                with self.assertRaises(ReleaseError):
                    identity.release_attempt(release_tag, sha, requested)

    def test_requires_all_twelve_successful_jobs_and_exact_run(self):
        github = Mock()
        github.call.side_effect = [green_run(), green_jobs()]
        deploy_id = identity.require_green_run(github, 123, 1, SHA)
        self.assertEqual(deploy_id, sorted(identity.REQUIRED_JOBS).index("Deploy to Dev") + 1)
        self.assertIn("/attempts/1/jobs?per_page=100", github.call.call_args.args[1])

    def test_rejects_wrong_run_source_identity_or_status_before_jobs(self):
        cases = {
            "head_sha": "b" * 40, "head_branch": "feature", "path": ".github/workflows/ci.yml",
            "id": 124, "run_attempt": 2, "status": "in_progress", "conclusion": "failure", "event": "pull_request",
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                run = green_run()
                run[field] = value
                github = Mock()
                github.call.return_value = run
                with self.assertRaises(ReleaseError):
                    identity.require_green_run(github, 123, 1, SHA)
                self.assertEqual(github.call.call_count, 1)

    def test_skipped_failed_missing_or_duplicate_job_cannot_pass(self):
        payloads = []
        for conclusion in ("skipped", "cancelled", "failure"):
            payload = green_jobs()
            payload["jobs"][0]["conclusion"] = conclusion
            payloads.append(payload)
        missing = green_jobs()
        missing["jobs"].pop()
        payloads.append(missing)
        duplicate = green_jobs()
        duplicate["jobs"][0] = copy.deepcopy(duplicate["jobs"][1])
        payloads.append(duplicate)
        for payload in payloads:
            with self.assertRaises(ReleaseError):
                identity.require_green_jobs(payload)

    def test_registry_tag_must_match_digest_recorded_by_green_deploy(self):
        receipt = f"Verified exact-head Coolify release sha={SHA} digest={DIGEST} deployment=release-1"
        with patch.object(identity, "command_output", side_effect=[receipt, json.dumps(DIGEST)]):
            recorded = identity.recorded_digest(3, SHA)
            identity.require_registry_digest(IMAGE, recorded)
        with patch.object(identity, "command_output", return_value=json.dumps("sha256:" + "c" * 64)):
            with self.assertRaisesMessage(ReleaseError, "differs-from-green-deployment"):
                identity.require_registry_digest(IMAGE, DIGEST)

    def test_missing_duplicate_or_wrong_revision_receipts_fail(self):
        receipt = f"Verified exact-head Coolify release sha={SHA} digest={DIGEST} deployment=x"
        for output in ("", receipt + receipt, receipt.replace(SHA, "b" * 40)):
            with patch.object(identity, "command_output", return_value=output):
                with self.assertRaises(ReleaseError):
                    identity.recorded_digest(3, SHA)

    def test_command_failure_never_exposes_captured_secret(self):
        error = subprocess.CalledProcessError(1, ["gh"], output="secret", stderr="secret")
        with patch.object(identity.subprocess, "run", side_effect=error):
            with self.assertRaisesMessage(ReleaseError, "release-evidence-command-failed") as raised:
                identity.command_output(["gh", "api"])
        self.assertNotIn("secret", str(raised.exception))


@tag("core")
class ProductionRuntimeAttestationTest(SimpleTestCase):
    def test_current_healthy_dual_release_is_attested_without_mutation(self):
        coolify = deployed_coolify()
        diagnostics = FakeDiagnostics()
        with (
            patch.object(attestation, "JsonClient", return_value=diagnostics),
            patch.object(attestation, "get_text", return_value=SHA) as ping,
        ):
            attestation.attest_runtime(release_config(), coolify)
        self.assertEqual(ping.call_count, 6)
        self.assertEqual({call.args[0] for call in ping.call_args_list}, {
            "https://aishippinglabs.com/ping", "https://dev.aishippinglabs.com/ping",
        })
        self.assertTrue(coolify.calls)
        self.assertEqual({method for method, _path in coolify.calls}, {"GET"})

    def test_stale_worker_public_app_boot_or_hostname_fails_without_mutation(self):
        for fault in ("worker", "app", "boot", "dev", "prod"):
            with self.subTest(fault=fault):
                coolify = deployed_coolify()
                diagnostics = FakeDiagnostics()
                if fault == "worker":
                    coolify.service["applications"][1]["image"] = "stale"
                if fault == "app":
                    coolify.app["docker_registry_image_tag"] = "stale"
                if fault == "boot":
                    diagnostics.current["worker"]["tag"] = "stale"
                    diagnostics.calls = 1

                def ping(url):
                    if fault == "dev" and "dev." in url:
                        return "stale"
                    if fault == "prod" and "dev." not in url:
                        return "stale"
                    return SHA

                with patch.object(attestation, "JsonClient", return_value=diagnostics), patch.object(attestation, "get_text", side_effect=ping):
                    with self.assertRaises(ReleaseError):
                        attestation.attest_runtime(release_config(), coolify)
                self.assertEqual({method for method, _path in coolify.calls}, {"GET"})

    def test_entrypoint_joins_green_run_digest_and_live_runtime_evidence(self):
        coolify = deployed_coolify()
        github = Mock()
        github.call.side_effect = [green_run(), green_jobs()]
        diagnostics = FakeDiagnostics()
        receipt = f"Verified exact-head Coolify release sha={SHA} digest={DIGEST} deployment=x"
        with (
            patch.dict("os.environ", {**RELEASE_ENVIRONMENT, "GH_TOKEN": "github", "REQUESTED_TAG": ""}, clear=True),
            patch.object(attestation, "JsonClient", side_effect=[coolify, github, diagnostics]),
            patch.object(attestation, "get_text", return_value=SHA),
            patch.object(identity, "command_output", side_effect=[receipt, json.dumps(DIGEST)]),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(attestation.main(), 0)
        self.assertIn(DIGEST, output.getvalue())
        self.assertIn("all 12 jobs", output.getvalue())
        self.assertNotIn("github", output.getvalue())
        self.assertEqual({method for method, _path in coolify.calls}, {"GET"})

    def test_unexpected_boundary_error_is_redacted(self):
        with patch.object(attestation, "run", side_effect=ValueError("private-secret")), redirect_stderr(io.StringIO()) as output:
            self.assertEqual(attestation.main(), 1)
        self.assertIn("unexpected-ValueError", output.getvalue())
        self.assertNotIn("private-secret", output.getvalue())
