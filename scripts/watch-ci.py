#!/usr/bin/env python3
"""Blocking, token-cheap GitHub Actions CI watcher for on-call.

Ported from PocketShell's `scripts/watch-ci.py`, adapted to this repository's
`Deploy Dev` workflow. The process polls a single GitHub Actions run and wakes
the caller once with a compact verdict, so on-call never has to sleep, call
`gh run watch`, or repeatedly poll `gh run list/view` across agent turns.

Outcome / exit-code contract (identical to PocketShell):

    0  green        every required job present and successful/appropriately skipped
    1  failed       a genuine required-job/run failure (wins over later cancel)
    2  hang         no job-state progress deadline or max wall-clock deadline hit
    3  unresolved   input/run resolution failed or gh CLI failures exceed budget
    4  superseded   cancelled run demonstrably replaced by a newer matching run
    5  no_verdict   cancellation with no newer run, or completed-but-not-green

The module is import-safe: importing it never starts polling. Every external
dependency (the `gh` CLI and the clock) is injectable so the behaviour can be
driven by deterministic offline tests with fake GitHub responses and a virtual
clock.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Outcome contract
# --------------------------------------------------------------------------- #

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

# A job conclusion that is a genuine failure (as opposed to `cancelled`, which
# is handled honestly as superseded / no-verdict, or `skipped`, which is
# acceptable only when nothing failed).
GENUINE_FAILURE_CONCLUSIONS = frozenset(
    {"failure", "timed_out", "startup_failure", "action_required"}
)
# Terminal conclusions that let a required job count toward `green`.
ACCEPTABLE_CONCLUSIONS = frozenset({"success", "skipped"})

DEFAULT_WORKFLOW = "Deploy Dev"

# The ten exact required job names of `.github/workflows/deploy-dev.yml` for a
# post-push `Deploy Dev` run on `main`. All ten must resolve to success (or an
# appropriate skip) before the watcher reports `green`. A missing required job
# is never green. Scheduled Playwright and manual production deployment are
# separate operational workflows and are intentionally NOT in this set.
DEPLOY_DEV_REQUIRED_CHECKS: tuple[str, ...] = (
    "Deploy Gates (migrations / OpenAPI / system check / static)",
    "Unit & Integration Tests (shard 1/4)",
    "Unit & Integration Tests (shard 2/4)",
    "Unit & Integration Tests (shard 3/4)",
    "Unit & Integration Tests (shard 4/4)",
    "Playwright Core E2E (shard 1/4)",
    "Playwright Core E2E (shard 2/4)",
    "Playwright Core E2E (shard 3/4)",
    "Playwright Core E2E (shard 4/4)",
    "Deploy to Dev",
)

# Maintained, website-relevant infrastructure signatures. A captured failure
# signature is marked `likely_infra` only when it matches one of these. Ordinary
# Python/Django/test assertions deliberately do NOT match, so the watcher never
# mislabels a real test regression as flaky infrastructure.
INFRA_SIGNATURE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Runner / process termination
        r"the runner has received a shutdown signal",
        r"the operation was canceled",
        r"received request to deprovision",
        r"lost communication with the server",
        r"the hosted runner .* lost communication",
        r"the runner.*was terminated",
        # GitHub / registry / network resolution failures
        r"could not resolve host",
        r"temporary failure in name resolution",
        r"tls handshake timeout",
        r"connection reset by peer",
        r"\bi/o timeout\b",
        r"503 service unavailable",
        r"error pinging docker registry",
        r"failed to solve.*(registry|resolve)",
        # Docker daemon / build infrastructure failures
        r"cannot connect to the docker daemon",
        r"docker: error during connect",
        r"error building image",
        r"buildkit",
        # Disk exhaustion
        r"no space left on device",
        r"disk quota exceeded",
        # Playwright browser / runner startup infrastructure failures
        r"executable doesn't exist at .*(chromium|chrome|firefox|webkit)",
        r"host system is missing dependencies to run browsers",
        r"failed to launch.*browser",
        r"browsertype\.launch.*(closed|crashed)",
    )
)

# Log lines that are echoed shell source or generic runner wrappers rather than
# executed failure evidence. These are ignored when capturing a signature so the
# watcher never fabricates evidence from a command that merely *prints* an
# error-looking string.
_ECHO_SOURCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^\+?\s*echo\b",  # `echo "..."` and xtrace `+ echo "..."`
        r"^\+ ",  # bash xtrace of any command
        r"^##\[",  # ##[group] / ##[endgroup] / ##[error] / ##[warning]
        r"^\s*run\b.*\|?\s*$",  # the "Run <cmd>" step header block
        r"^\s*shell:\s*",
        r"process completed with exit code",  # generic runner wrapper
        r"^\s*with:\s*$",
        r"^\s*env:\s*$",
    )
)

# Lines that look like real, executed failure evidence when they are not echoed
# source. Used to pick a non-infra signature when nothing matches the infra set.
_EVIDENCE_HINT_RE = re.compile(
    r"(assertionerror|traceback \(most recent call last\)|"
    r"\berror\b|\bexception\b|\bfailed\b|^e\s{2,}|assert\s)",
    re.IGNORECASE,
)

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


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
            "required": self.required,
            "failing_jobs": self.failing_jobs,
            "signature": self.signature,
            "likely_infra": self.likely_infra,
            "reason": self.reason,
            "newer_run_id": self.newer_run_id,
            "polls": self.polls,
            "elapsed_s": round(self.elapsed_s, 3),
        }


# --------------------------------------------------------------------------- #
# Injected dependencies
# --------------------------------------------------------------------------- #


class GhError(RuntimeError):
    """Raised when a `gh` invocation fails or returns unparseable output."""


class ResolutionError(RuntimeError):
    """Raised when the run to watch cannot be resolved from the inputs."""


GhRunner = Callable[[Sequence[str]], str]


class RealClock:
    """Wall-clock backed clock; `sleep` naturally advances `now`."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class VirtualClock:
    """Deterministic clock for tests; `sleep` advances virtual time only."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += max(0.0, seconds)


def make_gh_runner(gh_binary: str = "gh") -> GhRunner:
    def run(args: Sequence[str]) -> str:
        result = subprocess.run(
            [gh_binary, *args],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise GhError(
                f"`{gh_binary} {' '.join(args)}` exited {result.returncode}: "
                f"{result.stderr.strip()[:200]}"
            )
        return result.stdout

    return run


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def parse_run_payload(payload: dict[str, Any], *, fallback_run_id: str = "") -> Run:
    jobs = tuple(
        Job(
            name=str(job.get("name") or ""),
            status=str(job.get("status") or ""),
            conclusion=str(job.get("conclusion") or ""),
            url=str(job.get("url") or ""),
        )
        for job in payload.get("jobs", [])
        if job.get("name")
    )
    run_id = str(payload.get("databaseId") or fallback_run_id or "")
    return Run(
        run_id=run_id,
        status=str(payload.get("status") or ""),
        conclusion=str(payload.get("conclusion") or ""),
        workflow=str(payload.get("workflowName") or ""),
        branch=str(payload.get("headBranch") or ""),
        created_at=str(payload.get("createdAt") or ""),
        jobs=jobs,
    )


def run_fingerprint(run: Run) -> tuple[Any, ...]:
    """Aggregate job-state signature; a change is treated as progress.

    A rerun whose jobs reset from a terminal conclusion back to queued /
    in_progress changes this fingerprint, so the reset counts as progress and
    the watcher keeps watching instead of latching a stale verdict.
    """

    return (
        (run.status or "").lower(),
        tuple(
            sorted(
                (job.name, (job.status or "").lower(), (job.conclusion or "").lower())
                for job in run.jobs
            )
        ),
    )


def clean_log_line(raw_line: str) -> str:
    parts = raw_line.rstrip("\n").split("\t", 2)
    content = parts[2] if len(parts) == 3 else raw_line.rstrip("\n")
    content = content.lstrip("﻿")
    content = ANSI_RE.sub("", content)
    content = content.lstrip("﻿")
    content = TIMESTAMP_RE.sub("", content)
    return content.rstrip()


def _is_echoed_source(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return any(pattern.search(stripped) for pattern in _ECHO_SOURCE_PATTERNS)


def extract_signature(log_text: str) -> tuple[str | None, bool]:
    """Return (signature, likely_infra) from executed failed-step evidence.

    Echoed shell source and generic runner wrappers are ignored so the watcher
    never fabricates a signature from a command that merely prints an
    error-looking string. `likely_infra` is True only when a captured evidence
    line matches a maintained website-relevant infra pattern; ordinary
    Python/Django assertions stay non-infra.
    """

    evidence: list[str] = []
    for raw_line in log_text.splitlines():
        content = clean_log_line(raw_line)
        if not content.strip():
            continue
        if _is_echoed_source(content):
            continue
        evidence.append(content.strip())

    # First, prefer an infra match anywhere in the executed evidence.
    for line in evidence:
        for pattern in INFRA_SIGNATURE_PATTERNS:
            if pattern.search(line):
                return line[:300], True

    # Otherwise capture the first error-looking executed line (non-infra).
    for line in evidence:
        if _EVIDENCE_HINT_RE.search(line):
            return line[:300], False

    return None, False


# --------------------------------------------------------------------------- #
# Verdict classification
# --------------------------------------------------------------------------- #


def classify(run: Run, required: Sequence[str]) -> Verdict | None:
    """Classify a polled run. Returns None to keep watching.

    Precedence rules:
    - A genuine required-job failure wins even over a later cancellation.
    - `green` requires every required job present, terminal, and
      success/appropriately-skipped; a missing required job is never green.
    - A skipped required job is acceptable only when nothing failed; if it was
      gated behind any failed job the run reports failure and names the job.
    - Cancellation is honest: superseded only when a newer matching run exists
      (resolved by the caller), otherwise no_verdict. Neither is green/failed.
    """

    required = list(required)
    by_name = run.job_map()
    failed_all = [job for job in run.jobs if job.is_genuine_failure]
    failed_required = [job for job in failed_all if job.name in required]

    base = Verdict(
        result="",
        run_id=run.run_id,
        workflow=run.workflow,
        branch=run.branch,
        required=required,
    )

    # 1. Genuine required-job failure — decide immediately, before completion or
    #    any later cancellation can mask it.
    if failed_required:
        base.result = FAILED
        base.failing_jobs = [job.name for job in failed_required]
        base.reason = "required job failed: " + ", ".join(base.failing_jobs)
        return base

    # Do not commit to green/failed/cancel classification until GitHub reports
    # the run terminal. Required jobs may still be running.
    if not run.is_completed:
        return None

    present = [name for name in required if name in by_name]
    missing = [name for name in required if name not in by_name]
    required_jobs = [by_name[name] for name in present]
    skipped_required = [job for job in required_jobs if job.is_skipped]

    all_required_acceptable = (
        not missing
        and all((job.status or "").lower() == "completed" for job in required_jobs)
        and all((job.conclusion or "").lower() in ACCEPTABLE_CONCLUSIONS for job in required_jobs)
    )

    # 2. Green — but a skipped required job is acceptable only when nothing
    #    failed. If something failed and gated a skip, that is a failure.
    if all_required_acceptable:
        if failed_all and skipped_required:
            base.result = FAILED
            base.failing_jobs = [job.name for job in failed_all]
            base.reason = (
                "required job skipped behind failed job(s): "
                + ", ".join(base.failing_jobs)
            )
            return base
        base.result = GREEN
        base.reason = "all required jobs succeeded or were appropriately skipped"
        return base

    # 3. A non-required failure that gated a required job (skipped or missing).
    if failed_all and (skipped_required or missing):
        base.result = FAILED
        base.failing_jobs = [job.name for job in failed_all]
        base.reason = "required job gated behind failed job(s): " + ", ".join(base.failing_jobs)
        return base

    # 4. Cancellation without a genuine failure — honest superseded/no_verdict
    #    is resolved by the caller (needs a newer-run probe).
    if run.is_cancelled:
        base.result = SUPERSEDED  # provisional; caller downgrades to no_verdict
        base.reason = "run cancelled"
        return base

    # 5. Completed, not cancelled, no failure, but not green (e.g. a required
    #    job is missing from an otherwise-complete run). Not a pass, not a
    #    failure — no trustworthy verdict.
    base.result = NO_VERDICT
    if missing:
        base.reason = "completed run missing required job(s): " + ", ".join(missing)
    else:
        base.reason = "completed run did not reach a green required-job state"
    return base


# --------------------------------------------------------------------------- #
# Watcher
# --------------------------------------------------------------------------- #


class CIWatcher:
    def __init__(
        self,
        *,
        runner: GhRunner,
        clock: RealClock | VirtualClock | None = None,
        required_checks: Sequence[str] | None = None,
        workflow: str = DEFAULT_WORKFLOW,
        branch: str | None = None,
        repo: str | None = None,
        interval: float = 15.0,
        no_progress_timeout: float = 900.0,
        max_wall_clock: float = 5400.0,
        gh_retry_budget: int = 5,
        quiet: bool = False,
        log_file: Path | None = None,
    ) -> None:
        self.runner = runner
        self.clock = clock or RealClock()
        self.workflow = workflow
        self.branch = branch
        self.repo = repo
        self.interval = interval
        self.no_progress_timeout = no_progress_timeout
        self.max_wall_clock = max_wall_clock
        self.gh_retry_budget = gh_retry_budget
        self.quiet = quiet
        self.log_file = log_file
        self.last_run: Run | None = None

        if required_checks:
            self.required = list(required_checks)
        elif workflow and workflow.strip().lower() == DEFAULT_WORKFLOW.lower():
            self.required = list(DEPLOY_DEV_REQUIRED_CHECKS)
        else:
            self.required = []

    # -- gh helpers -------------------------------------------------------- #

    def _gh_json(self, args: Sequence[str]) -> Any:
        full = list(args)
        if self.repo:
            full.extend(["--repo", self.repo])
        raw = self.runner(full)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:  # pragma: no cover - defensive
            raise GhError(f"invalid JSON from `gh {' '.join(args)}`: {exc}") from exc

    def _gh_text(self, args: Sequence[str]) -> str:
        full = list(args)
        if self.repo:
            full.extend(["--repo", self.repo])
        return self.runner(full)

    # -- run resolution ---------------------------------------------------- #

    def _resolve_run_id(self, run_id: str | None) -> str:
        if run_id:
            return str(run_id)
        if not self.branch:
            raise ResolutionError("either --run-id or --branch is required")

        args = [
            "run",
            "list",
            "--workflow",
            self.workflow,
            "--branch",
            self.branch,
            "--limit",
            "1",
            "--json",
            "databaseId,status,createdAt,workflowName,headBranch",
        ]
        failures = 0
        while True:
            try:
                payload = self._gh_json(args)
            except GhError:
                failures += 1
                if failures > self.gh_retry_budget:
                    raise ResolutionError("could not resolve a run from --branch (gh failures exceeded budget)")
                self.clock.sleep(self.interval)
                continue
            if not payload:
                raise ResolutionError(
                    f"no {self.workflow!r} run found for branch {self.branch!r}"
                )
            return str(payload[0].get("databaseId") or "")

    def _poll_run(self, run_id: str) -> Run:
        args = [
            "run",
            "view",
            str(run_id),
            "--json",
            "status,conclusion,jobs,workflowName,headBranch,databaseId,createdAt",
        ]
        payload = self._gh_json(args)
        return parse_run_payload(payload, fallback_run_id=str(run_id))

    def _probe_newer_run(self, run: Run) -> str | None:
        workflow = run.workflow or self.workflow
        branch = run.branch or self.branch
        if not workflow or not branch:
            return None
        args = [
            "run",
            "list",
            "--workflow",
            workflow,
            "--branch",
            branch,
            "--limit",
            "20",
            "--json",
            "databaseId,createdAt,status",
        ]
        try:
            payload = self._gh_json(args)
        except GhError:
            return None
        newer: str | None = None
        for entry in payload:
            candidate_id = str(entry.get("databaseId") or "")
            if not candidate_id or candidate_id == run.run_id:
                continue
            created = str(entry.get("createdAt") or "")
            if run.created_at and created and created <= run.created_at:
                continue
            newer = candidate_id
            break
        return newer

    def _capture_signature(self, run: Run) -> tuple[str | None, bool]:
        try:
            log_text = self._gh_text(["run", "view", run.run_id, "--log-failed"])
        except GhError:
            return None, False
        return extract_signature(log_text)

    # -- output ------------------------------------------------------------ #

    def _log(self, message: str) -> None:
        if not self.log_file:
            return
        with self.log_file.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip("\n") + "\n")

    def _emit_state_change(self, poll: int, run: Run) -> None:
        if not self.quiet:
            compact = ", ".join(
                f"{job.name.split(' (')[0][:24]}={(job.conclusion or job.status or '?')}"
                for job in run.jobs
            )
            print(f"[watch-ci] poll {poll}: {run.status or '?'} | {compact}", file=sys.stderr)
        job_states = [(job.name, job.status, job.conclusion) for job in run.jobs]
        self._log(f"poll {poll} status={run.status} jobs={job_states}")

    # -- main loop --------------------------------------------------------- #

    def watch(self, run_id: str | None = None) -> Verdict:
        start = self.clock.now()

        try:
            resolved = self._resolve_run_id(run_id)
        except ResolutionError as exc:
            return self._finalize(
                Verdict(result=UNRESOLVED, reason=str(exc), required=self.required),
                polls=0,
                start=start,
            )
        if not resolved:
            return self._finalize(
                Verdict(
                    result=UNRESOLVED,
                    reason="run resolution returned an empty run id",
                    required=self.required,
                ),
                polls=0,
                start=start,
            )

        last_fingerprint: tuple[Any, ...] | None = None
        last_progress = start
        consecutive_gh_failures = 0
        polls = 0
        last_run: Run | None = None

        while True:
            try:
                run = self._poll_run(resolved)
                consecutive_gh_failures = 0
            except GhError:
                consecutive_gh_failures += 1
                if consecutive_gh_failures > self.gh_retry_budget:
                    return self._finalize(
                        Verdict(
                            result=UNRESOLVED,
                            run_id=resolved,
                            reason="gh CLI failures exceeded the retry budget while polling",
                            required=self.required,
                        ),
                        polls=polls,
                        start=start,
                    )
                if self._deadline_exceeded(start, last_progress):
                    return self._hang_verdict(resolved, last_run, polls, start)
                self.clock.sleep(self.interval)
                continue

            polls += 1
            last_run = run
            self.last_run = run

            fingerprint = run_fingerprint(run)
            if fingerprint != last_fingerprint:
                last_fingerprint = fingerprint
                last_progress = self.clock.now()
                self._emit_state_change(polls, run)

            verdict = classify(run, self.required)
            if verdict is not None:
                verdict = self._resolve_terminal(verdict, run)
                return self._finalize(verdict, polls=polls, start=start)

            if self._deadline_exceeded(start, last_progress):
                return self._hang_verdict(resolved, run, polls, start)

            self.clock.sleep(self.interval)

    # -- helpers ----------------------------------------------------------- #

    def _deadline_exceeded(self, start: float, last_progress: float) -> bool:
        now = self.clock.now()
        if now - last_progress >= self.no_progress_timeout:
            return True
        if now - start >= self.max_wall_clock:
            return True
        return False

    def _hang_verdict(self, run_id: str, run: Run | None, polls: int, start: float) -> Verdict:
        now = self.clock.now()
        if run is not None and now - start >= self.max_wall_clock:
            reason = f"maximum wall-clock deadline reached after {now - start:.0f}s without a verdict"
        else:
            reason = f"no job-state progress within {self.no_progress_timeout:.0f}s"
        verdict = Verdict(
            result=HANG,
            run_id=run_id,
            reason=reason,
            required=self.required,
            workflow=run.workflow if run else self.workflow,
            branch=run.branch if run else (self.branch or ""),
        )
        return self._finalize(verdict, polls=polls, start=start)

    def _resolve_terminal(self, verdict: Verdict, run: Run) -> Verdict:
        if verdict.result == FAILED:
            signature, likely_infra = self._capture_signature(run)
            verdict.signature = signature
            verdict.likely_infra = likely_infra
        elif verdict.result == SUPERSEDED:
            newer = self._probe_newer_run(run)
            if newer:
                verdict.newer_run_id = newer
                verdict.reason = f"run cancelled and superseded by newer run {newer}"
            else:
                verdict.result = NO_VERDICT
                verdict.reason = (
                    "run cancelled with no demonstrably newer matching run; no verdict"
                )
        return verdict

    def _finalize(self, verdict: Verdict, *, polls: int, start: float) -> Verdict:
        verdict.polls = polls
        verdict.elapsed_s = self.clock.now() - start
        if not verdict.required:
            verdict.required = self.required
        if not verdict.workflow:
            verdict.workflow = self.workflow
        if not verdict.branch and self.branch:
            verdict.branch = self.branch
        return verdict


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def render_summary(verdict: Verdict, run: Run | None) -> list[str]:
    lines = [
        f"CI watch result: {verdict.result} (exit {verdict.exit_code})",
        f"Workflow: {verdict.workflow or '?'} | Branch: {verdict.branch or '?'} | Run: {verdict.run_id or '?'}",
        f"Reason: {verdict.reason}",
    ]
    if verdict.required:
        by_name = run.job_map() if run else {}
        lines.append(f"Required checks ({len(verdict.required)}):")
        for name in verdict.required:
            job = by_name.get(name)
            state = "missing" if job is None else (job.conclusion or job.status or "pending")
            lines.append(f"  - {name}: {state}")
    if verdict.failing_jobs:
        lines.append("Failing jobs: " + ", ".join(verdict.failing_jobs))
    if verdict.signature:
        lines.append(f"Signature: {verdict.signature} (likely_infra={str(verdict.likely_infra).lower()})")
    if verdict.newer_run_id:
        lines.append(f"Newer run: {verdict.newer_run_id}")
    return lines


def render_json_line(verdict: Verdict) -> str:
    return json.dumps(verdict.to_json_dict(), separators=(",", ":"))


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id", help="Watch this GitHub Actions run id")
    target.add_argument("--branch", help="Resolve the newest workflow run on this branch")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW, help="Workflow name (default: Deploy Dev)")
    parser.add_argument("--repo", default=None, help="owner/name repo (defaults to gh's current repo)")
    parser.add_argument("--interval", type=float, default=15.0, help="Poll interval in seconds")
    parser.add_argument(
        "--no-progress-timeout",
        type=float,
        default=900.0,
        help="Hang if no job-state change within this many seconds",
    )
    parser.add_argument(
        "--max-wall-clock",
        type=float,
        default=5400.0,
        help="Hang if no verdict within this many seconds total",
    )
    parser.add_argument(
        "--required-check",
        action="append",
        dest="required_checks",
        default=None,
        help="Override required job names (repeatable)",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="Append heartbeats here, never to stdout")
    parser.add_argument("--quiet", action="store_true", help="Emit no per-poll stderr output")
    parser.add_argument("--gh-binary", default="gh", help="gh executable to invoke")
    parser.add_argument("--gh-retry-budget", type=int, default=5, help="Consecutive gh failures tolerated")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    watcher = CIWatcher(
        runner=make_gh_runner(args.gh_binary),
        clock=RealClock(),
        required_checks=args.required_checks,
        workflow=args.workflow,
        branch=args.branch,
        repo=args.repo,
        interval=args.interval,
        no_progress_timeout=args.no_progress_timeout,
        max_wall_clock=args.max_wall_clock,
        gh_retry_budget=args.gh_retry_budget,
        quiet=args.quiet,
        log_file=args.log_file,
    )

    verdict = watcher.watch(run_id=args.run_id)

    # No extra GitHub query: reuse the last run the watcher already polled.
    for line in render_summary(verdict, watcher.last_run):
        print(line)
    print(render_json_line(verdict))
    return verdict.exit_code


if __name__ == "__main__":
    sys.exit(main())
