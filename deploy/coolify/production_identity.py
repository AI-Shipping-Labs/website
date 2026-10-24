"""Require the exact successful Deploy Dev attempt and its recorded digest."""

import json
import re
import subprocess

from .contracts import DIGEST_PATTERN, TAG_PATTERN
from .errors import ReleaseError

REPOSITORY = "AI-Shipping-Labs/website"
REQUIRED_JOBS = {
    "Deploy Gates (migrations / OpenAPI / system check / static)",
    "Combined coverage (fail-under 85)",
    "PostgreSQL 16 Verification",
    "Deploy to Dev",
}
for shard in range(1, 5):
    REQUIRED_JOBS.add(f"Unit & Integration Tests (shard {shard}/4)")
    REQUIRED_JOBS.add(f"Playwright Core E2E (shard {shard}/4)")


def release_attempt(tag, sha, requested):
    if not isinstance(tag, str) or not TAG_PATTERN.fullmatch(tag):
        raise ReleaseError("production-release-tag-invalid")
    if not tag.endswith(f"-{sha}"):
        raise ReleaseError("dev-production-revision-mismatch")
    if requested and requested not in {tag, sha}:
        raise ReleaseError("requested-release-is-not-current-dev")
    _prefix, run_id, attempt, _sha = tag.split("-", 3)
    return int(run_id), int(attempt)


def require_green_run(github, run_id, attempt, sha):
    prefix = f"/repos/{REPOSITORY}/actions/runs/{run_id}"
    run = github.call("GET", prefix)
    expected = {
        "id": run_id, "run_attempt": attempt, "head_sha": sha,
        "head_branch": "main", "path": ".github/workflows/deploy-dev.yml",
        "status": "completed", "conclusion": "success",
    }
    for key, value in expected.items():
        if run.get(key) != value:
            raise ReleaseError(f"dev-run-not-green-exact-release field={key}")
    if run.get("event") not in {"push", "workflow_dispatch"}:
        raise ReleaseError("dev-run-event-invalid")
    jobs = github.call("GET", f"{prefix}/attempts/{attempt}/jobs?per_page=100")
    return require_green_jobs(jobs)


def require_green_jobs(payload):
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or payload.get("total_count") != len(REQUIRED_JOBS):
        raise ReleaseError("dev-job-inventory-incomplete")
    names = []
    deploy_job = None
    for job in jobs:
        names.append(job.get("name"))
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            raise ReleaseError("dev-job-not-successful")
        if job.get("name") == "Deploy to Dev":
            deploy_job = job.get("id")
    if len(names) != len(REQUIRED_JOBS) or set(names) != REQUIRED_JOBS:
        raise ReleaseError("dev-job-inventory-mismatch")
    if not isinstance(deploy_job, int) or isinstance(deploy_job, bool):
        raise ReleaseError("dev-deploy-job-identity-invalid")
    return deploy_job


def command_output(arguments):
    try:
        result = subprocess.run(
            arguments, capture_output=True, text=True, check=True, timeout=60,
        )
    except (subprocess.SubprocessError, OSError):
        raise ReleaseError("release-evidence-command-failed") from None
    return result.stdout


def recorded_digest(job_id, sha):
    logs = command_output([
        "gh", "api", f"/repos/{REPOSITORY}/actions/jobs/{job_id}/logs",
    ])
    pattern = rf"Verified exact-head Coolify release sha={sha} digest=(sha256:[0-9a-f]{{64}}) deployment="
    matches = re.findall(pattern, logs)
    if len(matches) != 1:
        raise ReleaseError("dev-deployment-digest-receipt-missing")
    return matches[0]


def require_registry_digest(image, expected):
    output = command_output([
        "docker", "buildx", "imagetools", "inspect", image,
        "--format", "{{json .Manifest.Digest}}",
    ])
    try:
        actual = json.loads(output)
    except json.JSONDecodeError:
        raise ReleaseError("registry-digest-invalid") from None
    if not isinstance(actual, str) or not DIGEST_PATTERN.fullmatch(actual):
        raise ReleaseError("registry-digest-invalid")
    if actual != expected:
        raise ReleaseError("registry-digest-differs-from-green-deployment")
