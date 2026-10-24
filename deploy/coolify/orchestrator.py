"""Ordered, fail-closed deployment of AISL's source Service and public web."""

import base64
from dataclasses import dataclass

from .contracts import (
    EXPECTED_WEB_APP_NAME,
    environment_fingerprint,
    pin_runtime_images,
    reject_version_override,
    service_runtime_ready,
    validate_service_identity,
    validate_web_application,
    validate_web_environment,
    verify_persisted_compose,
    web_runtime_fingerprint,
)
from .errors import ReleaseError
from .public_readiness import public_endpoints_ready
from .verification import (
    application_deployment_uuid,
    boot_evidence_ready,
    diagnostics_cutoff,
    exact_deployment_finished,
    service_request_accepted,
    source_boot_receipt,
    wait_for_consecutive,
    wait_until,
    web_application_ready,
)


@dataclass
class ReleasePlan:
    source_service: dict
    source_compose: str
    runtime_names: set
    source_env_fingerprint: tuple
    web_application: dict
    web_envs: list
    web_fingerprint: tuple


@dataclass(frozen=True)
class ReleaseResult:
    deployment_uuid: str
    source_receipt: dict


class ReleaseDeployer:
    def __init__(self, config, coolify, diagnostics, ping_reader):
        self.config = config
        self.coolify = coolify
        self.diagnostics = diagnostics
        self.ping_reader = ping_reader

    @property
    def service_path(self):
        return f"/api/v1/services/{self.config.service_uuid}"

    @property
    def app_path(self):
        return f"/api/v1/applications/{self.config.web_app_uuid}"

    def deploy(self, timeout=1500, interval=10):
        plan = self.preflight()
        receipt = self._deploy_source(plan, timeout, interval)
        deployment_uuid = self._deploy_web(plan, timeout, interval)
        return ReleaseResult(deployment_uuid, receipt)

    def preflight(self):
        self.config.validate()
        source = self.coolify.call("GET", self.service_path)
        validate_service_identity(source, self.config.service_uuid)
        source_envs = self.coolify.call("GET", f"{self.service_path}/envs")
        reject_version_override(source_envs, "source-service")
        compose, names = pin_runtime_images(source, self.config.image)
        app = self.coolify.call("GET", self.app_path)
        validate_web_application(app, self.config.web_app_uuid)
        app_envs = self.coolify.call("GET", f"{self.app_path}/envs")
        reject_version_override(app_envs, "web-application")
        validate_web_environment(app_envs)
        self._reject_active_web_deployments()
        fingerprint = web_runtime_fingerprint(app, app_envs)
        source_fingerprint = environment_fingerprint(source_envs)
        return ReleasePlan(
            source,
            compose,
            names,
            source_fingerprint,
            app,
            app_envs,
            fingerprint,
        )

    def _reject_active_web_deployments(self):
        path = f"/api/v1/deployments/applications/{self.config.web_app_uuid}"
        payload = self.coolify.call("GET", f"{path}?skip=0&take=1")
        if not isinstance(payload, dict):
            raise ReleaseError("invalid-application-deployment-list")
        count = payload.get("count")
        deployments = payload.get("deployments")
        invalid_count = not isinstance(count, int) or isinstance(count, bool)
        if invalid_count or not isinstance(deployments, list):
            raise ReleaseError("invalid-application-deployment-list")
        if count < 1 or len(deployments) != 1:
            raise ReleaseError("missing-application-deployment-history")
        latest = deployments[0]
        if not isinstance(latest, dict):
            raise ReleaseError("invalid-application-deployment-identity")
        application_id = latest.get("application_id")
        if not isinstance(application_id, str) or not application_id:
            raise ReleaseError("invalid-application-deployment-identity")
        if latest.get("application_name") != EXPECTED_WEB_APP_NAME:
            raise ReleaseError("invalid-application-deployment-identity")
        active = self.coolify.call("GET", "/api/v1/deployments")
        self._reject_matching_active_deployment(active, application_id)

    @staticmethod
    def _reject_matching_active_deployment(active, application_id):
        if not isinstance(active, list):
            raise ReleaseError("invalid-active-deployment-list")
        for item in active:
            if not isinstance(item, dict):
                raise ReleaseError("invalid-active-deployment-list")
            matches_id = item.get("application_id") == application_id
            matches_name = item.get("application_name") == EXPECTED_WEB_APP_NAME
            if matches_id or matches_name:
                raise ReleaseError("concurrent-web-deployment")

    def _deploy_source(self, plan, timeout, interval):
        current = self.coolify.call("GET", self.service_path)
        validate_service_identity(current, self.config.service_uuid)
        self._guard_source_unchanged(plan.source_service, current)
        self._verify_source_envs(plan)
        encoded = base64.b64encode(plan.source_compose.encode()).decode()
        self.coolify.call("PATCH", self.service_path, {"docker_compose_raw": encoded})
        self._verify_source_persisted(plan)
        cutoff_payload = self.diagnostics.call("GET", "/api/diagnostics/boot-timing")
        cutoff = diagnostics_cutoff(cutoff_payload)
        cutoff_value = cutoff_payload["generated_at"]
        response = self.coolify.call(
            "POST", f"/api/v1/deploy?uuid={self.config.service_uuid}&force=true"
        )
        service_request_accepted(response, self.config.service_uuid)
        return self._wait_for_source(plan, cutoff, cutoff_value, timeout, interval)

    @staticmethod
    def _guard_source_unchanged(before, current):
        same_compose = before.get("docker_compose_raw") == current.get("docker_compose_raw")
        same_hash = before.get("config_hash") == current.get("config_hash")
        if not same_compose or not same_hash:
            raise ReleaseError("concurrent-source-compose-edit")

    def _verify_source_persisted(self, plan):
        service = self.coolify.call("GET", self.service_path)
        validate_service_identity(service, self.config.service_uuid)
        self._verify_source_envs(plan)
        verify_persisted_compose(
            service,
            plan.source_compose,
            self.config.image,
            plan.runtime_names,
        )
        return service

    def _verify_source_envs(self, plan):
        source_envs = self.coolify.call("GET", f"{self.service_path}/envs")
        reject_version_override(source_envs, "source-service")
        if environment_fingerprint(source_envs) != plan.source_env_fingerprint:
            raise ReleaseError("concurrent-source-environment-edit")

    def _wait_for_source(self, plan, cutoff, cutoff_value, timeout, interval):
        receipt = None

        def source_ready():
            nonlocal receipt
            service = self._verify_source_persisted(plan)
            if not service_runtime_ready(service, self.config.image, plan.runtime_names):
                return False
            diagnostics = self.diagnostics.call("GET", "/api/diagnostics/boot-timing")
            if not boot_evidence_ready(diagnostics, self.config.sha, cutoff):
                return False
            receipt = source_boot_receipt(diagnostics, self.config.sha, cutoff_value)
            return True

        wait_until(source_ready, timeout, interval, "source-runtime-not-exact")
        return receipt

    def _deploy_web(self, plan, timeout, interval):
        self._guard_web_unchanged(plan)
        self._require_source_runtime(plan)
        repository, tag = self.config.image.rsplit(":", 1)
        payload = {
            "docker_registry_image_name": repository,
            "docker_registry_image_tag": tag,
        }
        self.coolify.call("PATCH", self.app_path, payload)
        self._verify_web_runtime(plan, require_image=True)
        response = self.coolify.call(
            "POST", f"/api/v1/deploy?uuid={self.config.web_app_uuid}&force=true"
        )
        deployment_uuid = application_deployment_uuid(
            response,
            self.config.web_app_uuid,
        )
        self._wait_for_web_deployment(plan, deployment_uuid, timeout, interval)
        self._wait_for_public_web(plan, timeout, interval)
        return deployment_uuid

    def _guard_web_unchanged(self, plan):
        self._reject_active_web_deployments()
        current = self.coolify.call("GET", self.app_path)
        if current.get("config_hash") != plan.web_application.get("config_hash"):
            raise ReleaseError("concurrent-web-configuration-edit")
        current_envs = self.coolify.call("GET", f"{self.app_path}/envs")
        self._verify_web_fingerprint(plan, current, current_envs)

    def _verify_web_runtime(self, plan, require_image):
        current = self.coolify.call("GET", self.app_path)
        current_envs = self.coolify.call("GET", f"{self.app_path}/envs")
        self._verify_web_fingerprint(plan, current, current_envs)
        if require_image and not web_application_ready(
            current,
            self.config.web_app_uuid,
            self.config.image,
            require_healthy=False,
        ):
            raise ReleaseError("web-image-persistence-mismatch")
        return current

    def _verify_web_fingerprint(self, plan, current, current_envs):
        validate_web_application(current, self.config.web_app_uuid)
        reject_version_override(current_envs, "web-application")
        validate_web_environment(current_envs)
        if web_runtime_fingerprint(current, current_envs) != plan.web_fingerprint:
            raise ReleaseError("concurrent-web-runtime-edit")

    def _wait_for_web_deployment(self, plan, deployment_uuid, timeout, interval):
        def deployment_finished():
            self._require_source_runtime(plan)
            self._verify_web_runtime(plan, require_image=True)
            path = f"/api/v1/deployments/{deployment_uuid}"
            payload = self.coolify.call("GET", path)
            return exact_deployment_finished(payload, deployment_uuid)

        wait_until(
            deployment_finished,
            timeout,
            interval,
            "application-deployment-not-terminal",
        )

    def _wait_for_public_web(self, plan, timeout, interval):
        def public_web_ready():
            self._require_source_runtime(plan)
            app = self._verify_web_runtime(plan, require_image=True)
            if not web_application_ready(app, self.config.web_app_uuid, self.config.image):
                return False
            return public_endpoints_ready(self.ping_reader, self.config.ping_url, self.config.sha)

        wait_for_consecutive(public_web_ready, 3, timeout, interval)
        self._require_source_runtime(plan)

    def _require_source_runtime(self, plan):
        service = self._verify_source_persisted(plan)
        if not service_runtime_ready(service, self.config.image, plan.runtime_names):
            raise ReleaseError("source-runtime-not-exact")
