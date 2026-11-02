"""Sanitized diagnostics for the Coolify source readiness poll."""

import logging
import re

from .contracts import (
    DIGEST_PATTERN,
    IMAGE_REPOSITORY,
    REQUIRED_ROLES,
    TAG_PATTERN,
    service_runtime_issues,
)
from .verification import boot_evidence_issues, source_boot_receipt

logger = logging.getLogger("deploy.coolify.orchestrator")

_SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z")
_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_REASON_BY_CONDITION = {
    "image-mismatch": "source-image-mismatch",
    "status-not-ready": "source-status-not-ready",
    "missing": "source-boot-evidence-missing",
    "not-current": "source-boot-evidence-not-current",
    "stale": "source-boot-evidence-stale",
}
_SAFE_SOURCE_STATUSES = {
    "created",
    "exited",
    "restarting",
    "running",
    "running:healthy",
    "running:restarting",
    "running:starting",
    "running:unhealthy",
    "running:unknown",
    "starting",
    "stopped",
    "unknown",
}


class SourceReadinessWaiter:
    def __init__(self, service_reader, diagnostics, config, plan, cutoff, cutoff_value):
        self.service_reader = service_reader
        self.diagnostics = diagnostics
        self.config = config
        self.plan = plan
        self.cutoff = cutoff
        self.cutoff_value = cutoff_value
        self.receipt = None
        self.last_reason = "source-runtime-not-ready"
        self.last_state = "not-observed"
        self.previous_state = None

    def probe(self):
        service = self.service_reader(self.plan)
        issues = service_runtime_issues(service, self.config.image, self.plan.runtime_names)
        diagnostics = None
        if not issues:
            diagnostics = self.diagnostics.call("GET", "/api/diagnostics/boot-timing")
            boot_issues = boot_evidence_issues(diagnostics, self.config.sha, self.cutoff)
            issues += boot_issues
            if not boot_issues:
                self.receipt = source_boot_receipt(
                    diagnostics, self.config.sha, self.cutoff_value
                )
        self.last_reason, self.last_state = source_readiness_summary(
            issues, service, self.plan, self.config, diagnostics, self.cutoff_value
        )
        self.previous_state = log_source_readiness_transition(
            self.previous_state, self.last_state
        )
        return not issues

    def timeout_message(self):
        return (
            f"verification-timeout reason={self.last_reason} "
            f"last-observed={self.last_state}"
        )


def source_readiness_summary(issues, service, plan, config, diagnostics, cutoff):
    observed_state = _describe_source_state(
        service, plan.runtime_names, config.image, diagnostics, config.sha, cutoff
    )
    if not issues:
        return "source-runtime-ready", f"ready {observed_state}"
    reasons = sorted({_REASON_BY_CONDITION[issue["condition"]] for issue in issues})
    states = [_describe_issue(issue) for issue in issues]
    details = "; ".join(states)
    return "+".join(reasons), f"{details}; {observed_state}"


def log_source_readiness_transition(previous_state, current_state):
    if current_state != previous_state:
        logger.info("Coolify source readiness state=%s", current_state)
    return current_state


def _describe_source_state(service, names, image, diagnostics, sha, cutoff):
    runtime = _describe_runtime_state(service, names)
    boot = _describe_boot_state(diagnostics)
    return (
        f"runtime=[{runtime}] boot=[{boot}] expected_image={_safe_image(image)} "
        f"expected_tag={_safe_sha(sha)} cutoff={_safe_timestamp(cutoff)}"
    )


def _describe_runtime_state(service, names):
    applications = {}
    for application in service.get("applications") or []:
        applications[application.get("name")] = application
    states = []
    for role in sorted(names):
        application = applications.get(role)
        if application is None:
            states.append(f"{role}=missing")
            continue
        image = _safe_image(application.get("image"))
        status = _safe_status(application.get("status"))
        states.append(f"{role}(image={image},status={status})")
    return ",".join(states)


def _describe_boot_state(diagnostics):
    if diagnostics is None:
        return "not-observed"
    states = []
    for role in sorted(REQUIRED_ROLES):
        evidence = diagnostics.get(role)
        if evidence is None:
            states.append(f"{role}=missing")
            continue
        if not isinstance(evidence, dict):
            states.append(f"{role}=invalid")
            continue
        observed_role = _safe_role(evidence.get("role"))
        tag = _safe_sha(evidence.get("tag"))
        recorded_at = _safe_timestamp(evidence.get("recorded_at"))
        states.append(f"{role}(role={observed_role},tag={tag},recorded_at={recorded_at})")
    return ",".join(states)


def _describe_issue(issue):
    condition = issue["condition"]
    role = _safe_role(issue.get("role"))
    if condition == "image-mismatch":
        observed = _safe_image(issue.get("observed"))
        expected = _safe_image(issue.get("expected"))
        return f"image-mismatch role={role} observed={observed} expected={expected}"
    if condition == "status-not-ready":
        observed = _safe_status(issue.get("observed"))
        return f"status-not-ready role={role} observed={observed} expected=running:healthy"
    return _describe_boot_issue(issue, role)


def _describe_boot_issue(issue, role):
    condition = issue["condition"]
    expected_tag = _safe_sha(issue.get("expected_tag"))
    cutoff = _safe_timestamp(issue.get("cutoff"))
    if condition == "missing":
        return (
            f"boot-evidence-missing role={role} expected_tag={expected_tag} "
            f"cutoff={cutoff}"
        )
    tag = _safe_sha(issue.get("observed_tag"))
    recorded_at = _safe_timestamp(issue.get("recorded_at"))
    observed_role = _safe_role(issue.get("observed_role"))
    return (
        f"boot-evidence-{condition} role={role} observed_role={observed_role} "
        f"observed_tag={tag} expected_tag={expected_tag} "
        f"recorded_at={recorded_at} cutoff={cutoff}"
    )


def _safe_image(value):
    if not isinstance(value, str):
        return "<redacted>"
    repository, separator, release = value.partition(":")
    if repository != IMAGE_REPOSITORY or not separator:
        return "<redacted>"
    tag, digest_separator, digest = release.partition("@")
    if digest_separator and not DIGEST_PATTERN.fullmatch(digest):
        return "<redacted>"
    if TAG_PATTERN.fullmatch(tag):
        return value
    return "<redacted>"


def _safe_status(value):
    if value is None:
        return "<missing>"
    if isinstance(value, str) and value in _SAFE_SOURCE_STATUSES:
        return value
    return "<redacted>"


def _safe_sha(value):
    if value is None:
        return "<missing>"
    if isinstance(value, str) and _SHA_PATTERN.fullmatch(value):
        return value
    return "<redacted>"


def _safe_timestamp(value):
    if isinstance(value, str) and _TIMESTAMP_PATTERN.fullmatch(value):
        return value
    return "<invalid>"


def _safe_role(value):
    if isinstance(value, str) and value in {"web", "worker"}:
        return value
    return "<unexpected>"
