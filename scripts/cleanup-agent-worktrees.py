#!/usr/bin/env python3
"""Fail-closed lifecycle management for agent Git worktrees.

The default command is a read-only classifier. Destructive operations require
one explicit candidate, a reviewed plan digest, and complete revalidation.
Lifecycle records live in the common Git directory so they survive worktree
removal and role handoffs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROTECTED_SHARED_MAIN = "PROTECTED_SHARED_MAIN"
RETAIN_ACTIVE_LIFECYCLE = "RETAIN_ACTIVE_LIFECYCLE"
RETAIN_ACTIVE_PROCESS = "RETAIN_ACTIVE_PROCESS"
RETAIN_DIRTY = "RETAIN_DIRTY"
RETAIN_UNMERGED_HEAD = "RETAIN_UNMERGED_HEAD"
RETAIN_TERMINAL_EVIDENCE_MISSING = "RETAIN_TERMINAL_EVIDENCE_MISSING"
RETAIN_MISSING_OR_UNCLASSIFIED = "RETAIN_MISSING_OR_UNCLASSIFIED"
ELIGIBLE_REMOVE = "ELIGIBLE_REMOVE"
STALE_REGISTRATION_ELIGIBLE_PRUNE = "STALE_REGISTRATION_ELIGIBLE_PRUNE"

REPO_SLUG = "AI-Shipping-Labs/website"
DEPLOY_WORKFLOW = "Deploy Dev"
LEASE_DIRNAME = "agent-worktree-leases"


class CleanupError(RuntimeError):
    """A fail-closed classification or lifecycle error."""


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class CommandRunner:
    """Small injectable subprocess boundary."""

    def __call__(self, args: Sequence[str], *, cwd: Path | None = None) -> CommandResult:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return CommandResult(completed.stdout, completed.stderr, completed.returncode)


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str = ""
    branch_ref: str = ""
    detached: bool = False
    locked: bool = False
    prunable: bool = False

    @property
    def branch(self) -> str | None:
        prefix = "refs/heads/"
        return self.branch_ref[len(prefix) :] if self.branch_ref.startswith(prefix) else None


@dataclass(frozen=True)
class ProcessUse:
    pid: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ProcessScan:
    complete: bool
    uses: tuple[ProcessUse, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass
class Plan:
    timestamp: str
    actor: str
    mode: str
    repository: str
    common_dir: str
    path: str
    issue: int | None
    branch: str | None
    detached: bool
    head: str
    origin_main: str
    lease_state: str
    terminal_run_id: str | None
    terminal_run_head: str | None
    terminal_result: str | None
    process_ids: list[int]
    process_reasons: list[str]
    classification: str
    reasons: list[str]
    requested_actions: list[str] = field(default_factory=list)
    completed_actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    exit_status: int = 0
    plan_digest: str = ""
    facts: dict[str, Any] = field(default_factory=dict, repr=False)

    def seal(self) -> Plan:
        payload = json.dumps(self.facts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        self.plan_digest = hashlib.sha256(payload.encode()).hexdigest()
        return self

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("facts", None)
        return result


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _decode(value: bytes) -> str:
    return value.decode("utf-8", errors="replace").strip()


def _command_error(args: Sequence[str], result: CommandResult) -> CleanupError:
    detail = _decode(result.stderr) or _decode(result.stdout) or f"exit {result.returncode}"
    return CleanupError(f"{' '.join(shlex.quote(arg) for arg in args)}: {detail}")


def parse_worktree_porcelain(raw: bytes) -> list[Worktree]:
    records: list[Worktree] = []
    fields: dict[str, str] = {}
    for token in raw.split(b"\0"):
        if not token:
            if fields:
                path = fields.get("worktree")
                if not path:
                    raise CleanupError("worktree record has no path")
                records.append(
                    Worktree(
                        path=canonical(path),
                        head=fields.get("HEAD", ""),
                        branch_ref=fields.get("branch", ""),
                        detached="detached" in fields,
                        locked="locked" in fields,
                        prunable="prunable" in fields,
                    )
                )
                fields = {}
            continue
        text = token.decode("utf-8", errors="strict")
        key, _, value = text.partition(" ")
        fields[key] = value
    if fields:
        raise CleanupError("unterminated worktree record")
    return records


def lease_key(path: Path) -> str:
    return hashlib.sha256(os.fsencode(path)).hexdigest()


class ProcessScanner:
    """Linux /proc scanner; incomplete visibility is an explicit veto."""

    def __init__(self, proc_root: Path = Path("/proc"), *, self_pid: int | None = None):
        self.proc_root = proc_root
        self.self_pid = os.getpid() if self_pid is None else self_pid

    @staticmethod
    def _rooted_target(raw: str, candidate: Path) -> bool:
        if not raw.startswith("/"):
            return False
        return is_below(canonical(raw), candidate) or canonical(raw) == candidate

    def _readlink(self, path: Path) -> str:
        return os.readlink(path)

    def scan(self, candidate: Path) -> ProcessScan:
        uses: list[ProcessUse] = []
        error_counts: Counter[str] = Counter()
        try:
            entries = list(self.proc_root.iterdir())
        except OSError as exc:
            return ProcessScan(False, errors=(f"proc-list:{type(exc).__name__}",))

        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            reasons: set[str] = set()
            self_rooted = False
            try:
                cwd_raw = self._readlink(entry / "cwd")
                if self._rooted_target(cwd_raw, candidate):
                    reasons.add("cwd")
                    self_rooted = True
            except FileNotFoundError:
                continue
            except (PermissionError, OSError) as exc:
                error_counts[f"cwd:{type(exc).__name__}"] += 1
                continue

            try:
                for fd in (entry / "fd").iterdir():
                    try:
                        raw = self._readlink(fd)
                    except FileNotFoundError:
                        continue
                    if self._rooted_target(raw, candidate):
                        reasons.add("fd")
                        self_rooted = True
            except FileNotFoundError:
                continue
            except (PermissionError, OSError) as exc:
                error_counts[f"fd:{type(exc).__name__}"] += 1
                continue

            # The classifier's own --path argument is not active use. It is
            # ignored only when neither its cwd nor an fd is rooted there.
            if pid != self.self_pid or self_rooted:
                try:
                    tokens = (entry / "cmdline").read_bytes().split(b"\0")
                    for token in tokens:
                        value = token.decode("utf-8", errors="replace")
                        candidates = [value]
                        if "=" in value:
                            candidates.append(value.split("=", 1)[1])
                        if any(self._rooted_target(item, candidate) for item in candidates):
                            reasons.add("cmdline")
                except FileNotFoundError:
                    continue
                except (PermissionError, OSError) as exc:
                    error_counts[f"cmdline:{type(exc).__name__}"] += 1

            if reasons:
                uses.append(ProcessUse(pid, tuple(sorted(reasons))))

        return ProcessScan(
            complete=not error_counts,
            uses=tuple(sorted(uses, key=lambda use: use.pid)),
            errors=tuple(
                f"proc-visibility:{kind}:count={count}" for kind, count in sorted(error_counts.items())
            ),
        )


class CleanupService:
    def __init__(
        self,
        repo: Path,
        *,
        actor: str,
        runner: Callable[..., CommandResult] | None = None,
        gh_runner: Callable[[Sequence[str]], dict[str, Any]] | None = None,
        process_scanner: ProcessScanner | None = None,
        now: Callable[[], str] = utc_now,
    ):
        self.repo = canonical(repo)
        if not actor.strip():
            raise CleanupError("actor identity must not be blank")
        self.actor = actor
        self.runner = runner or CommandRunner()
        self.gh_runner = gh_runner or self._run_gh
        self.process_scanner = process_scanner or ProcessScanner()
        self.now = now
        self.common_dir = self._resolve_common_dir()
        self.lease_dir = self.common_dir / LEASE_DIRNAME

    def _run(self, args: Sequence[str], *, cwd: Path | None = None, check: bool = True) -> CommandResult:
        result = self.runner(args, cwd=cwd or self.repo)
        if check and result.returncode:
            raise _command_error(args, result)
        return result

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True) -> CommandResult:
        return self._run(("git", *args), cwd=cwd, check=check)

    def _resolve_common_dir(self) -> Path:
        result = self._run(("git", "rev-parse", "--git-common-dir"), cwd=self.repo)
        raw = Path(_decode(result.stdout))
        return canonical(raw if raw.is_absolute() else self.repo / raw)

    def _run_gh(self, args: Sequence[str]) -> dict[str, Any]:
        result = self._run(("gh", *args), cwd=self.repo)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CleanupError("gh returned invalid JSON") from exc

    def worktrees(self) -> list[Worktree]:
        raw = self._git("worktree", "list", "--porcelain", "-z").stdout
        return parse_worktree_porcelain(raw)

    def shared_main(self, worktrees: Sequence[Worktree] | None = None) -> Worktree:
        matches = [wt for wt in (worktrees or self.worktrees()) if wt.branch_ref == "refs/heads/main"]
        if len(matches) != 1:
            raise CleanupError(f"expected exactly one shared main worktree, found {len(matches)}")
        return matches[0]

    def boundary(self, worktrees: Sequence[Worktree] | None = None) -> Path:
        return self.shared_main(worktrees).path / ".claude" / "worktrees"

    def _lease_path(self, path: Path) -> Path:
        return self.lease_dir / f"{lease_key(path)}.json"

    def read_lease(self, path: Path) -> tuple[dict[str, Any] | None, list[str]]:
        lease_path = self._lease_path(path)
        if not lease_path.exists():
            return None, ["lease missing"]
        try:
            data = json.loads(lease_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return None, [f"lease unreadable:{type(exc).__name__}"]
        if not isinstance(data, dict):
            return None, ["lease payload is not an object"]
        required = {"version", "issue", "path", "state", "actor", "created_at", "updated_at"}
        errors = [f"lease missing field:{key}" for key in sorted(required - data.keys())]
        if data.get("version") != 1:
            errors.append("lease version unsupported")
        if data.get("path") != str(path):
            errors.append("lease path mismatch")
        if data.get("state") not in {"active", "terminal"}:
            errors.append("lease state invalid")
        if not isinstance(data.get("issue"), int) or isinstance(data.get("issue"), bool) or data.get("issue", 0) < 1:
            errors.append("lease issue invalid")
        for key in ("actor", "created_at", "updated_at"):
            if not isinstance(data.get(key), str) or not data.get(key):
                errors.append(f"lease {key} invalid")
        return (data if not errors else None), errors

    def _write_lease(self, path: Path, data: dict[str, Any]) -> Path:
        self.lease_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._lease_path(path)
        temporary = target.with_suffix(f".tmp-{os.getpid()}")
        payload = json.dumps(data, sort_keys=True, indent=2) + "\n"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

    def _registered_candidate(self, path: Path) -> Worktree:
        matches = [wt for wt in self.worktrees() if wt.path == path]
        if len(matches) != 1:
            raise CleanupError("candidate must be exactly one registered worktree")
        if path == self.shared_main().path:
            raise CleanupError("shared main cannot have an agent lifecycle lease")
        if not is_below(path, self.boundary()):
            raise CleanupError("candidate is outside the agent worktree boundary")
        return matches[0]

    def create_lease(self, *, path: Path, issue: int, role: str) -> Path:
        path = canonical(path)
        if issue < 1 or not role.strip():
            raise CleanupError("lease issue and role must be valid")
        self._registered_candidate(path)
        existing, errors = self.read_lease(path)
        if existing is not None or not errors or errors != ["lease missing"]:
            raise CleanupError("lease already exists or cannot be safely classified")
        timestamp = self.now()
        return self._write_lease(
            path,
            {
                "version": 1,
                "issue": issue,
                "path": str(path),
                "state": "active",
                "actor": self.actor,
                "role": role,
                "created_at": timestamp,
                "updated_at": timestamp,
            },
        )

    def _validate_run(self, lease: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        terminal = lease.get("terminal")
        if not isinstance(terminal, dict):
            return False, "terminal evidence missing", None
        required = {"merge_sha", "run_id", "run_head_sha", "result", "roles_ended"}
        missing = sorted(required - terminal.keys())
        if missing:
            return False, f"terminal evidence missing:{','.join(missing)}", None
        if terminal.get("result") != "success" or terminal.get("roles_ended") is not True:
            return False, "terminal result or role completion is not successful", None
        try:
            payload = self.gh_runner(
                (
                    "run",
                    "view",
                    str(terminal["run_id"]),
                    "--repo",
                    REPO_SLUG,
                    "--json",
                    "databaseId,status,conclusion,workflowName,headSha",
                )
            )
        except Exception as exc:
            return False, f"run lookup failed:{type(exc).__name__}", None
        valid = (
            str(payload.get("databaseId")) == str(terminal["run_id"])
            and payload.get("status") == "completed"
            and payload.get("conclusion") == "success"
            and payload.get("workflowName") == DEPLOY_WORKFLOW
            and payload.get("headSha") == terminal["run_head_sha"]
        )
        if not valid:
            return False, "run evidence mismatch", payload
        try:
            issue_payload = self.gh_runner(
                (
                    "issue",
                    "view",
                    str(lease["issue"]),
                    "--repo",
                    REPO_SLUG,
                    "--json",
                    "number,state",
                )
            )
        except Exception as exc:
            return False, f"issue lookup failed:{type(exc).__name__}", payload
        if issue_payload.get("number") != lease["issue"] or issue_payload.get("state") != "CLOSED":
            return False, "issue terminal evidence mismatch", payload
        return True, "", payload

    def _is_ancestor(self, older: str, newer: str) -> tuple[bool, str]:
        if not older or not newer:
            return False, "missing ancestry ref"
        result = self._git("merge-base", "--is-ancestor", older, newer, check=False)
        if result.returncode == 0:
            return True, ""
        if result.returncode == 1:
            return False, "not ancestor"
        return False, _decode(result.stderr) or f"ancestry exit {result.returncode}"

    def close_lease(
        self,
        *,
        path: Path,
        issue: int,
        merge_sha: str,
        run_id: str,
        run_head_sha: str,
        adopt_legacy: bool = False,
    ) -> Path:
        path = canonical(path)
        if issue < 1:
            raise CleanupError("lease issue must be positive")
        self._registered_candidate(path)
        lease, errors = self.read_lease(path)
        if adopt_legacy:
            if lease is not None or errors != ["lease missing"]:
                raise CleanupError("legacy adoption requires an absent, not malformed, lease")
            timestamp = self.now()
            lease = {
                "version": 1,
                "issue": issue,
                "path": str(path),
                "state": "active",
                "actor": self.actor,
                "role": "legacy-adoption",
                "created_at": timestamp,
                "updated_at": timestamp,
                "adopted_legacy": True,
            }
        elif lease is None:
            raise CleanupError("active lease is missing or invalid")
        if lease.get("state") != "active" or lease.get("issue") != issue:
            raise CleanupError("only the matching active issue lease can be closed")
        terminal = {
            "merge_sha": merge_sha,
            "run_id": str(run_id),
            "run_head_sha": run_head_sha,
            "result": "success",
            # This explicit orchestrator assertion is required because the
            # repository process cannot query Codex's active-agent registry.
            "roles_ended": True,
            "closed_by": self.actor,
            "closed_at": self.now(),
        }
        prospective = {**lease, "state": "terminal", "updated_at": self.now(), "terminal": terminal}
        valid, reason, _ = self._validate_run(prospective)
        if not valid:
            raise CleanupError(reason)
        origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
        merge_on_main, reason = self._is_ancestor(merge_sha, origin_main)
        if not merge_on_main:
            raise CleanupError(f"merge SHA is not on origin/main: {reason}")
        merge_on_run, reason = self._is_ancestor(merge_sha, run_head_sha)
        if not merge_on_run:
            raise CleanupError(f"merge SHA is not on successful run head: {reason}")
        return self._write_lease(path, prospective)

    def _status_dirty(self, path: Path) -> tuple[bool | None, str]:
        result = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=path, check=False)
        if result.returncode:
            return None, _decode(result.stderr) or f"status exit {result.returncode}"
        return bool(result.stdout), ""

    def _candidate_plan(self, wt: Worktree, *, main: Worktree, boundary: Path, origin_main: str) -> Plan:
        path = wt.path
        reasons: list[str] = []
        errors: list[str] = []
        lease, lease_errors = self.read_lease(path)
        lease_state = lease.get("state", "missing-or-invalid") if lease else "missing-or-invalid"
        issue = lease.get("issue") if lease else None
        process = ProcessScan(True)
        dirty: bool | None = None
        head_on_main = False
        merge_on_main = False
        merge_on_run = False
        evidence_valid = False
        terminal: dict[str, Any] = lease.get("terminal", {}) if lease else {}

        if path == main.path:
            reasons.append(PROTECTED_SHARED_MAIN)
        elif not is_below(path, boundary):
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.append("path outside agent worktree boundary")
        elif wt.prunable or not path.exists():
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.append("registered path is absent; use metadata-prune classification")
        else:
            if lease_errors:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.extend(lease_errors)
            elif lease_state == "active":
                reasons.append(RETAIN_ACTIVE_LIFECYCLE)

            process = self.process_scanner.scan(path)
            if process.uses:
                reasons.append(RETAIN_ACTIVE_PROCESS)
            if not process.complete:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.extend(process.errors)

            dirty, status_error = self._status_dirty(path)
            if dirty is True:
                reasons.append(RETAIN_DIRTY)
            elif dirty is None:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append(status_error)

            head_on_main, ancestry_error = self._is_ancestor(wt.head, origin_main)
            if not head_on_main:
                reasons.append(RETAIN_UNMERGED_HEAD)
                if ancestry_error != "not ancestor":
                    errors.append(ancestry_error)

            if lease_state == "terminal":
                evidence_valid, evidence_error, _ = self._validate_run(lease)
                merge_sha = str(terminal.get("merge_sha", ""))
                run_head = str(terminal.get("run_head_sha", ""))
                merge_on_main, merge_main_error = self._is_ancestor(merge_sha, origin_main)
                merge_on_run, merge_run_error = self._is_ancestor(merge_sha, run_head)
                if not evidence_valid or not merge_on_main or not merge_on_run:
                    reasons.append(RETAIN_TERMINAL_EVIDENCE_MISSING)
                    errors.extend(
                        item
                        for item in (evidence_error, merge_main_error, merge_run_error)
                        if item and item != "not ancestor"
                    )

        if not reasons:
            reasons.append(ELIGIBLE_REMOVE)
        reasons = list(dict.fromkeys(reasons))
        errors = list(dict.fromkeys(errors))
        classification = reasons[0]
        process_reasons = [f"pid={use.pid}:{','.join(use.reasons)}" for use in process.uses]
        facts = {
            "mode": "remove",
            "repository": str(main.path),
            "common_dir": str(self.common_dir),
            "path": str(path),
            "issue": issue,
            "branch": wt.branch,
            "detached": wt.detached,
            "head": wt.head,
            "origin_main": origin_main,
            "lease": lease,
            "lease_errors": lease_errors,
            "process_complete": process.complete,
            "process_uses": [asdict(use) for use in process.uses],
            "process_errors": list(process.errors),
            "dirty": dirty,
            "head_on_main": head_on_main,
            "evidence_valid": evidence_valid,
            "merge_on_main": merge_on_main,
            "merge_on_run": merge_on_run,
            "classification": classification,
            "reasons": reasons,
            "errors": errors,
        }
        return Plan(
            timestamp=self.now(),
            actor=self.actor,
            mode="remove",
            repository=str(main.path),
            common_dir=str(self.common_dir),
            path=str(path),
            issue=issue,
            branch=wt.branch,
            detached=wt.detached,
            head=wt.head,
            origin_main=origin_main,
            lease_state=lease_state,
            terminal_run_id=str(terminal.get("run_id")) if terminal.get("run_id") is not None else None,
            terminal_run_head=terminal.get("run_head_sha"),
            terminal_result=terminal.get("result"),
            process_ids=[use.pid for use in process.uses],
            process_reasons=process_reasons,
            classification=classification,
            reasons=reasons,
            errors=errors,
            facts=facts,
        ).seal()

    def classify(self) -> list[Plan]:
        worktrees = self.worktrees()
        main = self.shared_main(worktrees)
        boundary = self.boundary(worktrees)
        origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
        plans = [self._candidate_plan(wt, main=main, boundary=boundary, origin_main=origin_main) for wt in worktrees]
        registered = {wt.path for wt in worktrees}
        if boundary.is_dir():
            for entry in sorted(boundary.iterdir(), key=lambda path: path.name):
                path = canonical(entry)
                if path in registered:
                    continue
                facts = {
                    "mode": "remove",
                    "path": str(path),
                    "classification": RETAIN_MISSING_OR_UNCLASSIFIED,
                    "reason": "existing directory is not registered",
                }
                plans.append(
                    Plan(
                        timestamp=self.now(),
                        actor=self.actor,
                        mode="remove",
                        repository=str(main.path),
                        common_dir=str(self.common_dir),
                        path=str(path),
                        issue=None,
                        branch=None,
                        detached=False,
                        head="",
                        origin_main=origin_main,
                        lease_state="missing-or-invalid",
                        terminal_run_id=None,
                        terminal_run_head=None,
                        terminal_result=None,
                        process_ids=[],
                        process_reasons=[],
                        classification=RETAIN_MISSING_OR_UNCLASSIFIED,
                        reasons=[RETAIN_MISSING_OR_UNCLASSIFIED],
                        errors=["existing directory is not registered"],
                        facts=facts,
                    ).seal()
                )
        return sorted(plans, key=lambda plan: plan.path)

    def classify_path(self, path: Path) -> Plan:
        path = canonical(path)
        matches = [plan for plan in self.classify() if canonical(plan.path) == path]
        if len(matches) != 1:
            main = self.shared_main()
            origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
            facts = {"mode": "remove", "path": str(path), "classification": RETAIN_MISSING_OR_UNCLASSIFIED}
            return Plan(
                timestamp=self.now(),
                actor=self.actor,
                mode="remove",
                repository=str(main.path),
                common_dir=str(self.common_dir),
                path=str(path),
                issue=None,
                branch=None,
                detached=False,
                head="",
                origin_main=origin_main,
                lease_state="missing-or-invalid",
                terminal_run_id=None,
                terminal_run_head=None,
                terminal_result=None,
                process_ids=[],
                process_reasons=[],
                classification=RETAIN_MISSING_OR_UNCLASSIFIED,
                reasons=[RETAIN_MISSING_OR_UNCLASSIFIED],
                errors=["candidate is absent, outside the boundary, or ambiguously registered"],
                facts=facts,
            ).seal()
        return matches[0]

    def remove(self, *, path: Path, issue: int, plan_digest: str) -> Plan:
        path = canonical(path)
        plan = self.classify_path(path)
        remove_action = f"git worktree remove {path}"
        plan.requested_actions = [remove_action]
        if plan.branch:
            plan.requested_actions.append(f"git branch -d {plan.branch}")
        if plan.plan_digest != plan_digest:
            plan.exit_status = 2
            plan.errors.append("reviewed plan digest does not match recomputed facts")
            return plan
        if plan.classification != ELIGIBLE_REMOVE or plan.issue != issue:
            plan.exit_status = 2
            plan.errors.append("candidate is not eligible for the matching issue")
            return plan
        result = self._git("worktree", "remove", str(path), check=False)
        if result.returncode:
            plan.exit_status = 1
            plan.errors.append(_decode(result.stderr) or "git worktree remove refused")
            return plan
        plan.completed_actions.append(remove_action)
        if plan.branch:
            branch_tip = self._git("rev-parse", f"refs/heads/{plan.branch}", check=False)
            if branch_tip.returncode:
                plan.exit_status = 1
                plan.errors.append("attached branch disappeared before merged-safe deletion")
                return plan
            tip = _decode(branch_tip.stdout)
            merged, reason = self._is_ancestor(tip, plan.origin_main)
            if not merged:
                plan.exit_status = 1
                plan.errors.append(f"branch is no longer proven merged: {reason}")
                return plan
            deleted = self._git("branch", "-d", plan.branch, check=False)
            if deleted.returncode:
                plan.exit_status = 1
                plan.errors.append(_decode(deleted.stderr) or "git branch -d refused")
                return plan
            plan.completed_actions.append(f"git branch -d {plan.branch}")
        return plan

    def classify_stale(self, *, delete_merged_branches: bool = False) -> list[Plan]:
        worktrees = self.worktrees()
        main = self.shared_main(worktrees)
        boundary = self.boundary(worktrees)
        origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
        plans: list[Plan] = []
        for wt in worktrees:
            if not wt.prunable and wt.path.exists():
                continue
            reasons: list[str] = []
            errors: list[str] = []
            lease, lease_errors = self.read_lease(wt.path)
            terminal = lease.get("terminal", {}) if lease else {}
            if wt.path == main.path or not is_below(wt.path, boundary):
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            if wt.path.exists():
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("stale metadata mode never handles an existing path")
            if wt.locked:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("registration is locked")
            if lease_errors or not lease or lease.get("state") != "terminal":
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.extend(lease_errors or ["terminal lease missing"])
            valid = False
            head_on_main = False
            merge_main = False
            merge_run = False
            if lease and lease.get("state") == "terminal":
                valid, evidence_error, _ = self._validate_run(lease)
                merge_main, main_error = self._is_ancestor(str(terminal.get("merge_sha", "")), origin_main)
                merge_run, run_error = self._is_ancestor(
                    str(terminal.get("merge_sha", "")), str(terminal.get("run_head_sha", ""))
                )
                if not valid or not merge_main or not merge_run:
                    reasons.append(RETAIN_TERMINAL_EVIDENCE_MISSING)
                    errors.extend(item for item in (evidence_error, main_error, run_error) if item and item != "not ancestor")
            head_on_main, head_error = self._is_ancestor(wt.head, origin_main)
            if not head_on_main:
                reasons.append(RETAIN_UNMERGED_HEAD)
                if head_error != "not ancestor":
                    errors.append(head_error)
            process = self.process_scanner.scan(wt.path)
            if process.uses:
                reasons.append(RETAIN_ACTIVE_PROCESS)
            if not process.complete:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.extend(process.errors)
            if not reasons:
                reasons.append(STALE_REGISTRATION_ELIGIBLE_PRUNE)
            reasons = list(dict.fromkeys(reasons))
            errors = list(dict.fromkeys(errors))
            facts = {
                "mode": "prune-metadata",
                "path": str(wt.path),
                "head": wt.head,
                "branch": wt.branch,
                "locked": wt.locked,
                "prunable": wt.prunable,
                "origin_main": origin_main,
                "lease": lease,
                "lease_errors": lease_errors,
                "evidence_valid": valid,
                "head_on_main": head_on_main,
                "merge_on_main": merge_main,
                "merge_on_run": merge_run,
                "process_complete": process.complete,
                "process_uses": [asdict(use) for use in process.uses],
                "classification": reasons[0],
                "reasons": reasons,
                "errors": errors,
                "delete_merged_branches": delete_merged_branches,
            }
            plans.append(
                Plan(
                    timestamp=self.now(),
                    actor=self.actor,
                    mode="prune-metadata",
                    repository=str(main.path),
                    common_dir=str(self.common_dir),
                    path=str(wt.path),
                    issue=lease.get("issue") if lease else None,
                    branch=wt.branch,
                    detached=wt.detached,
                    head=wt.head,
                    origin_main=origin_main,
                    lease_state=lease.get("state", "missing-or-invalid") if lease else "missing-or-invalid",
                    terminal_run_id=str(terminal.get("run_id")) if terminal.get("run_id") is not None else None,
                    terminal_run_head=terminal.get("run_head_sha"),
                    terminal_result=terminal.get("result"),
                    process_ids=[use.pid for use in process.uses],
                    process_reasons=[f"pid={use.pid}:{','.join(use.reasons)}" for use in process.uses],
                    classification=reasons[0],
                    reasons=reasons,
                    requested_actions=(
                        ["git worktree prune --expire now", f"git branch -d {wt.branch}"]
                        if delete_merged_branches and wt.branch
                        else ["git worktree prune --expire now"]
                    ),
                    errors=errors,
                    facts=facts,
                ).seal()
            )
        return sorted(plans, key=lambda plan: plan.path)

    @staticmethod
    def stale_digest(plans: Sequence[Plan]) -> str:
        payload = [(plan.path, plan.plan_digest) for plan in plans]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    def prune_stale(self, *, plan_digest: str, delete_merged_branches: bool = False) -> dict[str, Any]:
        plans = self.classify_stale(delete_merged_branches=delete_merged_branches)
        digest = self.stale_digest(plans)
        result = {
            "timestamp": self.now(),
            "actor": self.actor,
            "mode": "prune-metadata",
            "repository": plans[0].repository if plans else str(self.shared_main().path),
            "common_dir": str(self.common_dir),
            "plan_digest": digest,
            "requested_actions": ["git worktree prune --expire now"]
            + (
                [f"git branch -d {plan.branch}" for plan in plans if plan.branch]
                if delete_merged_branches
                else []
            ),
            "completed_actions": [],
            "records": [plan.public_dict() for plan in plans],
            "errors": [],
            "exit_status": 0,
        }
        if digest != plan_digest:
            result["errors"].append("reviewed stale-metadata plan digest does not match recomputed facts")
            result["exit_status"] = 2
            return result
        if not plans or any(plan.classification != STALE_REGISTRATION_ELIGIBLE_PRUNE for plan in plans):
            result["errors"].append("every stale registration must be eligible before broad metadata pruning")
            result["exit_status"] = 2
            return result
        command = self._git("worktree", "prune", "--expire", "now", check=False)
        if command.returncode:
            result["errors"].append(_decode(command.stderr) or "git worktree prune refused")
            result["exit_status"] = 1
            return result
        result["completed_actions"].append("git worktree prune --expire now")
        if delete_merged_branches:
            for plan in plans:
                if not plan.branch:
                    continue
                branch_tip = self._git("rev-parse", f"refs/heads/{plan.branch}", check=False)
                if branch_tip.returncode:
                    result["errors"].append(f"{plan.branch}: branch disappeared before merged-safe deletion")
                    result["exit_status"] = 1
                    continue
                merged, reason = self._is_ancestor(_decode(branch_tip.stdout), plan.origin_main)
                if not merged:
                    result["errors"].append(f"{plan.branch}: branch is not proven merged: {reason}")
                    result["exit_status"] = 1
                    continue
                deleted = self._git("branch", "-d", plan.branch, check=False)
                if deleted.returncode:
                    result["errors"].append(
                        f"{plan.branch}: {_decode(deleted.stderr) or 'git branch -d refused'}"
                    )
                    result["exit_status"] = 1
                    continue
                result["completed_actions"].append(f"git branch -d {plan.branch}")
        return result


HUMAN_PLAN_FIELDS: tuple[str, ...] = (
    "timestamp",
    "actor",
    "mode",
    "repository",
    "common_dir",
    "path",
    "issue",
    "branch",
    "detached",
    "head",
    "origin_main",
    "lease_state",
    "terminal_run_id",
    "terminal_run_head",
    "terminal_result",
    "process_ids",
    "process_reasons",
    "classification",
    "reasons",
    "plan_digest",
    "requested_actions",
    "completed_actions",
    "exit_status",
    "errors",
)


def _human_value(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _action_succeeded(requested: str, completed: Sequence[str]) -> bool:
    return any(action == requested or action.startswith(f"{requested} ") for action in completed)


def _render_action_statuses(record: dict[str, Any]) -> list[str]:
    requested = list(record.get("requested_actions") or [])
    completed = list(record.get("completed_actions") or [])
    lines = []
    for action in requested:
        if _action_succeeded(action, completed):
            status = "success"
        elif record.get("exit_status"):
            status = "failure"
        else:
            status = "pending"
        lines.append(f"  action={_human_value(action)} status={status}")
    for action in completed:
        if not any(_action_succeeded(requested_action, [action]) for requested_action in requested):
            lines.append(f"  action={_human_value(action)} status=success")
    return lines


def _render_human_record(record: dict[str, Any], *, label: str) -> list[str]:
    lines = [label]
    for field_name in HUMAN_PLAN_FIELDS:
        lines.append(f"  {field_name}={_human_value(record.get(field_name))}")
    lines.extend(_render_action_statuses(record))
    return lines


def render_human(records: Sequence[Plan]) -> str:
    lines: list[str] = []
    for index, plan in enumerate(records, start=1):
        lines.extend(_render_human_record(plan.public_dict(), label=f"record={index}"))
    return "\n".join(lines)


def render_stale_human(result: dict[str, Any]) -> str:
    summary = {
        "timestamp": result.get("timestamp"),
        "actor": result.get("actor"),
        "mode": result.get("mode"),
        "repository": result.get("repository"),
        "common_dir": result.get("common_dir"),
        "plan_digest": result.get("plan_digest"),
        "requested_actions": result.get("requested_actions", []),
        "completed_actions": result.get("completed_actions", []),
        "exit_status": result.get("exit_status"),
        "errors": result.get("errors", []),
    }
    lines = _render_human_record(summary, label="apply-summary")
    for index, record in enumerate(result.get("records", []), start=1):
        lines.extend(_render_human_record(record, label=f"candidate={index}"))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--actor", required=True, help="Attributable orchestrator/operator identity")
    parser.add_argument("--json", action="store_true", help="Emit stable JSON records")
    subparsers = parser.add_subparsers(dest="command")

    classify_parser = subparsers.add_parser("classify", help="Read-only classification (default)")
    classify_parser.add_argument("--path", type=Path)

    create = subparsers.add_parser("lease-create", help="Create active lifecycle lease before role dispatch")
    create.add_argument("--path", type=Path, required=True)
    create.add_argument("--issue", type=int, required=True)
    create.add_argument("--role", required=True)

    for name in ("lease-close", "lease-adopt"):
        close = subparsers.add_parser(name, help="Record terminal on-call evidence after every role ends")
        close.add_argument("--path", type=Path, required=True)
        close.add_argument("--issue", type=int, required=True)
        close.add_argument("--merge-sha", required=True)
        close.add_argument("--run-id", required=True)
        close.add_argument("--run-head-sha", required=True)
        close.add_argument(
            "--roles-ended",
            action="store_true",
            required=True,
            help="Assert the orchestrator checked its active-agent registry",
        )

    remove = subparsers.add_parser("remove", help="Remove one reviewed eligible worktree")
    remove.add_argument("--apply", action="store_true", required=True)
    remove.add_argument("--path", type=Path, required=True)
    remove.add_argument("--issue", type=int, required=True)
    remove.add_argument("--plan-digest", required=True)

    prune = subparsers.add_parser("prune-metadata", help="Classify or prune already-absent registrations")
    prune.add_argument("--apply", action="store_true")
    prune.add_argument("--plan-digest")
    prune.add_argument(
        "--delete-merged-branches",
        action="store_true",
        help="Explicitly delete attached branches with git branch -d after metadata prune",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = CleanupService(args.repo, actor=args.actor)
    command = args.command or "classify"
    try:
        if command == "classify":
            records = [service.classify_path(args.path)] if getattr(args, "path", None) else service.classify()
            print(json.dumps([record.public_dict() for record in records], sort_keys=True) if args.json else render_human(records))
            return 0
        if command == "lease-create":
            target = service.create_lease(path=args.path, issue=args.issue, role=args.role)
            print(json.dumps({"action": "lease-created", "path": str(target), "actor": args.actor}, sort_keys=True))
            return 0
        if command in {"lease-close", "lease-adopt"}:
            target = service.close_lease(
                path=args.path,
                issue=args.issue,
                merge_sha=args.merge_sha,
                run_id=args.run_id,
                run_head_sha=args.run_head_sha,
                adopt_legacy=command == "lease-adopt",
            )
            print(json.dumps({"action": command, "path": str(target), "actor": args.actor}, sort_keys=True))
            return 0
        if command == "remove":
            result = service.remove(path=args.path, issue=args.issue, plan_digest=args.plan_digest)
            print(json.dumps(result.public_dict(), sort_keys=True) if args.json else render_human([result]))
            return result.exit_status
        if command == "prune-metadata":
            plans = service.classify_stale(delete_merged_branches=args.delete_merged_branches)
            if not args.apply:
                payload = {
                    "mode": "prune-metadata",
                    "plan_digest": service.stale_digest(plans),
                    "records": [plan.public_dict() for plan in plans],
                }
                print(json.dumps(payload, sort_keys=True) if args.json else render_human(plans))
                return 0
            if not args.plan_digest:
                raise CleanupError("--plan-digest is required with --apply")
            result = service.prune_stale(
                plan_digest=args.plan_digest,
                delete_merged_branches=args.delete_merged_branches,
            )
            print(json.dumps(result, sort_keys=True) if args.json else render_stale_human(result))
            return int(result["exit_status"])
        raise CleanupError(f"unknown command: {command}")
    except CleanupError as exc:
        print(json.dumps({"actor": args.actor, "error": str(exc), "exit_status": 2}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
