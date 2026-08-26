"""Build scheduled Playwright failure issue bodies from GitHub Actions logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

MAX_CONTEXT_LINES = 24
MAX_CONTEXT_CHARS = 4000

LOG_UNAVAILABLE_NOTE = "Failed logs were not available when this notification ran."
JOBS_UNAVAILABLE_NOTE = "Failure details were not available when this notification ran."

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s+")
# ``gh run view --log-failed`` prefixes every line with ``job\tstep\t`` before
# the timestamp. Job-scoped logs (``gh api .../jobs/{id}/logs``) start straight
# at the timestamp, so the prefix is only stripped when it is really there —
# otherwise tab characters inside pytest output would eat the line content.
JOB_PREFIX_RE = re.compile(r"^[^\t]+\t[^\t]+\t(?=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")
JOB_ID_FROM_URL_RE = re.compile(r"/job/(\d+)")
SENSITIVE_RE = re.compile(
    r"(?i)("
    r"authorization|credential|passwd|password|private[-_ ]?key|secret|token|"
    r"api[-_ ]?key|access[-_ ]?key|session[-_ ]?key"
    r")"
)


@dataclass(frozen=True)
class FailedJob:
    name: str
    url: str = ""
    job_id: str = ""


@dataclass
class JobDiagnostic:
    name: str
    url: str = ""
    node_ids: list[str] = field(default_factory=list)
    context_lines: list[str] = field(default_factory=list)
    note: str = ""


class GhCommandError(RuntimeError):
    """Raised when a best-effort GitHub CLI lookup fails."""


def run_gh(args: Sequence[str], *, gh_binary: str = "gh") -> str:
    result = subprocess.run(
        [gh_binary, *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise GhCommandError(f"`gh {' '.join(args)}` failed with exit code {result.returncode}")
    return result.stdout


def _gh_run_args(run_id: str, repo: str | None = None) -> list[str]:
    args = ["run", "view", run_id]
    if repo:
        args.extend(["--repo", repo])
    return args


def load_failed_jobs(
    run_id: str,
    *,
    repo: str | None = None,
    command_runner: Callable[[Sequence[str]], str] | None = None,
) -> tuple[list[FailedJob], str]:
    runner = command_runner or run_gh
    try:
        output = runner([*_gh_run_args(run_id, repo), "--json", "jobs"])
        payload = json.loads(output)
    except (GhCommandError, json.JSONDecodeError, OSError):
        return [], "Failure details were not available when this notification ran."

    jobs = []
    for job in payload.get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        name = str(job.get("name") or "Unnamed failed job")
        url = str(job.get("url") or "")
        jobs.append(FailedJob(name=name, url=url, job_id=_job_id(job)))

    if not jobs:
        return [], JOBS_UNAVAILABLE_NOTE

    return jobs, ""


def _job_id(job: dict) -> str:
    raw_id = job.get("databaseId") or job.get("id") or ""
    if raw_id:
        return str(raw_id)
    match = JOB_ID_FROM_URL_RE.search(str(job.get("url") or ""))
    return match.group(1) if match else ""


def load_job_log(
    job: FailedJob,
    *,
    repo: str | None = None,
    command_runner: Callable[[Sequence[str]], str] | None = None,
) -> tuple[str, str]:
    """Fetch one failed job's log.

    The run-scoped archive (``gh run view <run_id> --log-failed``) is only
    served once the whole run has finished, and the ``notify`` job is part of
    that run \u2014 so it can never succeed there (issue #1462). Job-scoped logs are
    served as soon as the individual job completes, which is exactly what we
    need while the parent run is still in progress.
    """
    if not job.job_id or not repo:
        return "", LOG_UNAVAILABLE_NOTE
    runner = command_runner or run_gh
    try:
        return runner(["api", f"repos/{repo}/actions/jobs/{job.job_id}/logs"]), ""
    except (GhCommandError, OSError):
        return "", LOG_UNAVAILABLE_NOTE


def clean_log_line(raw_line: str) -> str:
    """Strip the optional ``job\\tstep\\t`` prefix, ANSI codes and timestamp."""
    line = raw_line.rstrip("\n")
    if JOB_PREFIX_RE.match(line):
        line = line.split("\t", 2)[2]
    content = line.lstrip("\ufeff")
    content = ANSI_RE.sub("", content)
    content = content.lstrip("\ufeff")
    content = TIMESTAMP_RE.sub("", content)
    return content.rstrip()


def extract_log_lines(log_text: str) -> list[str]:
    """Return non-empty cleaned content lines from a job or run log."""
    lines: list[str] = []
    for raw_line in log_text.splitlines():
        content = clean_log_line(raw_line)
        if content.strip():
            lines.append(content)
    return lines


def extract_pytest_node_ids(lines: Sequence[str]) -> list[str]:
    node_ids: list[str] = []
    seen: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("FAILED "):
            continue
        candidate = stripped.removeprefix("FAILED ").split(" - ", 1)[0].strip()
        if not candidate.startswith("playwright_tests/") or "::" not in candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        node_ids.append(candidate)

    return node_ids


def _is_sensitive_line(line: str) -> bool:
    return bool(SENSITIVE_RE.search(line))


def sanitize_context_lines(lines: Sequence[str]) -> list[str]:
    sanitized: list[str] = []
    in_env_block = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "env:":
            if not sanitized or sanitized[-1] != "[omitted environment block]":
                sanitized.append("[omitted environment block]")
            in_env_block = True
            continue
        if in_env_block and line.startswith("  "):
            continue
        in_env_block = False

        if _is_sensitive_line(line):
            sanitized.append("[redacted sensitive log line]")
            continue

        sanitized.append(line.replace("```", "'''"))

    return sanitized


def _context_window(lines: Sequence[str], centers: Sequence[int], *, before: int, after: int) -> list[str]:
    selected: list[str] = []
    seen: set[int] = set()

    for center in centers:
        start = max(0, center - before)
        end = min(len(lines), center + after + 1)
        for index in range(start, end):
            if index in seen:
                continue
            seen.add(index)
            selected.append(lines[index])
            if len(selected) >= MAX_CONTEXT_LINES:
                return selected

    return selected


def extract_bounded_context(lines: Sequence[str]) -> list[str]:
    if not lines:
        return []

    # Job-scoped logs (issue #1462) carry the whole job, including setup steps
    # whose shell echoes start with ">" and would otherwise be mistaken for
    # pytest failure detail. When pytest reached its FAILURES banner, only look
    # for detail from there on; infra failures without a banner are unaffected.
    failures_banner = [
        index for index, line in enumerate(lines) if " FAILURES " in line
    ]
    detail_start = failures_banner[-1] if failures_banner else 0
    failure_detail_centers = [
        index
        for index, line in enumerate(lines)
        if index >= detail_start
        and (
            line.lstrip().startswith((">", "E   ", "E       "))
            or "AssertionError" in line
            or "Error:" in line
        )
    ][:4]
    summary_centers = [
        index
        for index, line in enumerate(lines)
        if line.strip().startswith("FAILED playwright_tests/")
        or "short test summary info" in line
        or " FAILURES " in line
    ]

    if failure_detail_centers:
        context = _context_window(lines, failure_detail_centers, before=2, after=2)
        remaining = MAX_CONTEXT_LINES - len(context)
        if remaining > 0 and summary_centers:
            context.extend(_context_window(lines, summary_centers, before=1, after=1)[:remaining])
    elif summary_centers:
        context = _context_window(lines, summary_centers, before=2, after=2)
    else:
        context = list(lines[-MAX_CONTEXT_LINES:])

    sanitized = sanitize_context_lines(context)
    text = "\n".join(sanitized)
    if len(text) <= MAX_CONTEXT_CHARS:
        return sanitized

    truncated = text[: MAX_CONTEXT_CHARS - len("\n... [truncated]")]
    return [*truncated.rstrip().splitlines(), "... [truncated]"]


def build_job_diagnostic(job: FailedJob, lines: Sequence[str], *, note: str = "") -> JobDiagnostic:
    return JobDiagnostic(
        name=job.name,
        url=job.url,
        node_ids=extract_pytest_node_ids(lines),
        context_lines=extract_bounded_context(lines),
        note=note,
    )


def collect_failed_job_diagnostics(
    run_id: str,
    *,
    repo: str | None = None,
    command_runner: Callable[[Sequence[str]], str] | None = None,
) -> tuple[list[JobDiagnostic], str]:
    jobs, jobs_note = load_failed_jobs(run_id, repo=repo, command_runner=command_runner)
    if not jobs:
        return [], jobs_note

    diagnostics: list[JobDiagnostic] = []
    for job in jobs:
        log_text, log_note = load_job_log(job, repo=repo, command_runner=command_runner)
        diagnostics.append(
            build_job_diagnostic(job, extract_log_lines(log_text), note=log_note)
        )

    return diagnostics, ""


def format_failure_body(
    *,
    branch: str,
    run_url: str,
    commit_sha: str,
    event_name: str,
    diagnostics: Sequence[JobDiagnostic],
    fallback_note: str = "",
) -> str:
    lines = [
        f"Scheduled Playwright failed on `{branch}`.",
        "",
        f"Run: {run_url}",
        f"Commit: {commit_sha}",
        f"Event: {event_name}",
        "",
        "Failed jobs:",
    ]

    if not diagnostics:
        note = fallback_note or JOBS_UNAVAILABLE_NOTE
        lines.append(f"- {note}")
        return "\n".join(lines).rstrip() + "\n"

    for diagnostic in diagnostics:
        lines.append(f"- {diagnostic.name}")
        if diagnostic.url:
            lines.append(f"  Job: {diagnostic.url}")

    for diagnostic in diagnostics:
        lines.extend(["", f"### {diagnostic.name}"])
        if diagnostic.url:
            lines.append(f"Job: {diagnostic.url}")

        if diagnostic.node_ids:
            lines.extend(["", "Failing tests:"])
            lines.extend(f"- `{node_id}`" for node_id in diagnostic.node_ids)
        else:
            lines.extend(["", "Failing tests: could not extract pytest node IDs from failed logs."])

        if diagnostic.note:
            lines.append(f"Diagnostics note: {diagnostic.note}")

        if diagnostic.context_lines:
            lines.extend(["", "Failure context (bounded):", "```text"])
            lines.extend(diagnostic.context_lines)
            lines.append("```")
        else:
            lines.append("Failure context: unavailable.")

    return "\n".join(lines).rstrip() + "\n"


def default_run_url(*, server_url: str, repository: str, run_id: str) -> str:
    return f"{server_url.rstrip('/')}/{repository}/actions/runs/{run_id}"


def build_notice_from_environment(
    *,
    output_path: Path,
    gh_binary: str = "gh",
) -> int:
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    repository = os.environ.get("GITHUB_REPOSITORY") or os.environ.get("GH_REPO") or ""
    if not run_id or not repository:
        raise RuntimeError("GITHUB_RUN_ID and GITHUB_REPOSITORY/GH_REPO are required")

    diagnostics, fallback_note = collect_failed_job_diagnostics(
        run_id,
        repo=repository,
        command_runner=lambda args: run_gh(args, gh_binary=gh_binary),
    )
    body = format_failure_body(
        branch=os.environ.get("GITHUB_REF_NAME", "unknown"),
        run_url=default_run_url(
            server_url=os.environ.get("GITHUB_SERVER_URL", "https://github.com"),
            repository=repository,
            run_id=run_id,
        ),
        commit_sha=os.environ.get("GITHUB_SHA", "unknown"),
        event_name=os.environ.get("GITHUB_EVENT_NAME", "unknown"),
        diagnostics=diagnostics,
        fallback_note=fallback_note,
    )
    output_path.write_text(body)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--gh-binary", default="gh")
    args = parser.parse_args(argv)

    return build_notice_from_environment(output_path=args.output, gh_binary=args.gh_binary)


if __name__ == "__main__":
    sys.exit(main())
