"""Pure validation and Compose transformations for AISL Coolify releases."""

import copy
import json
import re
from dataclasses import dataclass

import yaml

from .errors import ReleaseError
from .topology import (
    SOURCE_ENVIRONMENT,
    WEB_APPLICATION_CONTRACT,
    WEB_ENVIRONMENT,
    WEB_HEALTHCHECK,
    WEB_RUNTIME_FIELDS,
    WORKER_HEALTHCHECK,
)

EXPECTED_COOLIFY_URL = "https://coolify.169.58.119.163.sslip.io"
EXPECTED_SERVICE_UUID = "mekt9kz74fvbixla6olinuma"
EXPECTED_SERVICE_NAME = "aisl-staging"
EXPECTED_WEB_APP_UUID = "w3mxfr6wzjnpg3hqaw3y0obq"
EXPECTED_WEB_APP_NAME = "aishl-production-rolling-web"
EXPECTED_WEB_FQDN = (
    "https://aishippinglabs.com,https://www.aishippinglabs.com,"
    "https://prod.aishippinglabs.com,"
    "http://aisl-staging.169.58.124.141.sslip.io"
)
IMAGE_REPOSITORY = "ghcr.io/ai-shipping-labs/website"
REQUIRED_ROLES = {"web", "worker"}
REQUIRED_NETWORKS = {"coolify", EXPECTED_SERVICE_UUID}
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
TAG_PATTERN = re.compile(r"deploy-[0-9]+-[0-9]+-[0-9a-f]{40}\Z")


@dataclass(frozen=True)
class ReleaseConfig:
    coolify_url: str
    coolify_token: str
    service_uuid: str
    web_app_uuid: str
    verification_token: str
    image: str
    digest: str
    sha: str
    ping_url: str = "https://aishippinglabs.com/ping"
    diagnostics_url: str = "https://aishippinglabs.com"

    def validate(self):
        required = (
            self.coolify_url,
            self.coolify_token,
            self.service_uuid,
            self.web_app_uuid,
            self.verification_token,
        )
        if not all(value and value.strip() for value in required):
            raise ReleaseError("missing-deployment-configuration")
        if self.coolify_url.rstrip("/") != EXPECTED_COOLIFY_URL:
            raise ReleaseError("unexpected-coolify-api-target")
        if self.service_uuid != EXPECTED_SERVICE_UUID:
            raise ReleaseError("unexpected-service-uuid")
        if self.web_app_uuid != EXPECTED_WEB_APP_UUID:
            raise ReleaseError("unexpected-web-application-uuid")
        _validate_release_identity(self.image, self.digest, self.sha)


def _validate_release_identity(image, digest, sha):
    if not SHA_PATTERN.fullmatch(sha):
        raise ReleaseError("invalid-full-release-sha")
    if not DIGEST_PATTERN.fullmatch(digest):
        raise ReleaseError("invalid-image-digest")
    prefix = f"{IMAGE_REPOSITORY}:"
    if not image.startswith(prefix):
        raise ReleaseError("unexpected-image-repository")
    tag = image.removeprefix(prefix)
    if not TAG_PATTERN.fullmatch(tag) or not tag.endswith(sha):
        raise ReleaseError("non-unique-release-tag")


def validate_service_identity(service, service_uuid):
    if service.get("uuid") != service_uuid:
        raise ReleaseError("service-identity-mismatch")
    if service.get("name") != EXPECTED_SERVICE_NAME:
        raise ReleaseError("service-name-mismatch")
    if not service.get("docker_compose_raw"):
        raise ReleaseError("missing-service-compose")


def validate_web_application(app, app_uuid):
    if app.get("uuid") != app_uuid or app.get("name") != EXPECTED_WEB_APP_NAME:
        raise ReleaseError("web-application-identity-mismatch")
    for field, expected in WEB_APPLICATION_CONTRACT.items():
        if app.get(field) != expected:
            raise ReleaseError(f"web-application-contract-mismatch field={field}")


def validate_web_environment(envs):
    values = {}
    for item in envs:
        if not isinstance(item, dict):
            raise ReleaseError("invalid-web-environment-response")
        if item.get("is_preview") is not False:
            continue
        key = item.get("key")
        if key in values:
            raise ReleaseError(f"duplicate-web-environment-key key={key}")
        values[key] = item.get("value")
    for key, expected in WEB_ENVIRONMENT.items():
        if values.get(key) != expected:
            raise ReleaseError(f"web-environment-contract-mismatch key={key}")


def web_runtime_fingerprint(app, envs):
    app_values = []
    for field in WEB_RUNTIME_FIELDS:
        value = json.dumps(app.get(field), sort_keys=True, separators=(",", ":"))
        app_values.append((field, value))
    return tuple(app_values), environment_fingerprint(envs)


def environment_fingerprint(envs):
    values = []
    for item in envs:
        values.append(json.dumps(item, sort_keys=True, separators=(",", ":")))
    return tuple(sorted(values))


def reject_version_override(envs, owner):
    if not isinstance(envs, list):
        raise ReleaseError(f"invalid-environment-response owner={owner}")
    for item in envs:
        if not isinstance(item, dict):
            raise ReleaseError(f"invalid-environment-response owner={owner}")
        if item.get("key") == "VERSION":
            raise ReleaseError(f"runtime-version-override owner={owner}")


def load_compose(raw):
    try:
        compose = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise ReleaseError("invalid-service-compose") from None
    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise ReleaseError("invalid-service-compose")
    return compose


def pin_runtime_images(service, image):
    compose = load_compose(service["docker_compose_raw"])
    names = _runtime_service_names(service, compose)
    _validate_source_contract(compose)
    _validate_networks(compose, names)
    updated = copy.deepcopy(compose)
    for name in names:
        _reject_compose_version(updated["services"][name], name)
        updated["services"][name]["image"] = image
    return yaml.safe_dump(updated, sort_keys=False), names


def _runtime_service_names(service, compose):
    compose_names = _aisl_compose_names(compose)
    application_names = _aisl_application_names(service)
    if compose_names != REQUIRED_ROLES:
        raise ReleaseError("unexpected-compose-runtime-inventory")
    if application_names != REQUIRED_ROLES:
        raise ReleaseError("unexpected-runtime-application-inventory")
    if compose_names != application_names:
        raise ReleaseError("runtime-application-inventory-mismatch")
    return compose_names


def _aisl_compose_names(compose):
    names = set()
    for name, item in compose["services"].items():
        if not isinstance(item, dict):
            continue
        if _is_aisl_image(item.get("image")) or name in REQUIRED_ROLES:
            if not _is_aisl_image(item.get("image")):
                raise ReleaseError(f"unexpected-runtime-image role={name}")
            names.add(name)
    return names


def _aisl_application_names(service):
    applications = service.get("applications") or []
    if not isinstance(applications, list):
        raise ReleaseError("invalid-runtime-application-inventory")
    names = set()
    matches = 0
    for app in applications:
        if not isinstance(app, dict):
            raise ReleaseError("invalid-runtime-application-inventory")
        name = app.get("name")
        if _is_aisl_image(app.get("image")) or name in REQUIRED_ROLES:
            matches += 1
            if not _is_aisl_image(app.get("image")):
                raise ReleaseError(f"unexpected-runtime-app-image role={name}")
            names.add(name)
    if matches != len(names):
        raise ReleaseError("duplicate-runtime-application-inventory")
    return names


def _is_aisl_image(image):
    if not isinstance(image, str):
        return False
    return image.startswith(f"{IMAGE_REPOSITORY}:")


def _validate_source_contract(compose):
    healthchecks = {"web": WEB_HEALTHCHECK, "worker": WORKER_HEALTHCHECK}
    for role in REQUIRED_ROLES:
        service = compose["services"][role]
        if service.get("command") is not None or service.get("entrypoint") is not None:
            raise ReleaseError(f"source-runtime-command-mismatch role={role}")
        if service.get("healthcheck") != healthchecks[role]:
            raise ReleaseError(f"source-runtime-healthcheck-mismatch role={role}")
        _validate_source_environment(role, service.get("environment"))


def _validate_source_environment(role, environment):
    if not isinstance(environment, dict):
        raise ReleaseError(f"source-runtime-environment-mismatch role={role}")
    for key, expected in SOURCE_ENVIRONMENT[role].items():
        if environment.get(key) != expected:
            raise ReleaseError(f"source-runtime-environment-mismatch role={role} key={key}")


def _validate_networks(compose, names):
    networks = compose.get("networks")
    if not isinstance(networks, dict):
        raise ReleaseError("missing-required-external-networks")
    for name in REQUIRED_NETWORKS:
        definition = networks.get(name)
        if not isinstance(definition, dict):
            raise ReleaseError("missing-required-external-networks")
        if definition.get("external") is not True or definition.get("name") != name:
            raise ReleaseError("missing-required-external-networks")
    for service_name in names:
        attached = compose["services"][service_name].get("networks") or []
        if not isinstance(attached, (list, dict)):
            raise ReleaseError(f"runtime-network-mismatch role={service_name}")
        if not REQUIRED_NETWORKS.issubset(set(attached)):
            raise ReleaseError(f"runtime-network-mismatch role={service_name}")


def _reject_compose_version(service, name):
    environment = service.get("environment") or {}
    if isinstance(environment, dict) and "VERSION" in environment:
        raise ReleaseError(f"runtime-version-override owner=compose-{name}")
    if isinstance(environment, list):
        for entry in environment:
            if isinstance(entry, str) and entry.split("=", 1)[0] == "VERSION":
                raise ReleaseError(f"runtime-version-override owner=compose-{name}")


def verify_persisted_compose(service, expected_raw, image, names):
    expected = load_compose(expected_raw)
    actual = load_compose(service.get("docker_compose_raw") or "")
    if actual != expected:
        raise ReleaseError("compose-persistence-mismatch")
    for name in names:
        if actual["services"][name].get("image") != image:
            raise ReleaseError(f"persisted-image-mismatch role={name}")


def service_runtime_ready(service, image, names):
    if _aisl_application_names(service) != names:
        raise ReleaseError("runtime-application-inventory-mismatch")
    applications = {}
    for item in service.get("applications") or []:
        applications[item.get("name")] = item
    for name in names:
        app = applications.get(name)
        if not app or app.get("image") != image:
            return False
        if app.get("status") != "running:healthy":
            return False
    return True
