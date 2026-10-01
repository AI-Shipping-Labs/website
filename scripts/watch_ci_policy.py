"""Outcome, job inventory, and verdict policy for the CI watcher."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

GREEN = "green"
FAILED = "failed"
HANG = "hang"
UNRESOLVED = "unresolved"
SUPERSEDED = "superseded"
NO_VERDICT = "no_verdict"

RESULT_EXIT_CODES: dict[str, int] = {
    GREEN: 0,
    FAILED: 1,
    HANG: 2,
    UNRESOLVED: 3,
    SUPERSEDED: 4,
    NO_VERDICT: 5,
}

GENUINE_FAILURE_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "startup_failure", "action_required"}
)

DEFAULT_WORKFLOW = "Deploy Dev"


@dataclass(frozen=True)
class Job:
    name: str
    status: str = ""
    conclusion: str = ""
    url: str = ""

    @property
    def is_genuine_failure(self) -> bool:
        return (self.conclusion or "").lower() in GENUINE_FAILURE_CONCLUSIONS

    @property
    def is_skipped(self) -> bool:
        return (self.conclusion or "").lower() == "skipped"


@dataclass(frozen=True)
class Run:
    run_id: str
    status: str = ""
    conclusion: str = ""
    workflow: str = ""
    branch: str = ""
    created_at: str = ""
    head_sha: str = ""
    jobs: tuple[Job, ...] = ()

    @property
    def is_completed(self) -> bool:
        return (self.status or "").lower() == "completed"

    @property
    def is_cancelled(self) -> bool:
        return (self.conclusion or "").lower() == "cancelled"

    def job_map(self) -> dict[str, Job]:
        return {job.name: job for job in self.jobs}


@dataclass
class Verdict:
    result: str
    reason: str = ""
    run_id: str = ""
    workflow: str = ""
    branch: str = ""
    head_sha: str = ""
    required: list[str] = field(default_factory=list)
    failing_jobs: list[str] = field(default_factory=list)
    signature: str | None = None
    likely_infra: bool = False
    newer_run_id: str | None = None
    polls: int = 0
    elapsed_s: float = 0.0

    @property
    def exit_code(self) -> int:
        return RESULT_EXIT_CODES[self.result]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "exit_code": self.exit_code,
            "run_id": self.run_id,
            "workflow": self.workflow,
            "branch": self.branch,
            "head_sha": self.head_sha,
            "required": self.required,
            "failing_jobs": self.failing_jobs,
            "signature": self.signature,
            "likely_infra": self.likely_infra,
            "reason": self.reason,
            "newer_run_id": self.newer_run_id,
            "polls": self.polls,
            "elapsed_s": round(self.elapsed_s, 3),
        }


def allowed_skip_names(
    required: Sequence[str], allowed_skips: Sequence[str] | None
) -> list[str]:
    if allowed_skips is None:
        return list(required)
    names: list[str] = []
    for name in allowed_skips:
        if name in required:
            names.append(name)
    return names


def resolve_required_policy(
    workflow: str,
    required_checks: Sequence[str] | None,
    allowed_skips: Sequence[str] | None,
    deploy_dev_required_checks: Sequence[str],
) -> tuple[list[str], list[str]]:
    if required_checks:
        required = list(required_checks)
        default_allowed_skips = required
    elif workflow and workflow.strip().lower() == DEFAULT_WORKFLOW.lower():
        required = list(deploy_dev_required_checks)
        default_allowed_skips = []
    else:
        required = []
        default_allowed_skips = []
    if allowed_skips is None:
        allowed_skips = default_allowed_skips
    return required, allowed_skip_names(required, allowed_skips)


def _required_blockers(
    run: Run, required: Sequence[str], allowed_skips: Sequence[str] | None
) -> list[tuple[str, str]]:
    skip_policy = allowed_skip_names(required, allowed_skips)
    by_name = run.job_map()
    blockers: list[tuple[str, str]] = []
    for name in required:
        job = by_name.get(name)
        if job is None:
            blockers.append((name, "missing"))
            continue
        status = (job.status or "").lower()
        conclusion = (job.conclusion or "").lower()
        if status == "completed" and conclusion == "success":
            continue
        if status == "completed" and conclusion == "skipped" and name in skip_policy:
            continue
        blockers.append((name, conclusion or status or "pending"))
    return blockers


def _format_blockers(blockers: Sequence[tuple[str, str]]) -> str:
    return ", ".join(f"{name} ({state})" for name, state in blockers)


def _append_deployment_note(
    verdict: Verdict, blockers: Sequence[tuple[str, str]]
) -> None:
    for name, _state in blockers:
        if name == "Deploy to Dev":
            verdict.reason += "; no verified deployment was observed"
            return


def _failed_verdict(
    base: Verdict,
    failed: Sequence[Job],
    blockers: Sequence[tuple[str, str]],
    reason_prefix: str = "required job failed",
) -> Verdict:
    base.result = FAILED
    base.failing_jobs = [job.name for job in failed]
    failed_labels = [(job.name, (job.conclusion or "failure").lower()) for job in failed]
    base.reason = reason_prefix + ": " + _format_blockers(failed_labels)
    downstream: list[tuple[str, str]] = []
    for item in blockers:
        if item[0] not in base.failing_jobs:
            downstream.append(item)
    if downstream:
        base.reason += "; blocked required job(s): " + _format_blockers(downstream)
    _append_deployment_note(base, blockers)
    return base


def _failure_sets(run: Run, required: Sequence[str]) -> tuple[list[Job], list[Job]]:
    failed_all: list[Job] = []
    failed_required: list[Job] = []
    for job in run.jobs:
        if not job.is_genuine_failure:
            continue
        failed_all.append(job)
        if job.name in required:
            failed_required.append(job)
    return failed_all, failed_required


def _has_skipped_required(run: Run, required: Sequence[str]) -> bool:
    for job in run.jobs:
        if job.name in required and job.is_skipped:
            return True
    return False


def _blocked_verdict(
    base: Verdict, blockers: Sequence[tuple[str, str]], result: str
) -> Verdict:
    base.result = result
    for name, _state in blockers:
        base.failing_jobs.append(name)
    base.reason = "required job(s) blocked readiness: " + _format_blockers(blockers)
    _append_deployment_note(base, blockers)
    return base


def _terminal_verdict(
    run: Run, base: Verdict, blockers: Sequence[tuple[str, str]]
) -> Verdict:
    if run.is_cancelled:
        if blockers:
            return _blocked_verdict(base, blockers, SUPERSEDED)
        base.result = SUPERSEDED
        base.reason = "run cancelled"
        return base
    if not blockers:
        base.result = GREEN
        base.reason = "all required jobs reached policy-allowed conclusions"
        return base
    return _blocked_verdict(base, blockers, NO_VERDICT)


def classify(
    run: Run,
    required: Sequence[str],
    allowed_skips: Sequence[str] | None = None,
) -> Verdict | None:
    """Classify one poll using required jobs and job-specific skip policy."""
    required_names = list(required)
    blockers = _required_blockers(run, required_names, allowed_skips)
    failed_all, failed_required = _failure_sets(run, required_names)
    base = Verdict(
        result="",
        run_id=run.run_id,
        workflow=run.workflow,
        branch=run.branch,
        required=required_names,
    )
    if failed_required:
        return _failed_verdict(base, failed_required, blockers)
    if not run.is_completed:
        return None
    if failed_all and (blockers or _has_skipped_required(run, required_names)):
        reason = "required job gated behind failed job(s)"
        return _failed_verdict(base, failed_all, blockers, reason)
    return _terminal_verdict(run, base, blockers)
