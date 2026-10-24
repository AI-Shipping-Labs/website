"""Concurrent-edit and identity guards for the Coolify release helper."""

from django.test import SimpleTestCase, tag

from deploy.coolify.contracts import EXPECTED_SERVICE_UUID, EXPECTED_WEB_APP_UUID
from deploy.coolify.errors import ReleaseError
from deploy.coolify.orchestrator import ReleaseDeployer
from tests.test_coolify_release_contracts import SHA, release_config, source_service
from tests.test_coolify_release_orchestrator import FakeCoolify, FakeDiagnostics


@tag("core")
class ReleaseConcurrencyTest(SimpleTestCase):
    def test_worker_unhealthy_at_promotion_blocks_public_mutation(self):
        coolify = FakeCoolify()
        coolify.source_drift_at = 5
        coolify.source_drift_kind = "unhealthy"
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), lambda _url: SHA
        )

        with self.assertRaisesMessage(ReleaseError, "source-runtime-not-exact"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertNotIn(
            ("PATCH", f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}"),
            coolify.calls,
        )

    def test_worker_missing_during_web_deploy_blocks_public_proof(self):
        coolify = FakeCoolify()
        coolify.source_drift_at = 6
        coolify.source_drift_kind = "missing"
        ping_calls = []
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), ping_calls.append
        )

        with self.assertRaisesMessage(ReleaseError, "runtime-application-inventory"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertEqual(ping_calls, [])

    def test_wrong_worker_image_blocks_final_consecutive_proof(self):
        coolify = FakeCoolify()
        coolify.source_drift_at = 9
        coolify.source_drift_kind = "wrong-image"
        ping_calls = []
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), ping_calls.append
        )

        with self.assertRaisesMessage(ReleaseError, "source-runtime-not-exact"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertEqual(len(ping_calls), 2)

    def test_worker_loss_during_last_dev_ping_fails_terminal_health_check(self):
        coolify = FakeCoolify()
        ping_calls = []

        def ping_reader(url):
            ping_calls.append(url)
            if len(ping_calls) == 6:
                for item in coolify.service["applications"]:
                    if item["name"] == "worker":
                        item["status"] = "running:unhealthy"
            return SHA

        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), ping_reader
        )
        with self.assertRaisesMessage(ReleaseError, "source-runtime-not-exact"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertEqual(len(ping_calls), 6)

    def test_source_compose_edit_during_wait_blocks_public_application(self):
        coolify = FakeCoolify()
        original_call = coolify.call
        service_gets = 0

        def drifting_call(method, path, payload=None):
            nonlocal service_gets
            if method == "GET" and path == f"/api/v1/services/{EXPECTED_SERVICE_UUID}":
                service_gets += 1
                if service_gets == 4:
                    compose = source_service()["docker_compose_raw"]
                    coolify.service["docker_compose_raw"] = compose
            return original_call(method, path, payload)

        coolify.call = drifting_call
        deployer = ReleaseDeployer(
            release_config(),
            coolify,
            FakeDiagnostics(),
            lambda _url: SHA,
        )

        with self.assertRaisesMessage(ReleaseError, "compose-persistence-mismatch"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertNotIn(
            ("PATCH", f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}"),
            coolify.calls,
        )

    def test_source_environment_edit_during_wait_blocks_public_application(self):
        coolify = FakeCoolify()
        original_call = coolify.call
        env_gets = 0

        def drifting_call(method, path, payload=None):
            nonlocal env_gets
            source_env_path = f"/api/v1/services/{EXPECTED_SERVICE_UUID}/envs"
            if method == "GET" and path == source_env_path:
                env_gets += 1
                if env_gets == 4:
                    coolify.source_envs.append({"key": "DRIFT", "value": "1"})
            return original_call(method, path, payload)

        coolify.call = drifting_call
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), lambda _url: SHA
        )

        with self.assertRaisesMessage(ReleaseError, "source-environment-edit"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertNotIn(
            ("PATCH", f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}"),
            coolify.calls,
        )

    def test_source_environment_edit_before_patch_blocks_first_mutation(self):
        coolify = FakeCoolify()
        original_call = coolify.call
        env_gets = 0

        def drifting_call(method, path, payload=None):
            nonlocal env_gets
            source_env_path = f"/api/v1/services/{EXPECTED_SERVICE_UUID}/envs"
            if method == "GET" and path == source_env_path:
                env_gets += 1
                if env_gets == 2:
                    coolify.source_envs.append({"key": "DRIFT", "value": "1"})
            return original_call(method, path, payload)

        coolify.call = drifting_call
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), lambda _url: SHA
        )

        with self.assertRaisesMessage(ReleaseError, "source-environment-edit"):
            deployer.deploy(timeout=1, interval=0.01)
        self.assertNotIn("PATCH", [method for method, _path in coolify.calls])

    def test_web_runtime_edit_after_patch_blocks_terminal_or_ping_proof(self):
        for drift_get in (4, 5):
            coolify = FakeCoolify()
            original_call = coolify.call
            app_gets = 0
            ping_calls = []

            def drifting_call(method, path, payload=None):
                nonlocal app_gets
                if method == "GET" and path == f"/api/v1/applications/{EXPECTED_WEB_APP_UUID}":
                    app_gets += 1
                    if app_gets == drift_get:
                        coolify.app["health_check_path"] = "/changed"
                return original_call(method, path, payload)

            def ping_reader(url):
                ping_calls.append(url)
                return SHA

            coolify.call = drifting_call
            deployer = ReleaseDeployer(
                release_config(),
                coolify,
                FakeDiagnostics(),
                ping_reader,
            )
            with self.subTest(drift_get=drift_get), self.assertRaises(ReleaseError):
                deployer.deploy(timeout=1, interval=0.01)
            self.assertEqual(ping_calls, [])

    def test_service_and_application_body_identity_mismatch_block_mutation(self):
        cases = (
            ("service", "name"),
            ("service", "uuid"),
            ("application", "name"),
            ("application", "uuid"),
        )
        for owner, field in cases:
            coolify = FakeCoolify()
            if owner == "service":
                coolify.service[field] = "other"
            else:
                coolify.app[field] = "other"
            deployer = ReleaseDeployer(
                release_config(),
                coolify,
                FakeDiagnostics(),
                lambda _url: SHA,
            )

            with self.subTest(owner=owner, field=field), self.assertRaises(ReleaseError):
                deployer.deploy(timeout=1, interval=0.01)
            methods = []
            for method, _path in coolify.calls:
                methods.append(method)
            self.assertNotIn("PATCH", methods)


    def test_active_deployment_older_than_history_page_blocks_mutation(self):
        coolify = FakeCoolify()
        coolify.active_deployments = [
            {
                "application_id": "2",
                "application_name": "aishl-production-rolling-web",
                "status": "queued",
            }
        ]
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), lambda _url: SHA
        )

        with self.assertRaisesMessage(ReleaseError, "concurrent-web-deployment"):
            deployer.deploy(timeout=1, interval=0.01)

        self.assertNotIn("PATCH", [method for method, _path in coolify.calls])

    def test_deployment_inventory_shape_or_identity_mismatch_fails_closed(self):
        cases = (
            {"count": 0, "deployments": []},
            {"count": True, "deployments": [{}]},
            {"count": 11, "deployments": []},
            {
                "count": 1,
                "deployments": [
                    {"application_id": "2", "application_name": "other"}
                ],
            },
        )
        for history in cases:
            coolify = FakeCoolify()
            coolify.deployment_history = history
            deployer = ReleaseDeployer(
                release_config(), coolify, FakeDiagnostics(), lambda _url: SHA
            )

            with self.subTest(history=history), self.assertRaises(ReleaseError):
                deployer.deploy(timeout=1, interval=0.01)
            self.assertNotIn("PATCH", [method for method, _path in coolify.calls])

    def test_drifted_web_runtime_baseline_blocks_all_mutation(self):
        coolify = FakeCoolify()
        coolify.app["health_check_path"] = "/wrong"
        deployer = ReleaseDeployer(
            release_config(), coolify, FakeDiagnostics(), lambda _url: SHA
        )

        with self.assertRaisesMessage(ReleaseError, "contract-mismatch"):
            deployer.deploy(timeout=1, interval=0.01)

        self.assertNotIn("PATCH", [method for method, _path in coolify.calls])
