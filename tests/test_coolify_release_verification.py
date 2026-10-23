"""Fresh boot and exact deployment evidence contracts."""

from django.test import SimpleTestCase, tag

from deploy.coolify.errors import ReleaseError
from deploy.coolify.verification import (
    application_deployment_uuid,
    boot_evidence_ready,
    exact_deployment_finished,
    source_boot_receipt,
    wait_until,
)
from tests.test_coolify_release_contracts import (
    CUTOFF,
    EXPECTED_WEB_APP_UUID,
    SHA,
    diagnostics,
)


@tag("core")
class BootEvidenceTest(SimpleTestCase):
    def test_accepts_fresh_exact_web_and_worker_boots(self):
        self.assertTrue(boot_evidence_ready(diagnostics(), SHA, CUTOFF))

    def test_missing_wrong_or_pre_cutoff_evidence_is_not_ready(self):
        cases = []
        missing = diagnostics()
        missing["worker"] = None
        cases.append(missing)
        cases.append(diagnostics(worker={"tag": "a" * 7}))
        cases.append(diagnostics(web={"role": "worker"}))
        cases.append(diagnostics(web={"recorded_at": CUTOFF.isoformat()}))
        for payload in cases:
            with self.subTest(payload=payload):
                self.assertFalse(boot_evidence_ready(payload, SHA, CUTOFF))

    def test_rejects_future_malformed_or_nonpositive_evidence(self):
        cases = (
            diagnostics(worker={"recorded_at": "2026-10-23T14:02:00+00:00"}),
            diagnostics(worker={"recorded_at": "not-a-time"}),
            diagnostics(worker={"phases": {"total": 0}}),
            diagnostics(worker={"phases": {}}),
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ReleaseError):
                boot_evidence_ready(payload, SHA, CUTOFF)

    def test_receipt_retains_only_validated_role_identity_and_time(self):
        payload = diagnostics()
        payload["private"] = "must-not-leak"
        payload["web"]["phases"]["private"] = "must-not-leak"
        payload["web"]["recorded_at"] = "2026-10-23T14:00:30Z"
        payload["worker"]["recorded_at"] = "2026-10-23T14:00:30Z"

        receipt = source_boot_receipt(payload, SHA, "2026-10-23T14:00:00Z")

        self.assertEqual(set(receipt), {"cutoff", "roles"})
        self.assertEqual(receipt["cutoff"], "2026-10-23T14:00:00Z")
        self.assertEqual([item["role"] for item in receipt["roles"]], ["web", "worker"])
        for item in receipt["roles"]:
            self.assertEqual(set(item), {"role", "tag", "recorded_at"})
            self.assertEqual(item["tag"], SHA)
            self.assertEqual(item["recorded_at"], "2026-10-23T14:00:30Z")
        self.assertNotIn("must-not-leak", str(receipt))

    def test_receipt_rejects_unready_identity_or_freshness(self):
        missing = diagnostics()
        missing["worker"] = None
        cases = (diagnostics(worker={"tag": "wrong"}), missing)
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaisesMessage(
                ReleaseError, "boot-receipt-evidence-not-ready"
            ):
                source_boot_receipt(payload, SHA, CUTOFF.isoformat())

    def test_receipt_rejects_markdown_timestamp_separators(self):
        payload = diagnostics()
        payload["worker"]["recorded_at"] = "2026-10-23\n14:00:30+00:00"
        with self.assertRaisesMessage(ReleaseError, "invalid-timestamp"):
            source_boot_receipt(payload, SHA, CUTOFF.isoformat())

        with self.assertRaisesMessage(ReleaseError, "invalid-timestamp"):
            source_boot_receipt(diagnostics(), SHA, "2026-10-23`14:00:00+00:00")


@tag("core")
class DeploymentStatusTest(SimpleTestCase):
    def test_application_deploy_response_requires_one_exact_uuid(self):
        valid = {
            "deployments": [
                {"resource_uuid": EXPECTED_WEB_APP_UUID, "deployment_uuid": "deploy-1"}
            ]
        }
        self.assertEqual(
            application_deployment_uuid(valid, EXPECTED_WEB_APP_UUID),
            "deploy-1",
        )
        cases = (
            {},
            {"deployments": []},
            {"deployments": [{"resource_uuid": EXPECTED_WEB_APP_UUID}]},
            {"deployments": [{"resource_uuid": EXPECTED_WEB_APP_UUID, "deployment_uuid": ""}]},
            {"deployments": [{"resource_uuid": "other", "deployment_uuid": "deploy-1"}]},
            {"deployments": [valid["deployments"][0], valid["deployments"][0]]},
        )
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ReleaseError):
                application_deployment_uuid(payload, EXPECTED_WEB_APP_UUID)

    def test_requires_exact_terminal_finished_deployment(self):
        self.assertTrue(
            exact_deployment_finished(
                {"deployment_uuid": "deploy-1", "status": "finished"},
                "deploy-1",
            )
        )
        self.assertFalse(
            exact_deployment_finished(
                {"deployment_uuid": "deploy-1", "status": "queued"},
                "deploy-1",
            )
        )

    def test_rejects_failed_cancelled_unknown_and_wrong_deployment(self):
        for status in ("failed", "cancelled", "cancelled-by-user", "stale"):
            payload = {"deployment_uuid": "deploy-1", "status": status}
            with self.subTest(status=status), self.assertRaises(ReleaseError):
                exact_deployment_finished(payload, "deploy-1")
        with self.assertRaisesMessage(ReleaseError, "identity-mismatch"):
            exact_deployment_finished(
                {"deployment_uuid": "other", "status": "finished"},
                "deploy-1",
            )

    def test_pending_deployment_times_out(self):
        clock_values = iter((0.0, 1.0))
        with self.assertRaisesMessage(ReleaseError, "verification-timeout"):
            wait_until(
                lambda: False,
                timeout=0.5,
                interval=0.1,
                reason="still-queued",
                clock=lambda: next(clock_values),
                sleeper=lambda _seconds: None,
            )
