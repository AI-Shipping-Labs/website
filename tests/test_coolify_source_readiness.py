"""Source rollout diagnostics for Coolify readiness timeouts."""

from unittest.mock import patch

from django.test import SimpleTestCase

from deploy.coolify.contracts import EXPECTED_SERVICE_UUID
from deploy.coolify.errors import ReleaseError
from deploy.coolify.orchestrator import ReleaseDeployer
from tests.test_coolify_release_contracts import (
    CUTOFF,
    IMAGE,
    SHA,
    release_config,
)
from tests.test_coolify_release_orchestrator import FakeCoolify, FakeDiagnostics


class FakeClock:
    def __init__(self):
        self.value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class SourceReadinessWaitTest(SimpleTestCase):
    def _deployer_with_ready_source(self):
        coolify = FakeCoolify()
        diagnostics = FakeDiagnostics()
        deployer = ReleaseDeployer(release_config(), coolify, diagnostics, lambda _url: SHA)
        plan = deployer.preflight()
        coolify.service["docker_compose_raw"] = plan.source_compose
        for application in coolify.service["applications"]:
            application["image"] = IMAGE
            application["status"] = "running:healthy"
        return deployer, plan, coolify, diagnostics

    def _timeout_message(self, deployer, plan):
        with self.assertRaises(ReleaseError) as context:
            deployer._wait_for_source(
                plan,
                CUTOFF,
                CUTOFF.isoformat(),
                timeout=0,
                interval=0,
            )
        message = str(context.exception)
        self.assertIn("last-observed=", message)
        return message

    def _delay_rollout(self, coolify):
        original_call = coolify.call
        service_reads = {"count": 0}

        def delayed_rollout(method, path, payload=None):
            if method == "GET" and path == f"/api/v1/services/{EXPECTED_SERVICE_UUID}":
                service_reads["count"] += 1
                status = "running:starting"
                if service_reads["count"] >= 3:
                    status = "running:healthy"
                for application in coolify.service["applications"]:
                    application["status"] = status
            return original_call(method, path, payload)

        coolify.call = delayed_rollout
        return service_reads

    def test_image_timeout_reports_expected_and_observed_image(self):
        deployer, plan, coolify, _diagnostics = self._deployer_with_ready_source()
        observed_image = "ghcr.io/ai-shipping-labs/website:deploy-122-1-" + "b" * 40
        coolify.service["applications"][0]["image"] = observed_image

        message = self._timeout_message(deployer, plan)

        self.assertIn("reason=source-image-mismatch", message)
        self.assertIn(f"observed={observed_image}", message)
        self.assertIn(f"expected={IMAGE}", message)

    def test_status_timeout_reports_observed_status(self):
        deployer, plan, coolify, _diagnostics = self._deployer_with_ready_source()
        coolify.service["applications"][1]["status"] = "running:starting"

        message = self._timeout_message(deployer, plan)

        self.assertIn("reason=source-status-not-ready", message)
        self.assertIn("status-not-ready role=worker", message)
        self.assertIn("observed=running:starting", message)
        self.assertIn("expected=running:healthy", message)

    def test_missing_boot_evidence_timeout_names_role_and_cutoff(self):
        deployer, plan, _coolify, diagnostics = self._deployer_with_ready_source()
        diagnostics.calls = 1
        diagnostics.current.pop("worker")

        message = self._timeout_message(deployer, plan)

        self.assertIn("reason=source-boot-evidence-missing", message)
        self.assertIn("boot-evidence-missing role=worker", message)
        self.assertIn(f"expected_tag={SHA}", message)
        self.assertIn(f"cutoff={CUTOFF.isoformat()}", message)

    def test_stale_boot_evidence_timeout_reports_recorded_time_and_cutoff(self):
        deployer, plan, _coolify, diagnostics = self._deployer_with_ready_source()
        diagnostics.calls = 1
        diagnostics.current["worker"]["recorded_at"] = "2026-10-23T13:59:00+00:00"

        message = self._timeout_message(deployer, plan)

        self.assertIn("reason=source-boot-evidence-stale", message)
        self.assertIn(f"observed_tag={SHA}", message)
        self.assertIn("recorded_at=2026-10-23T13:59:00+00:00", message)
        self.assertIn(f"cutoff={CUTOFF.isoformat()}", message)

    def test_boot_evidence_for_another_tag_reports_observed_and_expected_tags(self):
        deployer, plan, _coolify, diagnostics = self._deployer_with_ready_source()
        diagnostics.calls = 1
        diagnostics.current["worker"]["tag"] = "b" * 40

        message = self._timeout_message(deployer, plan)

        self.assertIn("reason=source-boot-evidence-not-current", message)
        self.assertIn("boot-evidence-not-current role=worker", message)
        self.assertIn(f"observed_tag={'b' * 40}", message)
        self.assertIn(f"expected_tag={SHA}", message)
        self.assertIn("recorded_at=2026-10-23T14:00:30+00:00", message)
        self.assertIn(f"cutoff={CUTOFF.isoformat()}", message)

    def test_token_shaped_image_and_status_are_redacted(self):
        deployer, plan, coolify, _diagnostics = self._deployer_with_ready_source()
        coolify.service["applications"][0]["image"] = (
            "ghcr.io/ai-shipping-labs/website:AKIA1234567890123456"
        )
        coolify.service["applications"][0]["status"] = "ghp_" + "x" * 36

        with self.assertLogs("deploy.coolify.orchestrator", level="INFO") as logs:
            message = self._timeout_message(deployer, plan)

        output = "\n".join(logs.output) + message
        self.assertNotIn("AKIA1234567890123456", output)
        self.assertNotIn("ghp_" + "x" * 36, output)
        self.assertIn("observed=<redacted>", output)

    def test_rollout_that_becomes_ready_late_logs_state_transitions(self):
        deployer, plan, coolify, diagnostics = self._deployer_with_ready_source()
        diagnostics.calls = 1
        service_reads = self._delay_rollout(coolify)
        clock = FakeClock()
        with patch("deploy.coolify.verification.time.monotonic", side_effect=clock.now):
            with patch("deploy.coolify.verification.time.sleep", side_effect=clock.sleep):
                with self.assertLogs("deploy.coolify.orchestrator", level="INFO") as logs:
                    receipt = deployer._wait_for_source(
                        plan,
                        CUTOFF,
                        CUTOFF.isoformat(),
                        timeout=1,
                        interval=0.4,
                    )

        self.assertEqual(service_reads["count"], 3)
        self.assertEqual(clock.value, 0.8)
        self.assertEqual([item["tag"] for item in receipt["roles"]], [SHA, SHA])
        self.assertEqual(len(logs.output), 2)
        self.assertIn("status-not-ready", logs.output[0])
        self.assertIn("state=ready", logs.output[1])
