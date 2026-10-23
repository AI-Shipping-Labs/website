"""Offline fail-closed contracts for the exact-head Coolify release."""

from datetime import datetime, timezone

import yaml
from django.test import SimpleTestCase, tag

from deploy.coolify.contracts import (
    EXPECTED_COOLIFY_URL,
    EXPECTED_SERVICE_UUID,
    EXPECTED_WEB_APP_UUID,
    ReleaseConfig,
    pin_runtime_images,
    reject_version_override,
    service_runtime_ready,
)
from deploy.coolify.errors import ReleaseError
from deploy.coolify.topology import (
    SOURCE_ENVIRONMENT,
    WEB_HEALTHCHECK,
    WORKER_HEALTHCHECK,
)

SHA = "a" * 40
IMAGE = f"ghcr.io/ai-shipping-labs/website:deploy-123-1-{SHA}"
DIGEST = "sha256:" + "b" * 64
CUTOFF = datetime(2026, 10, 23, 14, 0, tzinfo=timezone.utc)


def release_config(**overrides):
    values = {
        "coolify_url": EXPECTED_COOLIFY_URL,
        "coolify_token": "coolify-token",
        "service_uuid": EXPECTED_SERVICE_UUID,
        "web_app_uuid": EXPECTED_WEB_APP_UUID,
        "verification_token": "staff-token",
        "image": IMAGE,
        "digest": DIGEST,
        "sha": SHA,
    }
    values.update(overrides)
    return ReleaseConfig(**values)


def _source_runtime(role):
    healthcheck = WEB_HEALTHCHECK
    if role == "worker":
        healthcheck = WORKER_HEALTHCHECK
    service = {
        "image": "ghcr.io/ai-shipping-labs/website:old",
        "command": None,
        "entrypoint": None,
        "environment": dict(SOURCE_ENVIRONMENT[role]),
        "healthcheck": healthcheck,
        "networks": [EXPECTED_SERVICE_UUID, "coolify"],
    }
    if role == "web":
        service["labels"] = {"owner": "source"}
    return service


def source_service(include_worker=True):
    services = {
        "web": _source_runtime("web"),
        "redis": {"image": "redis:7", "volumes": ["redis:/data"]},
    }
    applications = [{"name": "web", "image": services["web"]["image"]}]
    if include_worker:
        services["worker"] = _source_runtime("worker")
        applications.append({"name": "worker", "image": services["worker"]["image"]})
    compose = {
        "services": services,
        "networks": {
            EXPECTED_SERVICE_UUID: {"name": EXPECTED_SERVICE_UUID, "external": True},
            "coolify": {"name": "coolify", "external": True},
        },
        "volumes": {"redis": {}},
    }
    return {"docker_compose_raw": yaml.safe_dump(compose), "applications": applications}


def diagnostics(**role_overrides):
    payload = {"generated_at": "2026-10-23T14:01:00+00:00"}
    for role in ("web", "worker"):
        evidence = {
            "tag": SHA,
            "recorded_at": "2026-10-23T14:00:30+00:00",
            "role": role,
            "phases": {"total": 4.2},
        }
        evidence.update(role_overrides.get(role, {}))
        payload[role] = evidence
    return payload


@tag("core")
class ReleaseIdentityTest(SimpleTestCase):
    def test_accepts_run_unique_tag_full_sha_and_digest(self):
        release_config().validate()

    def test_rejects_missing_credentials_and_unexpected_targets(self):
        cases = (
            {"coolify_token": ""},
            {"coolify_url": "http://169.58.119.163:8000"},
            {"service_uuid": "wrong"},
            {"web_app_uuid": "wrong"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ReleaseError):
                release_config(**overrides).validate()

    def test_rejects_short_sha_reusable_tag_and_wrong_digest(self):
        cases = (
            {"sha": "a" * 7},
            {"image": f"ghcr.io/ai-shipping-labs/website:{SHA}"},
            {"digest": "sha256:short"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ReleaseError):
                release_config(**overrides).validate()


@tag("core")
class ComposePinningTest(SimpleTestCase):
    def test_pins_web_and_worker_and_preserves_unrelated_compose_data(self):
        service = source_service()
        original = yaml.safe_load(service["docker_compose_raw"])

        updated_raw, names = pin_runtime_images(service, IMAGE)
        updated = yaml.safe_load(updated_raw)

        self.assertEqual(names, {"web", "worker"})
        for name in names:
            self.assertEqual(updated["services"][name]["image"], IMAGE)
        self.assertEqual(updated["services"]["web"]["environment"], original["services"]["web"]["environment"])
        self.assertEqual(updated["services"]["web"]["labels"], {"owner": "source"})
        self.assertEqual(updated["services"]["redis"], original["services"]["redis"])
        self.assertEqual(updated["networks"], original["networks"])
        self.assertEqual(updated["volumes"], original["volumes"])

    def test_rejects_partial_web_only_inventory(self):
        with self.assertRaisesMessage(ReleaseError, "runtime-inventory"):
            pin_runtime_images(source_service(include_worker=False), IMAGE)

    def test_rejects_extra_aisl_runtime_in_compose_or_api(self):
        for owner in ("compose", "api"):
            service = source_service()
            compose = yaml.safe_load(service["docker_compose_raw"])
            if owner == "compose":
                compose["services"]["scheduler"] = {
                    "image": "ghcr.io/ai-shipping-labs/website:old"
                }
                service["docker_compose_raw"] = yaml.safe_dump(compose)
            else:
                service["applications"].append(
                    {"name": "scheduler", "image": "ghcr.io/ai-shipping-labs/website:old"}
                )
            with self.subTest(owner=owner), self.assertRaises(ReleaseError):
                pin_runtime_images(service, IMAGE)

    def test_rejects_network_and_worker_image_drift(self):
        mutations = ("network", "network-name", "attachment", "image")
        for mutation in mutations:
            service = source_service()
            compose = yaml.safe_load(service["docker_compose_raw"])
            if mutation == "network":
                compose["networks"]["coolify"]["external"] = False
            elif mutation == "network-name":
                compose["networks"]["coolify"]["name"] = "other"
            elif mutation == "attachment":
                compose["services"]["worker"]["networks"] = ["coolify"]
            else:
                compose["services"]["worker"]["image"] = "other/repository:tag"
            service["docker_compose_raw"] = yaml.safe_dump(compose)
            with self.subTest(mutation=mutation), self.assertRaises(ReleaseError):
                pin_runtime_images(service, IMAGE)

    def test_rejects_runtime_version_overrides(self):
        service = source_service()
        compose = yaml.safe_load(service["docker_compose_raw"])
        compose["services"]["worker"]["environment"]["VERSION"] = "wrong"
        service["docker_compose_raw"] = yaml.safe_dump(compose)

        with self.assertRaisesMessage(ReleaseError, "runtime-version-override"):
            pin_runtime_images(service, IMAGE)
        with self.assertRaisesMessage(ReleaseError, "runtime-version-override"):
            reject_version_override([{"key": "VERSION", "is_build_time": True}], "app")

    def test_rejects_changed_runtime_command(self):
        service = source_service()
        compose = yaml.safe_load(service["docker_compose_raw"])
        compose["services"]["worker"]["command"] = "python manage.py qcluster"
        service["docker_compose_raw"] = yaml.safe_dump(compose)

        with self.assertRaisesMessage(ReleaseError, "command-mismatch"):
            pin_runtime_images(service, IMAGE)

    def test_runtime_requires_exact_image_and_healthy_worker(self):
        service = source_service()
        for app in service["applications"]:
            app.update(image=IMAGE, status="running:healthy")
        self.assertTrue(service_runtime_ready(service, IMAGE, {"web", "worker"}))

        service["applications"][1]["image"] = "ghcr.io/ai-shipping-labs/website:wrong"
        self.assertFalse(service_runtime_ready(service, IMAGE, {"web", "worker"}))
        service["applications"][1].update(image=IMAGE, status="running:unhealthy")
        self.assertFalse(service_runtime_ready(service, IMAGE, {"web", "worker"}))
