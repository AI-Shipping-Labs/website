"""Executed offline orchestration tests for the Coolify release helper."""

import base64
import copy

from django.test import SimpleTestCase, tag

from deploy.coolify.contracts import (
    EXPECTED_SERVICE_NAME,
    EXPECTED_SERVICE_UUID,
    EXPECTED_WEB_APP_NAME,
    EXPECTED_WEB_APP_UUID,
)
from deploy.coolify.errors import ReleaseError
from deploy.coolify.orchestrator import ReleaseDeployer
from deploy.coolify.topology import WEB_APPLICATION_CONTRACT, WEB_ENVIRONMENT
from deploy.coolify.verification import wait_for_consecutive
from tests.test_coolify_release_contracts import (
    IMAGE,
    SHA,
    diagnostics,
    release_config,
    source_service,
)


class FakeDiagnostics:
    def __init__(self):
        self.calls = 0
        self.current = diagnostics()

    def call(self, method, path):
        self.calls += 1
        if self.calls == 1:
            payload = diagnostics()
            payload["generated_at"] = "2026-10-23T14:00:00+00:00"
            payload["web"]["recorded_at"] = "2026-10-23T13:59:00+00:00"
            payload["worker"]["recorded_at"] = "2026-10-23T13:59:00+00:00"
            return payload
        return copy.deepcopy(self.current)


def overwrite_diagnostics_on_web_patch(coolify, diagnostic_client):
    original = coolify._app_call

    def overwriting_call(method, payload):
        result = original(method, payload)
        if method == "PATCH":
            diagnostic_client.current["web"]["tag"] = "rolling-web-overwrite"
        return result

    coolify._app_call = overwriting_call


def fake_web_application():
    return {
        "uuid": EXPECTED_WEB_APP_UUID,
        "name": EXPECTED_WEB_APP_NAME,
        "docker_registry_image_tag": "old",
        "status": "running:healthy",
        "config_hash": "web-config-1",
        "health_check_path": "/ping",
        "ports_exposes": "8000",
        **WEB_APPLICATION_CONTRACT,
    }


def fake_web_environment():
    envs = []
    for key, value in WEB_ENVIRONMENT.items():
        envs.append({"key": key, "value": value, "is_preview": False})
    return envs


def fake_deployment_history():
    return {
        "count": 11,
        "deployments": [
            {
                "application_id": "2",
                "application_name": EXPECTED_WEB_APP_NAME,
                "status": "finished",
            }
        ],
    }


class FakeCoolify:
    def __init__(self):
        self.service = source_service()
        self.service.update(
            uuid=EXPECTED_SERVICE_UUID,
            name=EXPECTED_SERVICE_NAME,
            config_hash="source-config-1",
        )
        self.app = fake_web_application()
        self.source_envs = []
        self.app_envs = fake_web_environment()
        self.deployment_history = fake_deployment_history()
        self.active_deployments = []
        self.service_gets = 0
        self.source_drift_at = None
        self.source_drift_kind = None
        self.calls = []

    def call(self, method, path, payload=None):
        self.calls.append((method, path))
        if path == f"/api/v1/services/{EXPECTED_SERVICE_UUID}":
            return self._service_call(method, payload)
        if path == f"/api/v1/services/{EXPECTED_SERVICE_UUID}/envs":
            return copy.deepcopy(self.source_envs)
        if path == f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}":
            return self._app_call(method, payload)
        if path == f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}/envs":
            return copy.deepcopy(self.app_envs)
        if path.startswith("/api/v1/deployments/applications/"):
            return copy.deepcopy(self.deployment_history)
        if path == "/api/v1/deployments":
            return copy.deepcopy(self.active_deployments)
        if path == "/api/v1/deployments/deploy-1":
            return {
                "deployment_uuid": "deploy-1",
                "status": getattr(self, "deployment_status", "finished"),
            }
        if path.startswith("/api/v1/deploy?"):
            return self._deploy_call(path)
        raise AssertionError((method, path, payload))

    def _service_call(self, method, payload):
        if method == "PATCH":
            raw = base64.b64decode(payload["docker_compose_raw"]).decode()
            self.service["docker_compose_raw"] = raw
            for item in self.service["applications"]:
                item["image"] = IMAGE
                item["status"] = "running:healthy"
        result = copy.deepcopy(self.service)
        if method == "GET":
            self.service_gets += 1
            self._apply_source_drift(result)
        return result

    def _apply_source_drift(self, service):
        if self.service_gets != self.source_drift_at:
            return
        if self.source_drift_kind == "missing":
            for index, item in enumerate(service["applications"]):
                if item["name"] == "worker":
                    service["applications"].pop(index)
                    break
            return
        for item in service["applications"]:
            if item["name"] != "worker":
                continue
            if self.source_drift_kind == "unhealthy":
                item["status"] = "running:unhealthy"
            if self.source_drift_kind == "wrong-image":
                item["image"] = "ghcr.io/ai-shipping-labs/website:stale"

    def _app_call(self, method, payload):
        if method == "PATCH":
            self.app.update(payload)
        return copy.deepcopy(self.app)

    def _deploy_call(self, path):
        if EXPECTED_SERVICE_UUID in path:
            return {
                "deployments": [
                    {"resource_uuid": EXPECTED_SERVICE_UUID, "message": "queued"}
                ]
            }
        return {
            "deployments": [
                {
                    "resource_uuid": EXPECTED_WEB_APP_UUID,
                    "deployment_uuid": "deploy-1",
                    "message": "queued",
                }
            ]
        }


@tag("core")
class ReleaseOrchestrationTest(SimpleTestCase):
    def _assert_source_receipt(self, result):
        receipt = result.source_receipt
        self.assertEqual(receipt["cutoff"], "2026-10-23T14:00:00+00:00")
        self.assertEqual([item["role"] for item in receipt["roles"]], ["web", "worker"])
        self.assertEqual([item["tag"] for item in receipt["roles"]], [SHA, SHA])

    def test_success_proves_source_before_trackable_public_web_deploy(self):
        coolify = FakeCoolify()
        diagnostic_client = FakeDiagnostics()
        overwrite_diagnostics_on_web_patch(coolify, diagnostic_client)
        deployer = ReleaseDeployer(
            release_config(),
            coolify,
            diagnostic_client,
            lambda _url: SHA,
        )

        result = deployer.deploy(timeout=1, interval=0.01)

        self.assertEqual(result.deployment_uuid, "deploy-1")
        self._assert_source_receipt(result)
        source_deploy = coolify.calls.index(
            ("POST", f"/api/v1/deploy?uuid={EXPECTED_SERVICE_UUID}&force=true")
        )
        app_patch = coolify.calls.index(
            ("PATCH", f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}")
        )
        self.assertLess(source_deploy, app_patch)
        self.assertGreaterEqual(diagnostic_client.calls, 2)
        self.assertEqual(coolify.app["docker_registry_image_tag"], IMAGE.rsplit(":", 1)[1])

    def test_concurrent_source_compose_edit_blocks_first_mutation(self):
        coolify = FakeCoolify()
        original_call = coolify.call
        service_gets = 0

        def drifting_call(method, path, payload=None):
            nonlocal service_gets
            result = original_call(method, path, payload)
            if method == "GET" and path == f"/api/v1/services/{EXPECTED_SERVICE_UUID}":
                service_gets += 1
                if service_gets == 2:
                    result["docker_compose_raw"] += "\n# concurrent\n"
            return result

        coolify.call = drifting_call
        deployer = ReleaseDeployer(
            release_config(),
            coolify,
            FakeDiagnostics(),
            lambda _url: SHA,
        )

        with self.assertRaisesMessage(ReleaseError, "concurrent-source-compose-edit"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertNotIn("PATCH", [method for method, _path in coolify.calls])

    def test_missing_worker_health_never_reaches_public_application(self):
        coolify = FakeCoolify()
        original_service_call = coolify._service_call

        def unhealthy_worker(method, payload):
            service = original_service_call(method, payload)
            for app in service["applications"]:
                if app["name"] == "worker":
                    app["status"] = "running:unhealthy"
            return service

        coolify._service_call = unhealthy_worker
        deployer = ReleaseDeployer(
            release_config(),
            coolify,
            FakeDiagnostics(),
            lambda _url: SHA,
        )

        with self.assertRaisesMessage(ReleaseError, "source-runtime-not-exact"):
            deployer.deploy(timeout=0.001, interval=0.001)
        self.assertNotIn(
            ("PATCH", f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}"),
            coolify.calls,
        )

    def test_failed_or_timed_out_deployment_never_reads_public_ping(self):
        for status in ("failed", "queued"):
            coolify = FakeCoolify()
            coolify.deployment_status = status
            ping_calls = []
            deployer = ReleaseDeployer(
                release_config(),
                coolify,
                FakeDiagnostics(),
                lambda url: ping_calls.append(url),
            )
            with self.subTest(status=status), self.assertRaises(ReleaseError):
                deployer.deploy(timeout=0.001, interval=0.001)
            self.assertEqual(ping_calls, [])

    def test_ping_mismatch_never_counts_as_consecutive_success(self):
        clock_values = iter((0.0, 1.0))
        with self.assertRaisesMessage(ReleaseError, "public-release-not-consecutive"):
            wait_for_consecutive(
                lambda: False,
                required=3,
                timeout=0.5,
                interval=0.1,
                clock=lambda: next(clock_values),
                sleeper=lambda _seconds: None,
            )
