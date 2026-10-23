"""Runtime and deployment evidence checks for the split Coolify topology."""

import re
import time
from datetime import datetime, timedelta

from .contracts import EXPECTED_WEB_APP_NAME, REQUIRED_ROLES
from .errors import ReleaseError

PENDING_STATUSES = {"queued", "in_progress"}
FAILED_STATUSES = {"failed", "cancelled", "cancelled-by-user", "error", "killed"}
TIMESTAMP_PATTERN = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)


def parse_timestamp(value, field):
    if not isinstance(value, str) or not TIMESTAMP_PATTERN.fullmatch(value):
        raise ReleaseError(f"invalid-timestamp field={field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ReleaseError(f"invalid-timestamp field={field}") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseError(f"naive-timestamp field={field}")
    return parsed


def diagnostics_cutoff(payload):
    if not isinstance(payload, dict):
        raise ReleaseError("invalid-diagnostics-response")
    return parse_timestamp(payload.get("generated_at"), "generated_at")


def boot_evidence_ready(payload, sha, cutoff):
    generated = diagnostics_cutoff(payload)
    if generated + timedelta(seconds=2) < cutoff:
        raise ReleaseError("diagnostics-clock-regressed")
    for role in REQUIRED_ROLES:
        evidence = payload.get(role)
        if evidence is None:
            return False
        if not _role_evidence_ready(evidence, role, sha, cutoff, generated):
            return False
    return True


def source_boot_receipt(payload, sha, cutoff_value):
    cutoff = parse_timestamp(cutoff_value, "cutoff")
    if not boot_evidence_ready(payload, sha, cutoff):
        raise ReleaseError("boot-receipt-evidence-not-ready")
    proofs = []
    for role in sorted(REQUIRED_ROLES):
        evidence = payload[role]
        proofs.append(
            {
                "role": role,
                "tag": evidence["tag"],
                "recorded_at": evidence["recorded_at"],
            }
        )
    return {"cutoff": cutoff_value, "roles": proofs}


def _role_evidence_ready(evidence, role, sha, cutoff, generated):
    if not isinstance(evidence, dict):
        raise ReleaseError(f"invalid-boot-evidence role={role}")
    if evidence.get("role") != role or evidence.get("tag") != sha:
        return False
    recorded = parse_timestamp(evidence.get("recorded_at"), f"{role}.recorded_at")
    if recorded <= cutoff:
        return False
    if recorded > generated + timedelta(seconds=2):
        raise ReleaseError(f"future-boot-evidence role={role}")
    total = (evidence.get("phases") or {}).get("total")
    if isinstance(total, bool) or not isinstance(total, (int, float)) or total <= 0:
        raise ReleaseError(f"invalid-boot-total role={role}")
    return True


def service_request_accepted(payload, service_uuid):
    deployments = None
    if isinstance(payload, dict):
        deployments = payload.get("deployments")
    if not isinstance(deployments, list) or len(deployments) != 1:
        raise ReleaseError("invalid-service-deploy-response")
    if deployments[0].get("resource_uuid") != service_uuid:
        raise ReleaseError("service-deploy-target-mismatch")


def application_deployment_uuid(payload, app_uuid):
    deployments = None
    if isinstance(payload, dict):
        deployments = payload.get("deployments")
    if not isinstance(deployments, list) or len(deployments) != 1:
        raise ReleaseError("invalid-application-deploy-response")
    deployment = deployments[0]
    if deployment.get("resource_uuid") != app_uuid:
        raise ReleaseError("application-deploy-target-mismatch")
    deployment_uuid = deployment.get("deployment_uuid")
    if not isinstance(deployment_uuid, str) or not deployment_uuid:
        raise ReleaseError("missing-application-deployment-uuid")
    return deployment_uuid


def exact_deployment_finished(payload, deployment_uuid):
    if payload.get("deployment_uuid") != deployment_uuid:
        raise ReleaseError("deployment-identity-mismatch")
    status = payload.get("status")
    if status == "finished":
        return True
    if status in PENDING_STATUSES:
        return False
    if status in FAILED_STATUSES:
        raise ReleaseError(f"deployment-terminal-failure status={status}")
    raise ReleaseError(f"unexpected-deployment-status status={status}")


def web_application_ready(app, app_uuid, image, require_healthy=True):
    repository, tag = image.rsplit(":", 1)
    expected = (
        app.get("uuid") == app_uuid,
        app.get("name") == EXPECTED_WEB_APP_NAME,
        app.get("docker_registry_image_name") == repository,
        app.get("docker_registry_image_tag") == tag,
    )
    if not all(expected):
        return False
    if require_healthy and app.get("status") != "running:healthy":
        return False
    return True


def wait_until(probe, timeout, interval, reason, clock=None, sleeper=None):
    clock = clock or time.monotonic
    sleeper = sleeper or time.sleep
    deadline = clock() + timeout
    while True:
        if probe():
            return
        if clock() >= deadline:
            raise ReleaseError(f"verification-timeout reason={reason}")
        sleeper(min(interval, max(0, deadline - clock())))


def wait_for_consecutive(probe, required, timeout, interval, clock=None, sleeper=None):
    matches = 0

    def consecutive_probe():
        nonlocal matches
        if probe():
            matches += 1
        else:
            matches = 0
        return matches >= required

    wait_until(
        consecutive_probe,
        timeout,
        interval,
        "public-release-not-consecutive",
        clock,
        sleeper,
    )
