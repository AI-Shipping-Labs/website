#!/usr/bin/env python3
"""Fail-closed, recoverable retirement of one unattached agent branch.

Classification and plan commands are read-only.  Archive-retire and restore
apply require an exact reviewed digest and use Git compare-and-update
transactions.  This helper is deliberately separate from worktree cleanup.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_SLUG = "AI-Shipping-Labs/website"
DEPLOY_WORKFLOW = "Deploy Dev"
LEASE_DIRNAME = "agent-worktree-leases"
RECORD_DIRNAME = "retired-agent-branches"
ARCHIVE_NAMESPACE = "refs/tags/retired-agent-branches"
BACKUP_NAMESPACE = "refs/retired-agent-backups"
ALLOWED_AUTHORS = frozenset({"alexeygrigorev", "kavaivaleri"})
ZERO_OID = "0" * 40

PROTECTED_ATTACHED_WORKTREE = "PROTECTED_ATTACHED_WORKTREE"
PROTECTED_OPEN_OR_HUMAN_ISSUE = "PROTECTED_OPEN_OR_HUMAN_ISSUE"
PROTECTED_ACTIVE_ROLE_OR_LEASE = "PROTECTED_ACTIVE_ROLE_OR_LEASE"
PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE = "PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE"
PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION = "PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION"
PROTECTED_ALREADY_MERGED = "PROTECTED_ALREADY_MERGED"
RETAIN_UNMERGED_UNARCHIVED = "RETAIN_UNMERGED_UNARCHIVED"
ELIGIBLE_ARCHIVE_RETIRE = "ELIGIBLE_ARCHIVE_RETIRE"
ARCHIVED_RETIRED = "ARCHIVED_RETIRED"
ELIGIBLE_RESTORE = "ELIGIBLE_RESTORE"

BRANCH_RE = re.compile(r"^worktree-(?:agent|tester)-(?P<issue>[1-9][0-9]*)(?:-[a-z0-9][a-z0-9-]*)?$")
FULL_SHA_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{40})(?![0-9a-f])", re.IGNORECASE)
TERMINAL_EVIDENCE_MARKER = "agent_branch_terminal_v1"
SUPERSESSION_EVIDENCE_MARKER = "agent_branch_supersession_v1"
TERMINAL_NEGATION_RE = re.compile(
    r"\b(?:did\s+not|do\s+not|don't|not)\s+accept(?:ed|ance)?\b|"
    r"\breject(?:ed|ion)?\b|\b(?:do\s+not|don't)\s+retire\b|\bretain(?:ed|ing)?\b|"
    r"\bwithdraw(?:n|ing)?\s+acceptance\b|\brevok(?:e|ed|ing)\s+acceptance\b|"
    r"\bacceptance\b.{0,20}\b(?:withdrawn|revoked)\b|"
    r"\bnever\b.{0,40}\baccept(?:ed|ance)?\b",
    re.IGNORECASE,
)
SUPERSESSION_NEGATION_RE = re.compile(
    r"\b(?:is\s+not|was\s+not|not|never)\s+supersed(?:e|ed)\b|"
    r"\b(?:do\s+not|don't|never)\s+retire\b|\bretain(?:ed|ing)?\b|"
    r"\bwithdraw(?:n|ing)?\s+supersession\b|\brevoke(?:d|ing)?\s+supersession\b|"
    r"\bsupersession\b.{0,20}\b(?:withdrawn|revoked)\b",
    re.IGNORECASE,
)


class RetirementError(RuntimeError):
    """A safety precondition could not be proved."""


def _strict_json_object(raw: str | bytes) -> dict[str, Any]:
    """Decode one JSON object while refusing duplicate keys at every level."""

    def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"duplicate JSON key:{key}")
            payload[key] = value
        return payload

    payload = json.loads(raw, object_pairs_hook=no_duplicate_keys)
    if not isinstance(payload, dict):
        raise ValueError("JSON payload is not an object")
    return payload


def _owner_comment_is_contradictory(body: str) -> bool:
    """Conservatively recognize ASCII owner denial without authorizing prose."""
    words = re.findall(r"[a-z]+", body.lower())
    denial_roots = (
        "revok",
        "withdraw",
        "retain",
        "reject",
        "deny",
        "deni",
        "rescind",
        "prohibit",
    )
    if any(word.startswith(denial_roots) for word in words):
        return True
    negative = any(word in {"no", "not", "never"} for word in words) or bool(re.search(r"\b[a-z]+n't\b", body.lower()))
    decision_target = any(word.startswith(("accept", "retir", "supersed")) for word in words)
    return negative and decision_target


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


class CommandRunner:
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
    ) -> CommandResult:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            input=input_bytes,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return CommandResult(completed.stdout, completed.stderr, completed.returncode)


@dataclass(frozen=True)
class ProcessEvidence:
    complete: bool
    process_ids: tuple[int, ...] = ()
    reasons: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ProcessScanner:
    """Scan Linux procfs for the exact branch name or associated directories."""

    def __init__(self, proc_root: Path = Path("/proc"), *, self_pid: int | None = None):
        self.proc_root = proc_root
        self.self_pid = os.getpid() if self_pid is None else self_pid

    def scan(self, branch: str, paths: Sequence[Path]) -> ProcessEvidence:
        try:
            entries = list(self.proc_root.iterdir())
        except OSError as exc:
            return ProcessEvidence(False, errors=(f"proc-list:{type(exc).__name__}",))
        canonical_paths = tuple(path.resolve(strict=False) for path in paths)
        found: dict[int, set[str]] = {}
        errors: set[str] = set()
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) == self.self_pid:
                continue
            pid = int(entry.name)
            reasons: set[str] = set()
            try:
                tokens = (entry / "cmdline").read_bytes().split(b"\0")
                text_tokens = [token.decode(errors="replace") for token in tokens if token]
                if any(branch == token or branch in token.split("/") for token in text_tokens):
                    reasons.add("cmdline-branch")
                for token in text_tokens:
                    for path in canonical_paths:
                        if token.startswith("/") and _at_or_below(Path(token).resolve(strict=False), path):
                            reasons.add("cmdline-path")
            except FileNotFoundError:
                continue
            except (PermissionError, OSError) as exc:
                errors.add(f"cmdline:{type(exc).__name__}")
                continue
            for link_name in ("cwd",):
                try:
                    target = Path(os.readlink(entry / link_name)).resolve(strict=False)
                    if any(_at_or_below(target, path) for path in canonical_paths):
                        reasons.add(link_name)
                except FileNotFoundError:
                    continue
                except (PermissionError, OSError) as exc:
                    errors.add(f"{link_name}:{type(exc).__name__}")
            try:
                for fd in (entry / "fd").iterdir():
                    try:
                        target = Path(os.readlink(fd)).resolve(strict=False)
                    except FileNotFoundError:
                        continue
                    if any(_at_or_below(target, path) for path in canonical_paths):
                        reasons.add("fd")
            except FileNotFoundError:
                continue
            except (PermissionError, OSError) as exc:
                errors.add(f"fd:{type(exc).__name__}")
            if reasons:
                found[pid] = reasons
        return ProcessEvidence(
            complete=not errors,
            process_ids=tuple(sorted(found)),
            reasons=tuple(f"pid={pid}:{','.join(sorted(found[pid]))}" for pid in sorted(found)),
            errors=tuple(sorted(errors)),
        )


@dataclass(frozen=True)
class IssueEvidence:
    number: int
    state: str
    labels: tuple[str, ...]
    author: str
    url: str
    comments: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class RunEvidence:
    run_id: str
    head_sha: str
    status: str
    conclusion: str
    workflow: str
    url: str


@dataclass(frozen=True)
class RecordSnapshot:
    payload: dict[str, Any]
    content_sha256: str
    device: int
    inode: int
    size: int


@dataclass
class Plan:
    timestamp: str
    actor: str
    mode: str
    repository: str
    common_dir: str
    branch: str
    branch_ref: str
    tip: str = ""
    parent: str = ""
    merge_base: str = ""
    main_sha: str = ""
    origin_main_sha: str = ""
    ahead: int | None = None
    behind: int | None = None
    attached_worktrees: list[str] = field(default_factory=list)
    patch_id: str = ""
    cherry: str = ""
    matching_main_commit: str = ""
    replacement_commit: str = ""
    changed_paths: list[str] = field(default_factory=list)
    subject: str = ""
    issue: int | None = None
    issue_state: str = ""
    issue_labels: list[str] = field(default_factory=list)
    issue_author: str = ""
    issue_url: str = ""
    accepted_author_input: list[str] = field(default_factory=list)
    terminal_run_id: str = ""
    terminal_run_head: str = ""
    terminal_run_result: str = ""
    terminal_run_url: str = ""
    roles_ended_asserted: bool = False
    lease_evidence: list[dict[str, Any]] = field(default_factory=list)
    lease_boundary: dict[str, Any] = field(default_factory=dict)
    process_ids: list[int] = field(default_factory=list)
    process_reasons: list[str] = field(default_factory=list)
    process_scan_complete: bool = False
    recovery_paths: list[str] = field(default_factory=list)
    recovery_boundary: dict[str, Any] = field(default_factory=dict)
    authoritative_restore: dict[str, Any] = field(default_factory=dict)
    archive_ref: str = ""
    backup_ref: str = ""
    archive_record_path: str = ""
    restore_command: str = ""
    classification: str = PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE
    reasons: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    requested_actions: list[str] = field(default_factory=list)
    completed_actions: list[str] = field(default_factory=list)
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


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").strip()


def _at_or_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _encoded_branch(branch: str) -> str:
    return base64.urlsafe_b64encode(branch.encode()).decode().rstrip("=")


def _record_key(branch_ref: str) -> str:
    return hashlib.sha256(branch_ref.encode()).hexdigest()


def _archive_ref(branch: str, tip: str) -> str:
    return f"{ARCHIVE_NAMESPACE}/{_encoded_branch(branch)}/{tip}"


def _backup_ref(branch: str, tip: str) -> str:
    return f"{BACKUP_NAMESPACE}/{_encoded_branch(branch)}/{tip}"


def _issue_from_branch(branch: str) -> int | None:
    match = BRANCH_RE.fullmatch(branch)
    return int(match.group("issue")) if match else None


def _stat_identity(metadata: os.stat_result) -> dict[str, int]:
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "mode": metadata.st_mode,
        "size": metadata.st_size,
        "mtime_ns": metadata.st_mtime_ns,
        "ctime_ns": metadata.st_ctime_ns,
    }


class RetirementService:
    def __init__(
        self,
        repo: Path,
        *,
        actor: str,
        runner: Callable[..., CommandResult] | None = None,
        gh_runner: Callable[[Sequence[str]], dict[str, Any]] | None = None,
        process_scanner: ProcessScanner | None = None,
        now: Callable[[], str] = utc_now,
        failure_hook: Callable[[str], None] | None = None,
    ):
        if not actor.strip():
            raise RetirementError("actor identity must not be blank")
        self.repo = repo.resolve(strict=True)
        self.actor = actor
        self.runner = runner or CommandRunner()
        self.gh_runner = gh_runner or self._run_gh
        self.process_scanner = process_scanner or ProcessScanner()
        self.now = now
        self.failure_hook = failure_hook or (lambda _stage: None)
        self.common_dir = self._resolve_common_dir()
        common_fd = self._open_common_fd()
        common_stat = os.fstat(common_fd)
        self.common_identity = (common_stat.st_dev, common_stat.st_ino)
        os.close(common_fd)
        self.record_dir = self.common_dir / RECORD_DIRNAME

    def _run(
        self,
        args: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        check: bool = True,
    ) -> CommandResult:
        result = self.runner(args, cwd=self.repo, input_bytes=input_bytes)
        if check and result.returncode:
            detail = _decode(result.stderr) or _decode(result.stdout) or f"exit {result.returncode}"
            raise RetirementError(f"{' '.join(shlex.quote(arg) for arg in args)}: {detail}")
        return result

    def _git(self, *args: str, input_bytes: bytes | None = None, check: bool = True) -> CommandResult:
        return self._run(("git", *args), input_bytes=input_bytes, check=check)

    def _resolve_common_dir(self) -> Path:
        result = self._run(("git", "rev-parse", "--git-common-dir"))
        raw = Path(_decode(result.stdout))
        return (raw if raw.is_absolute() else self.repo / raw).resolve(strict=True)

    def _run_gh(self, args: Sequence[str]) -> dict[str, Any]:
        result = self._run(("gh", *args))
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RetirementError("GitHub CLI returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RetirementError("GitHub CLI returned a non-object payload")
        return payload

    def _ref_oid(self, ref: str) -> str:
        result = self._git("show-ref", "--verify", "--hash", ref, check=False)
        return _decode(result.stdout) if result.returncode == 0 else ""

    def _commit_oid(self, expression: str) -> str:
        result = self._git("rev-parse", "--verify", f"{expression}^{{commit}}", check=False)
        return _decode(result.stdout) if result.returncode == 0 else ""

    def _is_ancestor(self, older: str, newer: str) -> bool:
        return self._git("merge-base", "--is-ancestor", older, newer, check=False).returncode == 0

    def _worktrees(self) -> list[dict[str, str]]:
        raw = self._git("worktree", "list", "--porcelain", "-z").stdout
        records: list[dict[str, str]] = []
        fields: dict[str, str] = {}
        for token in raw.split(b"\0"):
            if not token:
                if fields:
                    records.append(fields)
                    fields = {}
                continue
            text = token.decode(errors="strict")
            key, _, value = text.partition(" ")
            fields[key] = value
        if fields:
            raise RetirementError("unterminated worktree record")
        return records

    def _issue(self, number: int) -> IssueEvidence:
        payload = self.gh_runner(
            (
                "issue",
                "view",
                str(number),
                "--repo",
                REPO_SLUG,
                "--json",
                "number,state,labels,author,url,comments",
            )
        )
        if payload.get("number") != number:
            raise RetirementError("issue lookup returned the wrong issue")
        labels = tuple(sorted(str(item.get("name", "")) for item in payload.get("labels", [])))
        author = str((payload.get("author") or {}).get("login", ""))
        comments = tuple(
            {
                "author": str((item.get("author") or {}).get("login", "")),
                "body": str(item.get("body", "")),
                "url": str(item.get("url", "")),
            }
            for item in payload.get("comments", [])
        )
        return IssueEvidence(
            number=number,
            state=str(payload.get("state", "")),
            labels=labels,
            author=author,
            url=str(payload.get("url", "")),
            comments=comments,
        )

    def _run_evidence(self, issue: IssueEvidence, commit: str) -> tuple[RunEvidence | None, list[str]]:
        candidates: list[tuple[str, str]] = []
        accepted: list[str] = []
        ambiguous = False
        for comment in issue.comments:
            if comment["author"] not in ALLOWED_AUTHORS:
                continue
            body = comment["body"]
            if not body.isascii():
                ambiguous = True
                continue
            if TERMINAL_NEGATION_RE.search(body) or _owner_comment_is_contradictory(body):
                ambiguous = True
            payload = self._structured_comment(
                body,
                marker=TERMINAL_EVIDENCE_MARKER,
                fields={"decision", "run_id"},
            )
            if payload is None:
                if TERMINAL_EVIDENCE_MARKER in body:
                    ambiguous = True
                continue
            if payload.get("decision") != "accepted-terminal-green":
                ambiguous = True
                continue
            run_id = payload.get("run_id")
            if not isinstance(run_id, str) or not run_id.isdigit() or int(run_id) < 1:
                ambiguous = True
                continue
            candidates.append((run_id, comment["url"]))
            accepted.append(comment["url"])
        if ambiguous or len(candidates) != 1:
            return None, accepted
        run_id, _comment_url = candidates[0]
        payload = self.gh_runner(
            (
                "run",
                "view",
                run_id,
                "--repo",
                REPO_SLUG,
                "--json",
                "databaseId,status,conclusion,workflowName,headSha,url",
            )
        )
        run = RunEvidence(
            run_id=str(payload.get("databaseId", "")),
            head_sha=str(payload.get("headSha", "")),
            status=str(payload.get("status", "")),
            conclusion=str(payload.get("conclusion", "")),
            workflow=str(payload.get("workflowName", "")),
            url=str(payload.get("url", "")),
        )
        valid = (
            run.run_id == run_id
            and run.status == "completed"
            and run.conclusion == "success"
            and run.workflow == DEPLOY_WORKFLOW
            and self._commit_oid(run.head_sha) == run.head_sha
            and self._is_ancestor(commit, run.head_sha)
        )
        return (run if valid else None), accepted

    @staticmethod
    def _structured_comment(
        body: str,
        *,
        marker: str,
        fields: set[str],
    ) -> dict[str, Any] | None:
        """Parse an exact two-line evidence record; surrounding prose is invalid."""
        if not body.isascii() or "\r" in body:
            return None
        lines = body.split("\n")
        if len(lines) != 2 or lines[0] != marker:
            return None
        try:
            payload = _strict_json_object(lines[1])
        except (json.JSONDecodeError, ValueError):
            return None
        if set(payload) != fields:
            return None
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if lines[1] != canonical:
            return None
        return payload

    def _patch_id(self, commit: str) -> str:
        diff = self._git("show", "--pretty=format:", "--binary", commit).stdout
        result = self._git("patch-id", "--stable", input_bytes=diff, check=False)
        if result.returncode or not result.stdout:
            return ""
        return _decode(result.stdout).split()[0]

    def _matching_main_commits(self, patch_id: str, main_sha: str, paths: Sequence[str]) -> list[str]:
        if not patch_id:
            return []
        args = ["rev-list", main_sha]
        if paths:
            args.extend(("--", *paths))
        commits = _decode(self._git(*args).stdout).splitlines()
        return [commit for commit in commits if self._patch_id(commit) == patch_id]

    def _lease_evidence(
        self,
        issue: int,
    ) -> tuple[list[dict[str, Any]], bool, list[str], dict[str, Any]]:
        lease_dir = self.common_dir / LEASE_DIRNAME
        evidence: list[dict[str, Any]] = []
        active = False
        errors: list[str] = []
        boundary: dict[str, Any] = {"state": "unreadable", "complete": False}
        common_fd: int | None = None
        lease_fd: int | None = None
        try:
            common_fd = self._open_common_fd()
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            try:
                lease_fd = os.open(LEASE_DIRNAME, flags, dir_fd=common_fd)
            except FileNotFoundError:
                try:
                    os.stat(LEASE_DIRNAME, dir_fd=common_fd, follow_symlinks=False)
                except FileNotFoundError:
                    return [], False, [], {"state": "absent", "complete": True}
                raise RetirementError("lease directory appeared during absence check")
            path_stat = os.stat(LEASE_DIRNAME, dir_fd=common_fd, follow_symlinks=False)
            fd_stat = os.fstat(lease_fd)
            if (
                not stat.S_ISDIR(path_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino)
            ):
                raise RetirementError("lease directory is not a stable real common-Git child")
            names = sorted(os.listdir(lease_fd))
            boundary = {
                "state": "present",
                "complete": False,
                "path": str(lease_dir),
                "path_identity": _stat_identity(path_stat),
                "descriptor_identity": _stat_identity(fd_stat),
                "entry_names": names,
            }
            for name in names:
                entry = lease_dir / name
                if Path(name).name != name or not name.endswith(".json"):
                    errors.append(f"unexpected lease evidence:{entry}")
                    continue
                lease_file_fd: int | None = None
                try:
                    entry_stat = os.stat(name, dir_fd=lease_fd, follow_symlinks=False)
                    if not stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
                        raise RetirementError("lease entry is not a regular no-follow file")
                    lease_file_fd = os.open(
                        name,
                        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=lease_fd,
                    )
                    file_stat = os.fstat(lease_file_fd)
                    if (entry_stat.st_dev, entry_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
                        raise RetirementError("lease entry identity changed while opening")
                    chunks: list[bytes] = []
                    while True:
                        chunk = os.read(lease_file_fd, 65536)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    payload = _strict_json_object(raw)
                    current_stat = os.stat(name, dir_fd=lease_fd, follow_symlinks=False)
                    current_file_stat = os.fstat(lease_file_fd)
                    stable_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
                    if any(
                        getattr(current_stat, field) != getattr(file_stat, field)
                        or getattr(current_file_stat, field) != getattr(file_stat, field)
                        for field in stable_fields
                    ):
                        raise RetirementError("lease entry identity changed while reading")
                    os.lseek(lease_file_fd, 0, os.SEEK_SET)
                    reread_chunks: list[bytes] = []
                    while True:
                        chunk = os.read(lease_file_fd, 65536)
                        if not chunk:
                            break
                        reread_chunks.append(chunk)
                    after_reread_stat = os.fstat(lease_file_fd)
                    if b"".join(reread_chunks) != raw or any(
                        getattr(after_reread_stat, field) != getattr(current_file_stat, field)
                        for field in stable_fields
                    ):
                        raise RetirementError("lease entry content changed while reading")
                except (OSError, RetirementError, json.JSONDecodeError, ValueError) as exc:
                    errors.append(f"malformed lease:{entry}:{type(exc).__name__}:{exc}")
                    continue
                finally:
                    if lease_file_fd is not None:
                        os.close(lease_file_fd)
                required = {"version", "issue", "path", "state", "actor", "created_at", "updated_at"}
                malformed = sorted(required - payload.keys())
                if payload.get("version") != 1:
                    malformed.append("version")
                if payload.get("state") not in {"active", "terminal"}:
                    malformed.append("state")
                if not isinstance(payload.get("issue"), int) or isinstance(payload.get("issue"), bool):
                    malformed.append("issue")
                for key in ("path", "actor", "created_at", "updated_at"):
                    if not isinstance(payload.get(key), str) or not payload.get(key):
                        malformed.append(key)
                if payload.get("state") == "terminal":
                    terminal = payload.get("terminal")
                    terminal_required = {"merge_sha", "run_id", "run_head_sha", "result", "roles_ended"}
                    if not isinstance(terminal, dict) or terminal_required - terminal.keys():
                        malformed.append("terminal")
                    elif terminal.get("result") != "success" or terminal.get("roles_ended") is not True:
                        malformed.append("terminal-state")
                if malformed:
                    errors.append(f"malformed lease:{entry}:{','.join(sorted(set(malformed)))}")
                    continue
                state = payload.get("state")
                matching_issue = payload.get("issue") == issue
                evidence.append(
                    {
                        "path": str(entry),
                        "device": file_stat.st_dev,
                        "inode": file_stat.st_ino,
                        "mode": file_stat.st_mode,
                        "size": file_stat.st_size,
                        "mtime_ns": file_stat.st_mtime_ns,
                        "ctime_ns": file_stat.st_ctime_ns,
                        "content_sha256": hashlib.sha256(raw).hexdigest(),
                        "payload": payload,
                        "matching_issue": matching_issue,
                    }
                )
                if matching_issue and state == "active":
                    active = True
            current_dir = os.stat(LEASE_DIRNAME, dir_fd=common_fd, follow_symlinks=False)
            current_dir_fd = os.fstat(lease_fd)
            directory_fields = ("st_dev", "st_ino", "st_mode", "st_mtime_ns", "st_ctime_ns")
            if any(
                getattr(current_dir, field) != getattr(fd_stat, field)
                or getattr(current_dir_fd, field) != getattr(fd_stat, field)
                for field in directory_fields
            ):
                raise RetirementError("lease directory identity changed while scanning")
            boundary = {
                **boundary,
                "complete": True,
                "final_path_identity": _stat_identity(current_dir),
                "final_descriptor_identity": _stat_identity(current_dir_fd),
            }
        except (OSError, RetirementError) as exc:
            errors.append(f"lease directory unreadable:{type(exc).__name__}:{exc}")
        finally:
            if lease_fd is not None:
                os.close(lease_fd)
            if common_fd is not None:
                os.close(common_fd)
        return evidence, active or bool(errors), errors, boundary

    def _recovery_paths(
        self,
        branch: str,
        issue: int,
        worktrees: Sequence[dict[str, str]],
    ) -> tuple[list[Path], list[str], dict[str, Any]]:
        registered = {Path(item["worktree"]).resolve(strict=False) for item in worktrees}
        main_path = next(
            (
                Path(item["worktree"]).resolve(strict=False)
                for item in worktrees
                if item.get("branch") == "refs/heads/main"
            ),
            self.repo,
        )
        configured = main_path / ".claude" / "worktrees"
        try:
            configured_entry = configured.lstat()
        except FileNotFoundError:
            try:
                configured.lstat()
            except FileNotFoundError:
                return [], [], {"state": "absent", "complete": True, "path": str(configured)}
            return (
                [],
                ["recovery boundary appeared during absence check"],
                {
                    "state": "unreadable",
                    "complete": False,
                    "path": str(configured),
                },
            )
        configured_identity = _stat_identity(configured_entry)
        configured_target = os.readlink(configured) if stat.S_ISLNK(configured_entry.st_mode) else None
        boundary_identity: dict[str, Any] = {
            "state": "present",
            "complete": False,
            "configured_path": str(configured),
            "configured_identity": configured_identity,
            "configured_symlink_target": configured_target,
        }
        try:
            boundary = configured.resolve(strict=True)
            boundary_before = boundary.stat()
            entries = sorted(boundary.iterdir())
        except (OSError, RuntimeError) as exc:
            return [], [f"recovery boundary unreadable:{type(exc).__name__}"], boundary_identity
        if not stat.S_ISDIR(boundary_before.st_mode):
            return [], ["recovery boundary is not a directory"], boundary_identity
        entry_identities: list[dict[str, Any]] = []
        try:
            for entry in entries:
                entry_identities.append(
                    {
                        "name": entry.name,
                        "identity": _stat_identity(entry.lstat()),
                    }
                )
        except OSError as exc:
            return [], [f"recovery boundary entry unreadable:{type(exc).__name__}"], boundary_identity
        boundary_identity.update(
            {
                "resolved_path": str(boundary),
                "resolved_identity": _stat_identity(boundary_before),
                "entries": entry_identities,
            }
        )
        try:
            configured_after = configured.lstat()
            boundary_after = boundary.stat()
        except OSError as exc:
            return [], [f"recovery boundary changed:{type(exc).__name__}"], boundary_identity
        if (configured_entry.st_dev, configured_entry.st_ino) != (configured_after.st_dev, configured_after.st_ino) or (
            boundary_before.st_dev,
            boundary_before.st_ino,
        ) != (boundary_after.st_dev, boundary_after.st_ino):
            return [], ["recovery boundary identity changed while scanning"], boundary_identity
        suffix = branch.removeprefix("worktree-")
        matching: list[Path] = []
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name
            exact_issue = re.search(rf"(?:^|-){issue}(?:-|$)", name) is not None
            if name == suffix or exact_issue:
                resolved = entry.resolve(strict=False)
                if resolved not in registered:
                    matching.append(resolved)
        try:
            final_entry_identities = [
                {"name": entry.name, "identity": _stat_identity(entry.lstat())} for entry in sorted(boundary.iterdir())
            ]
            configured_final = configured.lstat()
            boundary_final = boundary.stat()
        except OSError as exc:
            return [], [f"recovery boundary changed:{type(exc).__name__}"], boundary_identity
        if (
            _stat_identity(configured_final) != configured_identity
            or (os.readlink(configured) if stat.S_ISLNK(configured_final.st_mode) else None) != configured_target
            or _stat_identity(boundary_final) != _stat_identity(boundary_before)
            or final_entry_identities != entry_identities
        ):
            return [], ["recovery boundary identity changed while scanning"], boundary_identity
        return (
            matching,
            [],
            {
                **boundary_identity,
                "complete": True,
                "final_configured_identity": _stat_identity(configured_final),
                "final_resolved_identity": _stat_identity(boundary_final),
                "final_entries": final_entry_identities,
            },
        )

    def _ownership_boundary_snapshot(
        self,
        branch: str,
        issue: int,
        *,
        roles_ended: bool,
    ) -> tuple[dict[str, Any], list[str], list[str]]:
        """Capture every local ownership fact used at a ref-mutation boundary."""
        reasons: list[str] = []
        errors: list[str] = []
        worktrees = self._worktrees()
        branch_ref = f"refs/heads/{branch}"
        attached = sorted(item["worktree"] for item in worktrees if item.get("branch") == branch_ref)
        if attached:
            reasons.append(PROTECTED_ATTACHED_WORKTREE)
        leases, active_lease, lease_errors, lease_boundary = self._lease_evidence(issue)
        errors.extend(lease_errors)
        if active_lease or not roles_ended:
            reasons.append(PROTECTED_ACTIVE_ROLE_OR_LEASE)
        if lease_errors:
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        recovery, recovery_errors, recovery_boundary = self._recovery_paths(branch, issue, worktrees)
        errors.extend(recovery_errors)
        if recovery or recovery_errors:
            reasons.append(PROTECTED_ACTIVE_ROLE_OR_LEASE)
        if recovery_errors:
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        process = self.process_scanner.scan(branch, recovery)
        errors.extend(process.errors)
        if process.process_ids or not process.complete:
            reasons.append(PROTECTED_ACTIVE_ROLE_OR_LEASE)
        snapshot = {
            "attached_worktrees": attached,
            "lease_boundary": lease_boundary,
            "lease_evidence": leases,
            "process": asdict(process),
            "recovery_boundary": recovery_boundary,
            "recovery_paths": [str(path) for path in recovery],
        }
        return snapshot, list(dict.fromkeys(reasons)), list(dict.fromkeys(errors))

    def _require_unchanged_ownership(
        self,
        plan: Plan,
        *,
        issue: int,
        roles_ended: bool,
    ) -> None:
        current, reasons, errors = self._ownership_boundary_snapshot(
            plan.branch,
            issue,
            roles_ended=roles_ended,
        )
        if reasons or errors or current != plan.facts.get("ownership_boundary"):
            raise RetirementError("worktree/lease/process/recovery ownership facts drifted")

    def _record_path(self, branch_ref: str) -> Path:
        return self.record_dir / f"{_record_key(branch_ref)}.json"

    def _validate_retirement_record(self, payload: dict[str, Any], branch_ref: str) -> None:
        branch = branch_ref.removeprefix("refs/heads/")
        issue = _issue_from_branch(branch)
        required = {
            "version",
            "branch",
            "branch_ref",
            "tip",
            "issue",
            "reviewed_main_sha",
            "evidence_type",
            "replacement_commit",
            "run_id",
            "run_head_sha",
            "run_result",
            "authorization_urls",
            "actor",
            "timestamp",
            "plan_digest",
            "archive_ref",
            "backup_ref",
            "record_path",
        }
        tip = payload.get("tip")
        full_sha_fields = ("tip", "reviewed_main_sha", "replacement_commit", "run_head_sha")
        valid = (
            set(payload) == required
            and issue is not None
            and payload.get("version") == 1
            and payload.get("branch") == branch
            and payload.get("branch_ref") == branch_ref
            and payload.get("issue") == issue
            and all(
                isinstance(payload.get(key), str) and FULL_SHA_RE.fullmatch(payload[key]) is not None
                for key in full_sha_fields
            )
            and self._commit_oid(str(tip)) == tip
            and payload.get("evidence_type") in {"patch-equivalent", "explicit-supersession"}
            and isinstance(payload.get("run_id"), str)
            and payload["run_id"].isdigit()
            and payload.get("run_result") == "success"
            and isinstance(payload.get("authorization_urls"), list)
            and len(payload["authorization_urls"]) in {1, 2}
            and payload["authorization_urls"] == sorted(set(payload["authorization_urls"]))
            and all(isinstance(url, str) and bool(url) for url in payload["authorization_urls"])
            and len(payload["authorization_urls"]) == (1 if payload.get("evidence_type") == "patch-equivalent" else 2)
            and isinstance(payload.get("actor"), str)
            and bool(payload["actor"])
            and isinstance(payload.get("timestamp"), str)
            and bool(payload["timestamp"])
            and isinstance(payload.get("plan_digest"), str)
            and re.fullmatch(r"[0-9a-f]{64}", payload["plan_digest"]) is not None
            and payload.get("archive_ref") == _archive_ref(branch, str(tip))
            and payload.get("backup_ref") == _backup_ref(branch, str(tip))
            and payload.get("record_path") == str(self._record_path(branch_ref))
        )
        if not valid:
            raise RetirementError("retirement record schema or derived identity is malformed")

    def _open_common_fd(self) -> int:
        try:
            metadata = self.common_dir.lstat()
            resolved = self.common_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise RetirementError(f"common Git directory is unsafe:{type(exc).__name__}") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or resolved != self.common_dir:
            raise RetirementError("common Git directory must be one resolved real directory")
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self.common_dir, flags)
        opened = os.fstat(fd)
        try:
            current = self.common_dir.lstat()
            current_resolved = self.common_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            os.close(fd)
            raise RetirementError(f"common Git directory changed while opening:{type(exc).__name__}") from exc
        identity = (opened.st_dev, opened.st_ino)
        expected = getattr(self, "common_identity", identity)
        if (
            identity != (metadata.st_dev, metadata.st_ino)
            or identity != (current.st_dev, current.st_ino)
            or identity != expected
            or current_resolved != self.common_dir
        ):
            os.close(fd)
            raise RetirementError("common Git directory identity changed")
        return fd

    def _assert_common_dir_current(self) -> None:
        fd = self._open_common_fd()
        os.close(fd)

    def _open_record_dir_fds(self, *, create: bool) -> tuple[int, int | None]:
        common_fd = self._open_common_fd()
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                record_fd = os.open(RECORD_DIRNAME, flags, dir_fd=common_fd)
            except FileNotFoundError:
                if not create:
                    return common_fd, None
                os.mkdir(RECORD_DIRNAME, mode=0o700, dir_fd=common_fd)
                record_fd = os.open(RECORD_DIRNAME, flags, dir_fd=common_fd)
            path_stat = os.stat(RECORD_DIRNAME, dir_fd=common_fd, follow_symlinks=False)
            fd_stat = os.fstat(record_fd)
            if (
                not stat.S_ISDIR(path_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or (path_stat.st_dev, path_stat.st_ino) != (fd_stat.st_dev, fd_stat.st_ino)
            ):
                os.close(record_fd)
                raise RetirementError("retirement record directory is not a stable real common-Git child")
            return common_fd, record_fd
        except Exception:
            os.close(common_fd)
            raise

    @staticmethod
    def _snapshot_from_fd(fd: int) -> RecordSnapshot:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RetirementError("retirement record is not a regular file")
        os.lseek(fd, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        raw = b"".join(chunks)
        try:
            payload = _strict_json_object(raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise RetirementError(f"retirement record JSON is invalid:{type(exc).__name__}") from exc
        return RecordSnapshot(
            payload=payload,
            content_sha256=hashlib.sha256(raw).hexdigest(),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            size=metadata.st_size,
        )

    def _read_record_snapshot(
        self,
        branch_ref: str,
    ) -> tuple[RecordSnapshot | None, list[str]]:
        path = self._record_path(branch_ref)
        common_fd: int | None = None
        record_dir_fd: int | None = None
        record_fd: int | None = None
        try:
            common_fd, record_dir_fd = self._open_record_dir_fds(create=False)
            if record_dir_fd is None:
                return None, []
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                record_fd = os.open(path.name, flags, dir_fd=record_dir_fd)
            except FileNotFoundError:
                return None, []
            snapshot = self._snapshot_from_fd(record_fd)
            payload = snapshot.payload
            self._validate_retirement_record(payload, branch_ref)
            return snapshot, []
        except (OSError, RetirementError) as exc:
            return None, [f"record boundary/read failure:{path}:{type(exc).__name__}:{exc}"]
        finally:
            for fd in (record_fd, record_dir_fd, common_fd):
                if fd is not None:
                    os.close(fd)

    def _read_record(self, branch_ref: str) -> tuple[dict[str, Any] | None, list[str]]:
        snapshot, errors = self._read_record_snapshot(branch_ref)
        return (snapshot.payload if snapshot else None), errors

    @contextmanager
    def _locked_record(self, branch_ref: str):
        """Hold the exact no-follow record inode stable across a ref transaction."""
        path = self._record_path(branch_ref)
        common_fd, record_dir_fd = self._open_record_dir_fds(create=False)
        record_fd: int | None = None
        try:
            if record_dir_fd is None:
                raise RetirementError("retirement record directory disappeared")
            record_fd = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=record_dir_fd,
            )
            fcntl.flock(record_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            snapshot = self._snapshot_from_fd(record_fd)
            if not self._record_handle_current(
                path.name,
                snapshot,
                common_fd=common_fd,
                record_dir_fd=record_dir_fd,
                record_fd=record_fd,
            ):
                raise RetirementError("retirement record identity drifted while acquiring lock")
            yield snapshot, common_fd, record_dir_fd, record_fd
        finally:
            if record_fd is not None:
                try:
                    fcntl.flock(record_fd, fcntl.LOCK_UN)
                finally:
                    os.close(record_fd)
            if record_dir_fd is not None:
                os.close(record_dir_fd)
            os.close(common_fd)

    def _record_handle_current(
        self,
        filename: str,
        expected: RecordSnapshot,
        *,
        common_fd: int,
        record_dir_fd: int,
        record_fd: int,
    ) -> bool:
        try:
            common_entry = os.stat(RECORD_DIRNAME, dir_fd=common_fd, follow_symlinks=False)
            directory = os.fstat(record_dir_fd)
            path_entry = os.stat(filename, dir_fd=record_dir_fd, follow_symlinks=False)
            current = self._snapshot_from_fd(record_fd)
        except (OSError, RetirementError):
            return False
        return (
            stat.S_ISDIR(common_entry.st_mode)
            and not stat.S_ISLNK(common_entry.st_mode)
            and (common_entry.st_dev, common_entry.st_ino) == (directory.st_dev, directory.st_ino)
            and stat.S_ISREG(path_entry.st_mode)
            and not stat.S_ISLNK(path_entry.st_mode)
            and (path_entry.st_dev, path_entry.st_ino) == (expected.device, expected.inode)
            and current == expected
        )

    def _archive_metadata(self, plan: Plan) -> dict[str, Any]:
        return {
            "version": 1,
            "branch": plan.branch,
            "branch_ref": plan.branch_ref,
            "tip": plan.tip,
            "issue": plan.issue,
            "reviewed_main_sha": plan.main_sha,
            "evidence_type": "patch-equivalent" if plan.matching_main_commit else "explicit-supersession",
            "replacement_commit": plan.matching_main_commit or plan.replacement_commit,
            "run_id": plan.terminal_run_id,
            "run_head_sha": plan.terminal_run_head,
            "run_result": plan.terminal_run_result,
            "authorization_urls": sorted(set(plan.accepted_author_input)),
            "actor": plan.actor,
            "timestamp": plan.timestamp,
            "plan_digest": plan.plan_digest,
            "archive_ref": plan.archive_ref,
            "backup_ref": plan.backup_ref,
            "record_path": plan.archive_record_path,
        }

    def _metadata_matches_plan(self, payload: dict[str, Any] | None, plan: Plan) -> bool:
        """Allow only the timestamp to survive from an interrupted prior apply."""
        if payload is None:
            return False
        expected = self._archive_metadata(plan)
        if set(payload) != set(expected):
            return False
        return all(payload[key] == expected[key] for key in expected if key != "timestamp")

    def _archive_payload(self, archive_ref: str) -> tuple[dict[str, Any] | None, str]:
        oid = self._ref_oid(archive_ref)
        if not oid:
            return None, ""
        result = self._git("cat-file", "tag", oid, check=False)
        if result.returncode:
            return None, "archive ref is not an annotated tag"
        text = result.stdout.decode(errors="replace")
        _, separator, message = text.partition("\n\n")
        if not separator:
            return None, "archive tag metadata missing"
        try:
            payload = _strict_json_object(message)
        except (json.JSONDecodeError, ValueError):
            return None, "archive tag metadata is invalid"
        return payload, ""

    def _archive_commit(self, archive_ref: str) -> str:
        return self._commit_oid(archive_ref)

    def _authoritative_restore_evidence(
        self,
        branch: str,
        record: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Re-prove stored retirement authority from GitHub and current Git."""
        errors: list[str] = []
        issue_number = _issue_from_branch(branch)
        if issue_number is None or record.get("issue") != issue_number:
            return {}, ["record issue is not derived from the requested branch"]
        main_sha = self._ref_oid("refs/heads/main")
        origin_main_sha = self._ref_oid("refs/remotes/origin/main")
        reviewed_main = str(record.get("reviewed_main_sha", ""))
        replacement = str(record.get("replacement_commit", ""))
        tip = str(record.get("tip", ""))
        if (
            not main_sha
            or main_sha != origin_main_sha
            or self._commit_oid(reviewed_main) != reviewed_main
            or not self._is_ancestor(reviewed_main, main_sha)
            or self._commit_oid(replacement) != replacement
            or not self._is_ancestor(replacement, reviewed_main)
        ):
            errors.append("reviewed main or replacement provenance is no longer authoritative")
        try:
            issue = self._issue(issue_number)
        except Exception as exc:
            return {}, [*errors, f"issue evidence lookup failed:{type(exc).__name__}"]
        if issue.author not in ALLOWED_AUTHORS:
            errors.append("issue author is not an accepted owner")
        if issue.state != "CLOSED" or {label.lower() for label in issue.labels} & {"human", "blocked"}:
            errors.append("issue is not closed and unblocked without human review")

        authorization_urls: list[str] = []
        evidence_type = record.get("evidence_type")
        if evidence_type == "patch-equivalent":
            patch_id = self._patch_id(tip)
            paths = _decode(self._git("diff-tree", "--no-commit-id", "--name-only", "-r", tip).stdout).splitlines()
            matches = self._matching_main_commits(patch_id, reviewed_main, paths)
            issue_pattern = re.compile(rf"(?<![0-9])#{issue_number}(?![0-9])")
            same_issue = [
                commit
                for commit in matches
                if issue_pattern.search(_decode(self._git("show", "-s", "--format=%B", commit).stdout))
            ]
            if same_issue != [replacement]:
                errors.append("stored patch-equivalent replacement is not the one exact same-issue match")
        elif evidence_type == "explicit-supersession":
            current_replacement, supersession_urls, supersession_errors = self._supersession(
                issue,
                branch,
                tip,
                reviewed_main,
            )
            authorization_urls.extend(supersession_urls)
            errors.extend(supersession_errors)
            if current_replacement != replacement:
                errors.append("stored replacement no longer has exact owner supersession authority")
        else:
            errors.append("retirement evidence type is invalid")

        run, run_urls = self._run_evidence(issue, replacement)
        authorization_urls.extend(run_urls)
        expected_urls = sorted(set(authorization_urls))
        if expected_urls != record.get("authorization_urls"):
            errors.append("stored authorization comment identities no longer match")
        if (
            run is None
            or run.run_id != record.get("run_id")
            or run.head_sha != record.get("run_head_sha")
            or run.conclusion != record.get("run_result")
        ):
            errors.append("stored terminal run is not currently authoritative")
        authorized_comments = sorted(
            (
                {
                    "author": comment["author"],
                    "url": comment["url"],
                    "body": comment["body"],
                }
                for comment in issue.comments
                if comment["url"] in expected_urls
            ),
            key=lambda comment: comment["url"],
        )
        facts = {
            "issue": asdict(issue),
            "main_sha": main_sha,
            "origin_main_sha": origin_main_sha,
            "reviewed_main_sha": reviewed_main,
            "replacement_commit": replacement,
            "authorization_urls": expected_urls,
            "authorization_comments": authorized_comments,
            "terminal_run": asdict(run) if run else None,
        }
        return facts, list(dict.fromkeys(errors))

    def _safe_authoritative_evidence(
        self,
        branch: str,
        record: dict[str, Any],
    ) -> tuple[dict[str, Any], list[str]]:
        """Return fail-closed authority evidence even when a refetch errors."""
        try:
            return self._authoritative_restore_evidence(branch, record)
        except Exception as exc:
            return {}, [f"authoritative evidence lookup failed:{type(exc).__name__}"]

    def _supersession(
        self,
        issue: IssueEvidence,
        branch: str,
        tip: str,
        main_sha: str,
    ) -> tuple[str, list[str], list[str]]:
        matches: list[tuple[str, str]] = []
        ambiguous = False
        for comment in issue.comments:
            if comment["author"] not in ALLOWED_AUTHORS:
                continue
            body = comment["body"]
            if not body.isascii():
                ambiguous = True
                continue
            if SUPERSESSION_NEGATION_RE.search(body) or _owner_comment_is_contradictory(body):
                ambiguous = True
            payload = self._structured_comment(
                body,
                marker=SUPERSESSION_EVIDENCE_MARKER,
                fields={"decision", "branch", "tip", "replacement_commit", "reason"},
            )
            if payload is None:
                if SUPERSESSION_EVIDENCE_MARKER in body:
                    ambiguous = True
                continue
            if payload.get("decision") != "retire-as-superseded":
                ambiguous = True
                continue
            reason = payload.get("reason")
            sha = payload.get("replacement_commit")
            if (
                payload.get("branch") != branch
                or payload.get("tip") != tip
                or not isinstance(reason, str)
                or len(reason.strip()) < 10
                or not isinstance(sha, str)
                or FULL_SHA_RE.fullmatch(sha) is None
            ):
                ambiguous = True
                continue
            sha = sha.lower()
            if self._commit_oid(sha) == sha and self._is_ancestor(sha, main_sha):
                matches.append((sha, comment["url"]))
            else:
                ambiguous = True
        if ambiguous or len(matches) != 1:
            return "", [url for _, url in matches], ["exact owner supersession decision is missing or ambiguous"]
        return matches[0][0], [matches[0][1]], []

    def _empty_plan(self, branch: str, *, mode: str, roles_ended: bool) -> Plan:
        return Plan(
            timestamp=self.now(),
            actor=self.actor,
            mode=mode,
            repository=str(self.repo),
            common_dir=str(self.common_dir),
            branch=branch,
            branch_ref=f"refs/heads/{branch}",
            roles_ended_asserted=roles_ended,
        )

    def plan(self, branch: str, *, mode: str = "classify", roles_ended: bool = False) -> Plan:
        plan = self._empty_plan(branch, mode=mode, roles_ended=roles_ended)
        reasons: list[str] = []
        errors: list[str] = []
        issue_number = _issue_from_branch(branch)
        valid_ref = self._git("check-ref-format", "--branch", branch, check=False).returncode == 0
        if not issue_number or not valid_ref:
            errors.append("branch must be one exact worktree-agent-* or worktree-tester-* full name")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        plan.issue = issue_number
        record, record_errors = self._read_record(plan.branch_ref)
        errors.extend(record_errors)
        if record_errors:
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        plan.archive_record_path = str(self._record_path(plan.branch_ref))

        tip = self._ref_oid(plan.branch_ref) if issue_number and valid_ref else ""
        if not tip and record and not record_errors:
            return self._plan_archived(plan, record, reasons, errors)
        if not tip:
            errors.append("exact local branch ref is missing or ambiguous")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
            return self._finish_plan(plan, reasons, errors, {})
        plan.tip = tip
        if self._git("symbolic-ref", "-q", plan.branch_ref, check=False).returncode == 0:
            errors.append("symbolic branch refs are protected")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)

        plan.main_sha = self._ref_oid("refs/heads/main")
        plan.origin_main_sha = self._ref_oid("refs/remotes/origin/main")
        if not plan.main_sha or plan.main_sha != plan.origin_main_sha:
            errors.append("local main and origin/main are missing or differ")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        worktrees = self._worktrees()
        plan.attached_worktrees = sorted(
            item["worktree"] for item in worktrees if item.get("branch") == plan.branch_ref
        )
        if plan.attached_worktrees:
            reasons.append(PROTECTED_ATTACHED_WORKTREE)

        if plan.main_sha and self._is_ancestor(tip, plan.main_sha):
            reasons.append(PROTECTED_ALREADY_MERGED)
        plan.merge_base = _decode(self._git("merge-base", plan.main_sha, tip, check=False).stdout)
        counts = _decode(
            self._git("rev-list", "--left-right", "--count", f"{plan.main_sha}...{tip}", check=False).stdout
        ).split()
        if len(counts) == 2 and all(item.isdigit() for item in counts):
            plan.behind, plan.ahead = map(int, counts)
        else:
            errors.append("ahead/behind counts could not be determined")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        divergence = _decode(self._git("rev-list", f"{plan.merge_base}..{tip}", check=False).stdout).splitlines()
        parents = _decode(self._git("rev-list", "--parents", "-n", "1", tip, check=False).stdout).split()
        if len(divergence) != 1 or len(parents) != 2:
            errors.append("bounded retirement requires exactly one divergent non-merge commit")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        plan.parent = parents[1] if len(parents) == 2 else ""
        plan.changed_paths = _decode(
            self._git("diff-tree", "--no-commit-id", "--name-only", "-r", tip).stdout
        ).splitlines()
        plan.subject = _decode(self._git("show", "-s", "--format=%s", tip).stdout)
        plan.patch_id = self._patch_id(tip)
        plan.cherry = _decode(self._git("cherry", plan.main_sha, tip, check=False).stdout)

        issue: IssueEvidence | None = None
        if issue_number:
            try:
                issue = self._issue(issue_number)
            except Exception as exc:
                errors.append(f"issue evidence lookup failed:{type(exc).__name__}")
                reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        if issue:
            plan.issue_state = issue.state
            plan.issue_labels = list(issue.labels)
            plan.issue_author = issue.author
            plan.issue_url = issue.url
            protected_labels = {label.lower() for label in issue.labels} & {"human", "blocked"}
            if issue.state != "CLOSED" or protected_labels:
                reasons.append(PROTECTED_OPEN_OR_HUMAN_ISSUE)
            if issue.author not in ALLOWED_AUTHORS:
                errors.append("issue author is not an accepted owner")
                reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)

        ownership, ownership_reasons, ownership_errors = self._ownership_boundary_snapshot(
            branch,
            issue_number or 0,
            roles_ended=roles_ended,
        )
        plan.attached_worktrees = ownership["attached_worktrees"]
        plan.lease_boundary = ownership["lease_boundary"]
        plan.lease_evidence = ownership["lease_evidence"]
        plan.recovery_boundary = ownership["recovery_boundary"]
        plan.recovery_paths = ownership["recovery_paths"]
        process = ProcessEvidence(**ownership["process"])
        plan.process_scan_complete = process.complete
        plan.process_ids = list(process.process_ids)
        plan.process_reasons = list(process.reasons)
        reasons.extend(ownership_reasons)
        errors.extend(ownership_errors)

        matching: list[str] = []
        if plan.patch_id and plan.main_sha:
            matching = self._matching_main_commits(plan.patch_id, plan.main_sha, plan.changed_paths)
        same_issue_matches = []
        if issue_number:
            issue_pattern = re.compile(rf"(?<![0-9])#{issue_number}(?![0-9])")
            same_issue_matches = [
                commit
                for commit in matching
                if issue_pattern.search(_decode(self._git("show", "-s", "--format=%B", commit).stdout))
            ]
        if len(same_issue_matches) == 1:
            plan.matching_main_commit = same_issue_matches[0]
        elif len(same_issue_matches) > 1:
            errors.append("multiple same-issue patch-equivalent main commits")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        elif issue and issue.state == "CLOSED":
            replacement, accepted, supersession_errors = self._supersession(issue, branch, tip, plan.main_sha)
            plan.replacement_commit = replacement
            plan.accepted_author_input.extend(accepted)
            errors.extend(supersession_errors)
            if not replacement:
                reasons.append(PROTECTED_UNIQUE_PATCH_WITHOUT_SUPERSESSION)

        evidence_commit = plan.matching_main_commit or plan.replacement_commit
        if issue and evidence_commit:
            run, accepted = self._run_evidence(issue, evidence_commit)
            plan.accepted_author_input.extend(accepted)
            if run:
                plan.terminal_run_id = run.run_id
                plan.terminal_run_head = run.head_sha
                plan.terminal_run_result = run.conclusion
                plan.terminal_run_url = run.url
            else:
                errors.append("accepted terminal Deploy Dev evidence is missing or ambiguous")
                reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)

        plan.archive_ref = _archive_ref(branch, tip)
        plan.backup_ref = _backup_ref(branch, tip)
        plan.accepted_author_input = sorted(set(plan.accepted_author_input))
        plan.restore_command = (
            f"uv run python scripts/retire-agent-branches.py --repo {shlex.quote(str(self.repo))} "
            f"--actor <actor> restore --branch {shlex.quote(branch)} --roles-ended"
        )
        facts = {
            "version": 1,
            "mode": mode,
            "actor": self.actor,
            "repository": str(self.repo),
            "common_dir": str(self.common_dir),
            "common_dir_identity": self.common_identity,
            "branch": branch,
            "branch_ref": plan.branch_ref,
            "tip": tip,
            "parent": plan.parent,
            "merge_base": plan.merge_base,
            "main_sha": plan.main_sha,
            "origin_main_sha": plan.origin_main_sha,
            "ahead": plan.ahead,
            "behind": plan.behind,
            "attached_worktrees": plan.attached_worktrees,
            "patch_id": plan.patch_id,
            "cherry": plan.cherry,
            "matching_main_commit": plan.matching_main_commit,
            "replacement_commit": plan.replacement_commit,
            "changed_paths": plan.changed_paths,
            "subject": plan.subject,
            "issue": asdict(issue) if issue else None,
            "accepted_author_input": sorted(set(plan.accepted_author_input)),
            "terminal_run": {
                "id": plan.terminal_run_id,
                "head": plan.terminal_run_head,
                "result": plan.terminal_run_result,
                "url": plan.terminal_run_url,
            },
            "roles_ended_asserted": roles_ended,
            "lease_evidence": plan.lease_evidence,
            "lease_boundary": plan.lease_boundary,
            "process": asdict(process),
            "recovery_paths": plan.recovery_paths,
            "recovery_boundary": plan.recovery_boundary,
            "ownership_boundary": ownership,
            "archive_ref": plan.archive_ref,
            "backup_ref": plan.backup_ref,
            "archive_availability": "absent-or-exact-resumable",
            "backup_availability": "absent-or-exact-resumable",
            "record_path": plan.archive_record_path,
            "record_availability": "absent-or-exact-resumable",
        }
        plan = self._finish_plan(plan, reasons, errors, facts)
        archive_payload, archive_error = self._archive_payload(plan.archive_ref)
        backup_oid = self._ref_oid(plan.backup_ref)
        transition_errors: list[str] = []
        if archive_error:
            transition_errors.append(archive_error)
        if archive_payload is not None and not self._metadata_matches_plan(archive_payload, plan):
            transition_errors.append("archive ref collision or evidence drift")
        if backup_oid and backup_oid != plan.tip:
            transition_errors.append("backup ref collision or evidence drift")
        if record is not None:
            if not self._metadata_matches_plan(record, plan):
                transition_errors.append("retirement record collision or evidence drift")
            if archive_payload is None:
                transition_errors.append("retirement record exists without its archive ref")
            elif record != archive_payload:
                transition_errors.append("archive and retirement record disagree")
        if transition_errors:
            plan.errors.extend(transition_errors)
            plan.errors = list(dict.fromkeys(plan.errors))
            if PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE not in plan.reasons:
                plan.reasons.insert(0, PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
            plan.classification = PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE
            plan.facts.update(
                {
                    "archive_existing_payload": archive_payload,
                    "backup_existing_oid": backup_oid,
                    "record_existing_payload": record,
                    "classification": plan.classification,
                    "reasons": plan.reasons,
                    "errors": plan.errors,
                }
            )
            plan.seal()
        return plan

    def _finish_plan(
        self,
        plan: Plan,
        reasons: Sequence[str],
        errors: Sequence[str],
        facts: dict[str, Any],
    ) -> Plan:
        plan.reasons = list(dict.fromkeys(reasons))
        plan.errors = list(dict.fromkeys(errors))
        blockers = [reason for reason in plan.reasons if reason != RETAIN_UNMERGED_UNARCHIVED]
        if not blockers and plan.tip and plan.terminal_run_result == "success":
            plan.classification = ELIGIBLE_ARCHIVE_RETIRE
            plan.reasons = [ELIGIBLE_ARCHIVE_RETIRE]
        elif plan.reasons:
            plan.classification = plan.reasons[0]
            if plan.tip and RETAIN_UNMERGED_UNARCHIVED not in plan.reasons:
                plan.reasons.append(RETAIN_UNMERGED_UNARCHIVED)
        else:
            plan.classification = RETAIN_UNMERGED_UNARCHIVED
            plan.reasons = [RETAIN_UNMERGED_UNARCHIVED]
        plan.requested_actions = (
            [
                f"archive {plan.branch_ref} at {plan.archive_ref}",
                f"backup {plan.branch_ref} at {plan.backup_ref}",
                f"compare-delete {plan.branch_ref} {plan.tip}",
            ]
            if plan.mode == "archive-retire"
            else []
        )
        facts = {**facts, "classification": plan.classification, "reasons": plan.reasons, "errors": plan.errors}
        plan.facts = facts
        return plan.seal()

    def _plan_archived(
        self,
        plan: Plan,
        record: dict[str, Any],
        reasons: list[str],
        errors: list[str],
    ) -> Plan:
        plan.tip = str(record.get("tip", ""))
        plan.issue = record.get("issue")
        plan.main_sha = str(record.get("reviewed_main_sha", ""))
        plan.archive_ref = str(record.get("archive_ref", ""))
        plan.backup_ref = str(record.get("backup_ref", ""))
        plan.plan_digest = str(record.get("plan_digest", ""))
        plan.archive_record_path = str(self._record_path(plan.branch_ref))
        archive_payload, archive_error = self._archive_payload(plan.archive_ref)
        if (
            archive_error
            or archive_payload != record
            or self._archive_commit(plan.archive_ref) != plan.tip
            or self._ref_oid(plan.backup_ref) != plan.tip
        ):
            errors.append(archive_error or "archive and retirement record disagree")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
            return self._finish_plan(plan, reasons, errors, {"record": record, "archive": archive_payload})
        plan.classification = ARCHIVED_RETIRED
        plan.reasons = [ARCHIVED_RETIRED]
        plan.restore_command = (
            f"uv run python scripts/retire-agent-branches.py --repo {shlex.quote(str(self.repo))} "
            f"--actor <actor> restore --branch {shlex.quote(plan.branch)} --roles-ended"
        )
        plan.facts = {"archived_record": record}
        return plan

    def _tag_object(self, plan: Plan, metadata: dict[str, Any]) -> str:
        self._assert_common_dir_current()
        tag_name = plan.archive_ref.removeprefix("refs/tags/")
        body = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        raw = (
            f"object {plan.tip}\n"
            "type commit\n"
            f"tag {tag_name}\n"
            f"tagger {self.actor} <agent-branch-retirement@local> {int(datetime.now(UTC).timestamp())} +0000\n\n"
            f"{body}\n"
        ).encode()
        return _decode(self._git("mktag", input_bytes=raw).stdout)

    def _write_record(self, path: Path, payload: dict[str, Any]) -> None:
        data = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()
        if path != self._record_path(str(payload.get("branch_ref", ""))):
            raise RetirementError("retirement record target does not match its exact branch identity")
        common_fd, record_dir_fd = self._open_record_dir_fds(create=True)
        assert record_dir_fd is not None
        fd: int | None = None
        try:
            fd = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=record_dir_fd,
            )
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise RetirementError("new retirement record is not a regular file")
            remaining = memoryview(data)
            while remaining:
                written = os.write(fd, remaining)
                if written < 1:
                    raise RetirementError("retirement record write made no progress")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            if fd is not None:
                os.close(fd)
            os.fsync(record_dir_fd)
            os.close(record_dir_fd)
            os.close(common_fd)

    def _ref_transaction(self, commands: Sequence[str]) -> CommandResult:
        self._assert_common_dir_current()
        script = "start\n" + "\n".join(commands) + "\nprepare\ncommit\n"
        return self._git("update-ref", "--stdin", input_bytes=script.encode(), check=False)

    def _restore_source_after_failed_postcondition(self, plan: Plan) -> bool:
        """Recreate an absent reviewed source without trusting a drifted archive."""
        if self._ref_oid(plan.branch_ref) == plan.tip:
            return True
        if self._ref_oid(plan.branch_ref) or self._commit_oid(plan.tip) != plan.tip:
            return False
        result = self._ref_transaction((f"create {plan.branch_ref} {plan.tip}",))
        return result.returncode == 0 and self._ref_oid(plan.branch_ref) == plan.tip

    def _remove_failed_restore(self, plan: Plan) -> bool:
        if not self._ref_oid(plan.branch_ref):
            return True
        result = self._ref_transaction(
            (
                f"verify {plan.backup_ref} {plan.tip}",
                f"delete {plan.branch_ref} {plan.tip}",
            )
        )
        return result.returncode == 0 and not self._ref_oid(plan.branch_ref)

    def archive_retire(self, branch: str, *, plan_digest: str, roles_ended: bool) -> Plan:
        plan = self.plan(branch, mode="archive-retire", roles_ended=roles_ended)
        if plan.classification == ARCHIVED_RETIRED and plan.plan_digest == plan_digest:
            return plan
        if not roles_ended or plan.plan_digest != plan_digest or plan.classification != ELIGIBLE_ARCHIVE_RETIRE:
            plan.exit_status = 2
            plan.errors.append("exact eligible plan, digest, action, actor, and --roles-ended are required")
            return plan
        metadata = self._archive_metadata(plan)
        archive_payload, archive_error = self._archive_payload(plan.archive_ref)
        if archive_payload is not None and self._metadata_matches_plan(archive_payload, plan):
            metadata = archive_payload
        if archive_error or (archive_payload is not None and archive_payload != metadata):
            plan.exit_status = 2
            plan.errors.append(archive_error or "archive ref collision")
            return plan
        if archive_payload is None:
            self.failure_hook("before-archive")
            tag_oid = self._tag_object(plan, metadata)
            self._assert_common_dir_current()
            created = self._git("update-ref", plan.archive_ref, tag_oid, ZERO_OID, check=False)
            if created.returncode:
                plan.exit_status = 1
                plan.errors.append(_decode(created.stderr) or "archive compare-create failed")
                return plan
            plan.completed_actions.append(f"created {plan.archive_ref}")
        self.failure_hook("after-archive")
        if self._archive_commit(plan.archive_ref) != plan.tip:
            plan.exit_status = 2
            plan.errors.append("archive does not resolve to exact source tip")
            return plan
        backup_oid = self._ref_oid(plan.backup_ref)
        if backup_oid and backup_oid != plan.tip:
            plan.exit_status = 2
            plan.errors.append("immutable backup ref collision")
            return plan
        if not backup_oid:
            self._assert_common_dir_current()
            backed_up = self._git(
                "update-ref",
                plan.backup_ref,
                plan.tip,
                ZERO_OID,
                check=False,
            )
            if backed_up.returncode:
                plan.exit_status = 1
                plan.errors.append(_decode(backed_up.stderr) or "backup compare-create failed")
                return plan
            plan.completed_actions.append(f"created immutable {plan.backup_ref}")
        if self._ref_oid(plan.backup_ref) != plan.tip:
            plan.exit_status = 2
            plan.errors.append("immutable backup does not resolve to exact source tip")
            return plan
        record_path = Path(plan.archive_record_path)
        record, record_errors = self._read_record(plan.branch_ref)
        if record_errors or (record is not None and record != metadata):
            plan.exit_status = 2
            plan.errors.extend(record_errors or ["retirement record collision"])
            return plan
        if record is None:
            try:
                self._write_record(record_path, metadata)
            except (OSError, RetirementError) as exc:
                plan.exit_status = 1
                plan.errors.append(f"retirement record compare-create failed:{type(exc).__name__}")
                return plan
            plan.completed_actions.append(f"created {record_path}")
        self.failure_hook("after-record")
        record, record_errors = self._read_record(plan.branch_ref)
        archive_payload, archive_error = self._archive_payload(plan.archive_ref)
        if record_errors or archive_error or record != metadata or archive_payload != metadata:
            plan.exit_status = 2
            plan.errors.extend(record_errors + ([archive_error] if archive_error else []))
            plan.errors.append("archive/record verification failed before deletion")
            return plan
        self.failure_hook("before-delete")
        try:
            with self._locked_record(plan.branch_ref) as (
                locked_record,
                common_fd,
                record_dir_fd,
                record_fd,
            ):
                if locked_record.payload != metadata:
                    raise RetirementError("locked retirement record differs from reviewed evidence")
                revalidated = self.plan(branch, mode="archive-retire", roles_ended=roles_ended)
                if revalidated.plan_digest != plan_digest or revalidated.classification != ELIGIBLE_ARCHIVE_RETIRE:
                    raise RetirementError("facts drifted after archival and before source deletion")
                archive_oid = self._ref_oid(plan.archive_ref)
                if (
                    not archive_oid
                    or self._archive_commit(plan.archive_ref) != plan.tip
                    or self._ref_oid(plan.backup_ref) != plan.tip
                    or not self._record_handle_current(
                        Path(plan.archive_record_path).name,
                        locked_record,
                        common_fd=common_fd,
                        record_dir_fd=record_dir_fd,
                        record_fd=record_fd,
                    )
                ):
                    raise RetirementError("archive or record drifted before retirement transaction")
                self.failure_hook("before-retire-transaction")
                final_revalidated = self.plan(branch, mode="archive-retire", roles_ended=roles_ended)
                if (
                    final_revalidated.plan_digest != plan_digest
                    or final_revalidated.classification != ELIGIBLE_ARCHIVE_RETIRE
                ):
                    raise RetirementError("authority facts drifted at the final retirement boundary")
                authority_before, authority_errors = self._safe_authoritative_evidence(
                    plan.branch,
                    locked_record.payload,
                )
                if authority_errors:
                    raise RetirementError("authoritative issue/run/owner evidence is no longer valid")
                self._require_unchanged_ownership(
                    plan,
                    issue=plan.issue or 0,
                    roles_ended=roles_ended,
                )
                deleted = self._ref_transaction(
                    (
                        f"verify {plan.archive_ref} {archive_oid}",
                        f"verify {plan.backup_ref} {plan.tip}",
                        f"delete {plan.branch_ref} {plan.tip}",
                    )
                )
                if deleted.returncode:
                    plan.exit_status = 1
                    plan.errors.append(
                        _decode(deleted.stderr) or "atomic archive-verify/source-delete transaction failed"
                    )
                    return plan
                self.failure_hook("after-retire-transaction")
                self.failure_hook("after-delete")
                ownership_holds = True
                authority_holds = True
                try:
                    self._require_unchanged_ownership(
                        plan,
                        issue=plan.issue or 0,
                        roles_ended=roles_ended,
                    )
                except RetirementError:
                    ownership_holds = False
                authority_after, authority_errors = self._safe_authoritative_evidence(
                    plan.branch,
                    locked_record.payload,
                )
                if authority_errors or authority_after != authority_before:
                    authority_holds = False
                postconditions_hold = (
                    not self._ref_oid(plan.branch_ref)
                    and self._ref_oid(plan.archive_ref) == archive_oid
                    and self._archive_commit(plan.archive_ref) == plan.tip
                    and self._ref_oid(plan.backup_ref) == plan.tip
                    and ownership_holds
                    and authority_holds
                    and self._record_handle_current(
                        Path(plan.archive_record_path).name,
                        locked_record,
                        common_fd=common_fd,
                        record_dir_fd=record_dir_fd,
                        record_fd=record_fd,
                    )
                )
                if not postconditions_hold:
                    plan.exit_status = 2
                    plan.errors.append("post-retirement archive/record verification failed")
                    if not self._restore_source_after_failed_postcondition(plan):
                        plan.errors.append("source rollback could not be proven; preserved refs must be inspected")
                    return plan
        except (OSError, RetirementError) as exc:
            plan.exit_status = 2
            plan.errors.append(f"retirement transaction refused:{type(exc).__name__}:{exc}")
            return plan
        plan.completed_actions.append(f"compare-deleted {plan.branch_ref} at {plan.tip}")
        plan.classification = ARCHIVED_RETIRED
        plan.reasons = [ARCHIVED_RETIRED]
        return plan

    def restore_plan(self, branch: str, *, roles_ended: bool) -> Plan:
        plan = self._empty_plan(branch, mode="restore", roles_ended=roles_ended)
        issue_number = _issue_from_branch(branch)
        plan.issue = issue_number
        record, errors = self._read_record(plan.branch_ref)
        plan.archive_record_path = str(self._record_path(plan.branch_ref))
        reasons: list[str] = []
        if not issue_number or not record:
            errors.append("one exact valid retirement record is required")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
            return self._finish_restore(plan, record, reasons, errors)
        plan.tip = str(record.get("tip", ""))
        plan.archive_ref = str(record.get("archive_ref", ""))
        plan.backup_ref = str(record.get("backup_ref", ""))
        plan.main_sha = self._ref_oid("refs/heads/main")
        plan.origin_main_sha = self._ref_oid("refs/remotes/origin/main")
        archive_payload, archive_error = self._archive_payload(plan.archive_ref)
        if (
            archive_error
            or archive_payload != record
            or self._archive_commit(plan.archive_ref) != plan.tip
            or self._ref_oid(plan.backup_ref) != plan.tip
        ):
            errors.append(archive_error or "archive and record disagree")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        authority, authority_errors = self._safe_authoritative_evidence(branch, record)
        plan.authoritative_restore = authority
        if authority_errors:
            errors.extend(authority_errors)
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        if self._ref_oid(plan.branch_ref):
            errors.append("original branch ref already exists")
            reasons.append(PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
        ownership, ownership_reasons, ownership_errors = self._ownership_boundary_snapshot(
            branch,
            issue_number,
            roles_ended=roles_ended,
        )
        plan.attached_worktrees = ownership["attached_worktrees"]
        plan.lease_boundary = ownership["lease_boundary"]
        plan.lease_evidence = ownership["lease_evidence"]
        plan.recovery_boundary = ownership["recovery_boundary"]
        plan.recovery_paths = ownership["recovery_paths"]
        process = ProcessEvidence(**ownership["process"])
        plan.process_scan_complete = process.complete
        plan.process_ids = list(process.process_ids)
        plan.process_reasons = list(process.reasons)
        reasons.extend(ownership_reasons)
        errors.extend(ownership_errors)
        plan.facts["ownership_boundary"] = ownership
        return self._finish_restore(plan, record, reasons, errors)

    def _finish_restore(
        self,
        plan: Plan,
        record: dict[str, Any] | None,
        reasons: Sequence[str],
        errors: Sequence[str],
    ) -> Plan:
        plan.reasons = list(dict.fromkeys(reasons))
        plan.errors = list(dict.fromkeys(errors))
        if not plan.reasons and record:
            plan.classification = ELIGIBLE_RESTORE
            plan.reasons = [ELIGIBLE_RESTORE]
        elif plan.reasons:
            plan.classification = plan.reasons[0]
        plan.requested_actions = [f"compare-create {plan.branch_ref} {plan.tip}"]
        record_snapshot, record_snapshot_errors = self._read_record_snapshot(plan.branch_ref)
        if record_snapshot_errors:
            plan.errors.extend(record_snapshot_errors)
            plan.errors = list(dict.fromkeys(plan.errors))
            if PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE not in plan.reasons:
                plan.reasons.insert(0, PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE)
            plan.classification = PROTECTED_MALFORMED_OR_AMBIGUOUS_EVIDENCE
        plan.facts = {
            "version": 1,
            "mode": "restore",
            "actor": self.actor,
            "repository": str(self.repo),
            "common_dir": str(self.common_dir),
            "common_dir_identity": self.common_identity,
            "branch": plan.branch,
            "branch_ref": plan.branch_ref,
            "record": record,
            "record_snapshot": asdict(record_snapshot) if record_snapshot else None,
            "main_sha": plan.main_sha,
            "origin_main_sha": plan.origin_main_sha,
            "archive_ref": plan.archive_ref,
            "archive_ref_oid": self._ref_oid(plan.archive_ref) if plan.archive_ref else "",
            "archive_commit": self._archive_commit(plan.archive_ref) if plan.archive_ref else "",
            "backup_ref": plan.backup_ref,
            "backup_ref_oid": self._ref_oid(plan.backup_ref) if plan.backup_ref else "",
            "authoritative_restore": plan.authoritative_restore,
            "roles_ended_asserted": plan.roles_ended_asserted,
            "attached_worktrees": plan.attached_worktrees,
            "lease_boundary": plan.lease_boundary,
            "lease_evidence": plan.lease_evidence,
            "process_scan_complete": plan.process_scan_complete,
            "process_ids": plan.process_ids,
            "process_reasons": plan.process_reasons,
            "recovery_paths": plan.recovery_paths,
            "recovery_boundary": plan.recovery_boundary,
            "ownership_boundary": {
                "attached_worktrees": plan.attached_worktrees,
                "lease_boundary": plan.lease_boundary,
                "lease_evidence": plan.lease_evidence,
                "process": {
                    "complete": plan.process_scan_complete,
                    "process_ids": tuple(plan.process_ids),
                    "reasons": tuple(plan.process_reasons),
                    "errors": tuple(),
                },
                "recovery_boundary": plan.recovery_boundary,
                "recovery_paths": plan.recovery_paths,
            },
            "classification": plan.classification,
            "reasons": plan.reasons,
            "errors": plan.errors,
        }
        return plan.seal()

    def restore(self, branch: str, *, plan_digest: str, roles_ended: bool) -> Plan:
        plan = self.restore_plan(branch, roles_ended=roles_ended)
        if not roles_ended or plan.plan_digest != plan_digest or plan.classification != ELIGIBLE_RESTORE:
            plan.exit_status = 2
            plan.errors.append("exact eligible restore plan, digest, actor, and --roles-ended are required")
            return plan
        self.failure_hook("before-restore-transaction")
        try:
            with self._locked_record(plan.branch_ref) as (
                locked_record,
                common_fd,
                record_dir_fd,
                record_fd,
            ):
                expected_snapshot = plan.facts.get("record_snapshot")
                if expected_snapshot != asdict(locked_record):
                    raise RetirementError("retirement record changed after restore planning")
                self._require_unchanged_ownership(
                    plan,
                    issue=plan.issue or 0,
                    roles_ended=roles_ended,
                )
                current_authority, authority_errors = self._safe_authoritative_evidence(
                    plan.branch,
                    locked_record.payload,
                )
                if authority_errors or current_authority != plan.facts.get("authoritative_restore"):
                    raise RetirementError("authoritative issue/run/owner evidence drifted before restore")
                archive_oid = self._ref_oid(plan.archive_ref)
                archive_payload, archive_error = self._archive_payload(plan.archive_ref)
                if (
                    archive_error
                    or archive_payload != locked_record.payload
                    or self._archive_commit(plan.archive_ref) != plan.tip
                    or self._ref_oid(plan.backup_ref) != plan.tip
                    or not self._record_handle_current(
                        Path(plan.archive_record_path).name,
                        locked_record,
                        common_fd=common_fd,
                        record_dir_fd=record_dir_fd,
                        record_fd=record_fd,
                    )
                ):
                    raise RetirementError("archive or record drifted before restore transaction")
                result = self._ref_transaction(
                    (
                        f"verify {plan.archive_ref} {archive_oid}",
                        f"verify {plan.backup_ref} {plan.tip}",
                        f"create {plan.branch_ref} {plan.tip}",
                    )
                )
                if result.returncode:
                    plan.exit_status = 1
                    plan.errors.append(
                        _decode(result.stderr) or "atomic archive-verify/restore-create transaction failed"
                    )
                    return plan
                self.failure_hook("after-restore-ref-transaction")
                ownership_holds = True
                authority_holds = True
                try:
                    self._require_unchanged_ownership(
                        plan,
                        issue=plan.issue or 0,
                        roles_ended=roles_ended,
                    )
                except RetirementError:
                    ownership_holds = False
                current_authority, authority_errors = self._safe_authoritative_evidence(
                    plan.branch,
                    locked_record.payload,
                )
                if authority_errors or current_authority != plan.facts.get("authoritative_restore"):
                    authority_holds = False
                postconditions_hold = (
                    self._ref_oid(plan.branch_ref) == plan.tip
                    and self._ref_oid(plan.archive_ref) == archive_oid
                    and self._archive_commit(plan.archive_ref) == plan.tip
                    and self._ref_oid(plan.backup_ref) == plan.tip
                    and ownership_holds
                    and authority_holds
                    and self._record_handle_current(
                        Path(plan.archive_record_path).name,
                        locked_record,
                        common_fd=common_fd,
                        record_dir_fd=record_dir_fd,
                        record_fd=record_fd,
                    )
                )
                if not postconditions_hold:
                    plan.exit_status = 2
                    plan.errors.append("post-restore archive/record verification failed")
                    if not self._remove_failed_restore(plan):
                        plan.errors.append("restore rollback could not be proven; preserved refs must be inspected")
                    return plan
        except (OSError, RetirementError) as exc:
            plan.exit_status = 2
            plan.errors.append(f"restore transaction refused:{type(exc).__name__}:{exc}")
            return plan
        plan.completed_actions.append(f"compare-created {plan.branch_ref} at {plan.tip}")
        return plan


def render_human(plan: Plan) -> str:
    return "\n".join(
        f"{key}={json.dumps(value, sort_keys=True, separators=(',', ':'))}" for key, value in plan.public_dict().items()
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--actor", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--branch", help="Exact full local agent branch (default read-only classify)")
    subparsers = parser.add_subparsers(dest="command")
    classify = subparsers.add_parser("classify", help="Read-only classification")
    classify.add_argument("--branch", required=True)
    classify.add_argument("--roles-ended", action="store_true")
    retire = subparsers.add_parser("archive-retire", help="Plan or apply recoverable retirement")
    retire.add_argument("--branch", required=True)
    retire.add_argument("--roles-ended", action="store_true")
    retire.add_argument("--apply", action="store_true")
    retire.add_argument("--plan-digest")
    restore = subparsers.add_parser("restore", help="Plan or apply restoration")
    restore.add_argument("--branch", required=True)
    restore.add_argument("--roles-ended", action="store_true")
    restore.add_argument("--apply", action="store_true")
    restore.add_argument("--plan-digest")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        service = RetirementService(args.repo, actor=args.actor)
        command = args.command or "classify"
        branch = args.branch
        if not branch:
            raise RetirementError("one exact --branch is required")
        roles_ended = bool(getattr(args, "roles_ended", False))
        if command == "classify":
            plan = service.plan(branch, roles_ended=roles_ended)
        elif command == "archive-retire":
            if args.apply:
                if not args.plan_digest:
                    raise RetirementError("--plan-digest is required with --apply")
                plan = service.archive_retire(branch, plan_digest=args.plan_digest, roles_ended=roles_ended)
            else:
                plan = service.plan(branch, mode="archive-retire", roles_ended=roles_ended)
        elif command == "restore":
            if args.apply:
                if not args.plan_digest:
                    raise RetirementError("--plan-digest is required with --apply")
                plan = service.restore(branch, plan_digest=args.plan_digest, roles_ended=roles_ended)
            else:
                plan = service.restore_plan(branch, roles_ended=roles_ended)
        else:
            raise RetirementError(f"unknown command: {command}")
        print(json.dumps(plan.public_dict(), sort_keys=True) if args.json else render_human(plan))
        return plan.exit_status
    except RetirementError as exc:
        print(json.dumps({"actor": args.actor, "error": str(exc), "exit_status": 2}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
