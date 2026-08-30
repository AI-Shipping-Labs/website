#!/usr/bin/env python3
"""Fail-closed lifecycle management for agent Git worktrees.

The default command is a read-only classifier. Destructive operations require
one explicit candidate, a reviewed plan digest, and complete revalidation.
Lifecycle records live in the common Git directory so they survive worktree
removal and role handoffs.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import secrets
import shlex
import stat
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
ELIGIBLE_LEASE_MIGRATION = "ELIGIBLE_LEASE_MIGRATION"
LEASE_MIGRATION_NOT_REQUIRED = "LEASE_MIGRATION_NOT_REQUIRED"

REPO_SLUG = "AI-Shipping-Labs/website"
DEPLOY_WORKFLOW = "Deploy Dev"
LEASE_DIRNAME = "agent-worktree-leases"
RENAME_NOREPLACE = 1


def rename_noreplace(
    source_dir_fd: int,
    source_name: str,
    destination_dir_fd: int,
    destination_name: str,
) -> None:
    """Atomically rename one directory entry without replacing another."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise OSError(errno.ENOSYS, "renameat2 is unavailable")
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_dir_fd,
        os.fsencode(source_name),
        destination_dir_fd,
        os.fsencode(destination_name),
        RENAME_NOREPLACE,
    )
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), source_name, destination_name)


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


@dataclass(frozen=True)
class LeaseLookup:
    lease: dict[str, Any] | None
    errors: tuple[str, ...]
    source: str
    source_file: Path | None
    source_key: str | None
    stored_path: str | None
    canonical_path: Path
    canonical_key: str
    content_digest: str | None
    evidence_sources: tuple[tuple[str, str], ...] = ()

    def evidence_description(self) -> str:
        return ",".join(f"path={source_file};key={source_key}" for source_file, source_key in self.evidence_sources)

    def facts(self) -> dict[str, Any]:
        return {
            "lookup_source": self.source,
            "source_file": str(self.source_file) if self.source_file else None,
            "source_key": self.source_key,
            "stored_path": self.stored_path,
            "canonical_path": str(self.canonical_path),
            "canonical_key": self.canonical_key,
            "content_digest": self.content_digest,
            "evidence_sources": [
                {"source_file": source_file, "source_key": source_key}
                for source_file, source_key in self.evidence_sources
            ],
            "errors": list(self.errors),
        }


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
    boundary: str | None = None
    lease_lookup_source: str | None = None
    lease_source_file: str | None = None
    lease_stored_path: str | None = None
    lease_source_key: str | None = None
    lease_canonical_path: str | None = None
    lease_canonical_key: str | None = None
    lease_content_digest: str | None = None
    lease_evidence_sources: list[dict[str, str]] = field(default_factory=list)
    registration_snapshot: list[dict[str, Any]] = field(default_factory=list)
    migration_entries: dict[str, dict[str, Any]] = field(default_factory=dict)
    lease_json_manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    migration_process_snapshot: dict[str, Any] = field(default_factory=dict)
    lease_directory_snapshot: dict[str, Any] = field(default_factory=dict)
    registered: bool | None = None
    path_exists: bool | None = None
    roles_ended_asserted: bool | None = None
    terminal_evidence_present: bool | None = None
    lease_actor: str | None = None
    lease_role: str | None = None
    lease_created_at: str | None = None
    lease_updated_at: str | None = None

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
            errors=tuple(f"proc-visibility:{kind}:count={count}" for kind, count in sorted(error_counts.items())),
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
        migration_hook: Callable[[str], None] | None = None,
    ):
        self.repo = canonical(repo)
        if not actor.strip():
            raise CleanupError("actor identity must not be blank")
        self.actor = actor
        self.runner = runner or CommandRunner()
        self.gh_runner = gh_runner or self._run_gh
        self.process_scanner = process_scanner or ProcessScanner()
        self.now = now
        self.migration_hook = migration_hook or (lambda transition: None)
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
        configured = self.shared_main(worktrees).path / ".claude" / "worktrees"
        try:
            resolved = configured.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CleanupError("agent worktree boundary cannot be resolved safely") from exc
        if not resolved.is_dir():
            raise CleanupError("agent worktree boundary is not a directory")
        return resolved

    def _lease_path(self, path: Path) -> Path:
        return self.lease_dir / f"{lease_key(path)}.json"

    @staticmethod
    def _entry_exists(path: Path) -> bool:
        return os.path.lexists(path)

    @staticmethod
    def _entry_kind(path: Path) -> str:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return "missing"
        except OSError as exc:
            return f"unreadable:{type(exc).__name__}"
        for predicate, label in (
            (stat.S_ISREG, "regular"),
            (stat.S_ISLNK, "symlink"),
            (stat.S_ISDIR, "directory"),
            (stat.S_ISFIFO, "fifo"),
            (stat.S_ISSOCK, "socket"),
            (stat.S_ISCHR, "character-device"),
            (stat.S_ISBLK, "block-device"),
        ):
            if predicate(mode):
                return label
        return "other"

    def _entry_snapshot(self, path: Path) -> dict[str, Any]:
        snapshot: dict[str, Any] = {"path": str(path), "kind": self._entry_kind(path)}
        if snapshot["kind"] == "missing":
            return snapshot
        try:
            metadata = path.lstat()
        except OSError as exc:
            snapshot["error"] = type(exc).__name__
            return snapshot
        snapshot.update(
            {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "mode": stat.S_IMODE(metadata.st_mode),
                "size": metadata.st_size,
            }
        )
        if snapshot["kind"] == "regular":
            snapshot["digest"] = self._file_digest(path)
        return snapshot

    @staticmethod
    def _read_regular_bytes(path: Path) -> tuple[bytes | None, list[str]]:
        try:
            before = path.lstat()
        except OSError as exc:
            return None, [f"lease unreadable:{type(exc).__name__}:{path}"]
        if not stat.S_ISREG(before.st_mode):
            return None, [f"lease evidence is not a regular file:{path}"]

        descriptor = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            after = os.fstat(descriptor)
            if not stat.S_ISREG(after.st_mode) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                return None, [f"lease evidence changed during no-follow read:{path}"]
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                return stream.read(), []
        except OSError as exc:
            return None, [f"lease unreadable:{type(exc).__name__}:{path}"]
        finally:
            if descriptor is not None:
                os.close(descriptor)

    @staticmethod
    def _validate_lease_data(data: Any, *, expected_path: str) -> tuple[dict[str, Any] | None, list[str]]:
        if not isinstance(data, dict):
            return None, ["lease payload is not an object"]
        required = {"version", "issue", "path", "state", "actor", "created_at", "updated_at"}
        errors = [f"lease missing field:{key}" for key in sorted(required - data.keys())]
        if data.get("version") != 1:
            errors.append("lease version unsupported")
        if data.get("path") != expected_path:
            errors.append("lease path mismatch")
        if data.get("state") not in {"active", "terminal"}:
            errors.append("lease state invalid")
        if not isinstance(data.get("issue"), int) or isinstance(data.get("issue"), bool) or data.get("issue", 0) < 1:
            errors.append("lease issue invalid")
        for key in ("actor", "created_at", "updated_at"):
            if not isinstance(data.get(key), str) or not data.get(key):
                errors.append(f"lease {key} invalid")
        return (data if not errors else None), errors

    def _read_lease_file(
        self,
        lease_path: Path,
        *,
        expected_path: str,
    ) -> tuple[dict[str, Any] | None, bytes | None, str | None, list[str]]:
        raw, read_errors = self._read_regular_bytes(lease_path)
        if read_errors or raw is None:
            return None, None, None, read_errors
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, None, None, [f"lease unreadable:{type(exc).__name__}:{lease_path}"]
        digest = hashlib.sha256(raw).hexdigest()
        valid, errors = self._validate_lease_data(data, expected_path=expected_path)
        return valid, raw, digest, errors

    def _lookup_lease(
        self,
        path: Path,
        *,
        worktrees: Sequence[Worktree] | None = None,
        main: Worktree | None = None,
        boundary: Path | None = None,
        allow_migration_artifacts: bool = False,
    ) -> LeaseLookup:
        path = canonical(path)
        worktrees = list(self.worktrees() if worktrees is None else worktrees)
        main = main or self.shared_main(worktrees)
        boundary = boundary or self.boundary(worktrees)
        canonical_key = lease_key(path)
        canonical_file = self.lease_dir / f"{canonical_key}.json"
        alias_root = main.path / ".claude" / "worktrees"
        expected_alias = alias_root / path.name
        expected_alias_key = lease_key(expected_alias)
        expected_alias_file = self.lease_dir / f"{expected_alias_key}.json"
        migration_artifacts = (
            self.lease_dir / f".{canonical_key}.migration-new",
            self.lease_dir / f".{canonical_key}.migration-source",
            self.lease_dir / f".{canonical_key}.migration-retained",
        )

        def evidence_sources(files: Sequence[Path]) -> tuple[tuple[str, str], ...]:
            return tuple((str(item), item.stem) for item in sorted(files, key=lambda item: item.name))

        present_artifacts = [artifact for artifact in migration_artifacts if self._entry_exists(artifact)]
        if present_artifacts and not allow_migration_artifacts:
            return LeaseLookup(
                None,
                ("lease migration is incomplete:" + ",".join(map(str, present_artifacts)),),
                "invalid",
                None,
                None,
                None,
                path,
                canonical_key,
                None,
                evidence_sources(present_artifacts),
            )

        if not self.lease_dir.exists():
            return LeaseLookup(None, ("lease missing",), "missing", None, None, None, path, canonical_key, None)
        try:
            files = sorted(self.lease_dir.glob("*.json"), key=lambda item: item.name)
        except OSError as exc:
            return LeaseLookup(
                None,
                (f"lease directory unreadable:{type(exc).__name__}",),
                "invalid",
                None,
                None,
                None,
                path,
                canonical_key,
                None,
            )

        parsed: dict[Path, tuple[Any, bytes | None, str | None, list[str]]] = {}
        associated: list[Path] = []
        for lease_file in files:
            exact_evidence = lease_file in {canonical_file, expected_alias_file}
            raw, parse_errors = self._read_regular_bytes(lease_file)
            if not parse_errors and raw is not None:
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    payload, raw = None, None
                    parse_errors = [f"lease unreadable:{type(exc).__name__}:{lease_file}"]
            else:
                payload = None
            if raw is not None:
                digest = hashlib.sha256(raw).hexdigest()
            else:
                digest = None
            parsed[lease_file] = (payload, raw, digest, parse_errors)
            stored = payload.get("path") if isinstance(payload, dict) else None
            same_identity = False
            if isinstance(stored, str) and stored:
                same_identity = stored in {str(path), str(expected_alias)}
                if not same_identity:
                    try:
                        same_identity = canonical(stored) == path
                    except (OSError, RuntimeError):
                        same_identity = False
            if exact_evidence or same_identity:
                associated.append(lease_file)

        errors: list[str] = []
        if canonical_file in associated:
            payload, raw, digest, parse_errors = parsed[canonical_file]
            errors.extend(parse_errors)
            valid = None
            if not parse_errors:
                valid, validation_errors = self._validate_lease_data(payload, expected_path=str(path))
                errors.extend(validation_errors)
                stored = payload.get("path") if isinstance(payload, dict) else None
                if isinstance(stored, str) and canonical_file.stem != lease_key(Path(stored)):
                    errors.append(f"lease filename/hash mismatch:{canonical_file}")
            collisions = [item for item in associated if item != canonical_file]
            if collisions:
                errors.append("canonical/legacy lease collision:" + ",".join(map(str, collisions)))
            if errors:
                return LeaseLookup(
                    None,
                    tuple(dict.fromkeys(errors)),
                    "invalid",
                    canonical_file,
                    canonical_file.stem,
                    payload.get("path") if isinstance(payload, dict) else None,
                    path,
                    canonical_key,
                    digest,
                    evidence_sources([canonical_file, *collisions]),
                )
            return LeaseLookup(
                valid,
                (),
                "canonical",
                canonical_file,
                canonical_file.stem,
                str(path),
                path,
                canonical_key,
                digest,
                evidence_sources([canonical_file]),
            )

        if not associated:
            return LeaseLookup(None, ("lease missing",), "missing", None, None, None, path, canonical_key, None)
        if len(associated) != 1:
            conflicts = evidence_sources(associated)
            return LeaseLookup(
                None,
                (
                    "multiple legacy lease records resolve to candidate:"
                    + ",".join(f"path={source_file};key={source_key}" for source_file, source_key in conflicts),
                ),
                "invalid",
                None,
                None,
                None,
                path,
                canonical_key,
                None,
                conflicts,
            )

        source_file = associated[0]
        payload, raw, digest, parse_errors = parsed[source_file]
        errors.extend(parse_errors)
        stored = payload.get("path") if isinstance(payload, dict) else None
        if path.parent != boundary:
            errors.append("legacy alias compatibility requires a direct canonical boundary child")
        if source_file != expected_alias_file:
            errors.append(f"unexpected legacy lease key:{source_file}")
        if not isinstance(stored, str) or stored != str(expected_alias):
            errors.append(f"unexpected legacy alias path:{stored}")
        else:
            stored_path = Path(stored)
            if not stored_path.is_absolute() or ".." in stored_path.parts or stored_path.parent != alias_root:
                errors.append(f"legacy alias is not a direct child of fixed root:{stored}")
            if source_file.stem != lease_key(stored_path):
                errors.append(f"lease filename/hash mismatch:{source_file}")
            try:
                if canonical(expected_alias) != path:
                    errors.append("legacy alias does not resolve to exact canonical candidate")
            except (OSError, RuntimeError):
                errors.append("legacy alias cannot be resolved safely")
        valid = None
        if not parse_errors and isinstance(stored, str):
            valid, validation_errors = self._validate_lease_data(payload, expected_path=stored)
            errors.extend(validation_errors)
        if errors:
            return LeaseLookup(
                None,
                tuple(dict.fromkeys(errors)),
                "invalid",
                source_file,
                source_file.stem,
                stored if isinstance(stored, str) else None,
                path,
                canonical_key,
                digest,
                evidence_sources([source_file]),
            )
        return LeaseLookup(
            valid,
            (),
            "legacy-alias",
            source_file,
            source_file.stem,
            stored,
            path,
            canonical_key,
            digest,
            evidence_sources([source_file]),
        )

    def read_lease(self, path: Path) -> tuple[dict[str, Any] | None, list[str]]:
        lookup = self._lookup_lease(path)
        return lookup.lease, list(lookup.errors)

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
        worktrees = self.worktrees()
        matches = [wt for wt in worktrees if wt.path == path]
        if len(matches) != 1:
            raise CleanupError("candidate must be exactly one registered worktree")
        if path == self.shared_main(worktrees).path:
            raise CleanupError("shared main cannot have an agent lifecycle lease")
        if not is_below(path, self.boundary(worktrees)):
            raise CleanupError("candidate is outside the agent worktree boundary")
        return matches[0]

    def create_lease(self, *, path: Path, issue: int, role: str) -> Path:
        path = canonical(path)
        if issue < 1 or not role.strip():
            raise CleanupError("lease issue and role must be valid")
        self._registered_candidate(path)
        lookup = self._lookup_lease(path)
        if lookup.lease is not None or list(lookup.errors) != ["lease missing"]:
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
        lookup = self._lookup_lease(path)
        lease, errors = lookup.lease, list(lookup.errors)
        if adopt_legacy:
            if lease is not None or errors != ["lease missing"]:
                evidence = lookup.evidence_description() or (
                    str(lookup.source_file) if lookup.source_file else lookup.source
                )
                raise CleanupError(f"legacy adoption requires genuinely absent evidence; blocked by {evidence}")
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
        elif lookup.source == "legacy-alias":
            raise CleanupError("legacy alias lease must be reconciled before lifecycle closure")
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

    def _migration_paths(self, path: Path) -> tuple[Path, Path, Path]:
        key = lease_key(path)
        return (
            self._lease_path(path),
            self.lease_dir / f".{key}.migration-new",
            self.lease_dir / f".{key}.migration-source",
        )

    def _retained_source_path(self, path: Path) -> Path:
        return self.lease_dir / f".{lease_key(path)}.migration-retained"

    def _lease_directory_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "path": str(self.lease_dir),
            "resolved_path": None,
            "lstat": None,
            "opened": None,
            "errors": [],
        }
        try:
            before = self.lease_dir.lstat()
        except OSError as exc:
            snapshot["errors"].append(f"lease directory lstat failed:{type(exc).__name__}")
            return snapshot
        snapshot["lstat"] = {
            "kind": "directory" if stat.S_ISDIR(before.st_mode) else self._entry_kind(self.lease_dir),
            "device": before.st_dev,
            "inode": before.st_ino,
            "mode": stat.S_IMODE(before.st_mode),
        }
        if not stat.S_ISDIR(before.st_mode):
            snapshot["errors"].append("lease directory path is not a no-follow directory")
            return snapshot
        try:
            snapshot["resolved_path"] = str(self.lease_dir.resolve(strict=True))
        except (OSError, RuntimeError) as exc:
            snapshot["errors"].append(f"lease directory resolve failed:{type(exc).__name__}")
            return snapshot
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = os.open(self.lease_dir, flags)
            opened = os.fstat(descriptor)
            snapshot["opened"] = {
                "kind": "directory" if stat.S_ISDIR(opened.st_mode) else "other",
                "device": opened.st_dev,
                "inode": opened.st_ino,
                "mode": stat.S_IMODE(opened.st_mode),
            }
            after = self.lease_dir.lstat()
            lstat_identity = (before.st_dev, before.st_ino, stat.S_IMODE(before.st_mode))
            opened_identity = (opened.st_dev, opened.st_ino, stat.S_IMODE(opened.st_mode))
            after_identity = (after.st_dev, after.st_ino, stat.S_IMODE(after.st_mode))
            if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(after.st_mode):
                snapshot["errors"].append("lease directory changed to a non-directory during review")
            elif lstat_identity != opened_identity or after_identity != opened_identity:
                snapshot["errors"].append("lease directory identity changed during review")
        except OSError as exc:
            snapshot["errors"].append(f"lease directory no-follow open failed:{type(exc).__name__}")
        finally:
            if descriptor is not None:
                os.close(descriptor)
        return snapshot

    @staticmethod
    def _directory_identity_from_snapshot(snapshot: dict[str, Any]) -> tuple[int, int, int] | None:
        opened = snapshot.get("opened")
        if not isinstance(opened, dict) or opened.get("kind") != "directory":
            return None
        if snapshot.get("errors"):
            return None
        values = (opened.get("device"), opened.get("inode"), opened.get("mode"))
        if not all(isinstance(value, int) for value in values):
            return None
        return values

    def _pin_lease_dir(self) -> tuple[int, tuple[int, int, int]]:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lease_dir, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(descriptor)
            raise CleanupError("lease directory is not a pinned directory")
        return descriptor, (metadata.st_dev, metadata.st_ino, stat.S_IMODE(metadata.st_mode))

    def _require_pinned_lease_dir(
        self,
        descriptor: int,
        identity: tuple[int, int, int],
    ) -> None:
        pinned = os.fstat(descriptor)
        pinned_identity = (pinned.st_dev, pinned.st_ino, stat.S_IMODE(pinned.st_mode))
        if not stat.S_ISDIR(pinned.st_mode) or pinned_identity != identity:
            raise CleanupError("pinned lease directory identity changed")
        try:
            public_lstat = self.lease_dir.lstat()
            public_resolved = self.lease_dir.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CleanupError("public lease directory no longer resolves safely") from exc
        public_lstat_identity = (
            public_lstat.st_dev,
            public_lstat.st_ino,
            stat.S_IMODE(public_lstat.st_mode),
        )
        if (
            not stat.S_ISDIR(public_lstat.st_mode)
            or public_lstat_identity != identity
            or public_resolved != self.lease_dir
        ):
            raise CleanupError("public lease directory no longer resolves to pinned directory")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            public_descriptor = os.open(self.lease_dir, flags)
        except OSError as exc:
            raise CleanupError("public lease directory no longer resolves without links") from exc
        try:
            public = os.fstat(public_descriptor)
            public_identity = (public.st_dev, public.st_ino, stat.S_IMODE(public.st_mode))
            if not stat.S_ISDIR(public.st_mode) or public_identity != identity:
                raise CleanupError("public lease directory no longer resolves to pinned directory")
        finally:
            os.close(public_descriptor)

    def _pinned_entry_snapshot(self, descriptor: int, path: Path) -> dict[str, Any]:
        if path.parent != self.lease_dir or path.name in {"", ".", ".."}:
            raise CleanupError("migration entry is outside pinned lease directory")
        snapshot: dict[str, Any] = {"path": str(path)}
        try:
            metadata = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            snapshot["kind"] = "missing"
            return snapshot
        for predicate, label in (
            (stat.S_ISREG, "regular"),
            (stat.S_ISLNK, "symlink"),
            (stat.S_ISDIR, "directory"),
            (stat.S_ISFIFO, "fifo"),
            (stat.S_ISSOCK, "socket"),
            (stat.S_ISCHR, "character-device"),
            (stat.S_ISBLK, "block-device"),
        ):
            if predicate(metadata.st_mode):
                snapshot["kind"] = label
                break
        else:
            snapshot["kind"] = "other"
        snapshot.update(
            {
                "device": metadata.st_dev,
                "inode": metadata.st_ino,
                "mode": stat.S_IMODE(metadata.st_mode),
                "size": metadata.st_size,
            }
        )
        if snapshot["kind"] == "regular":
            file_descriptor = os.open(
                path.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            try:
                opened = os.fstat(file_descriptor)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise CleanupError("pinned entry changed during content read")
                chunks = []
                while True:
                    chunk = os.read(file_descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                snapshot["digest"] = hashlib.sha256(b"".join(chunks)).hexdigest()
            finally:
                os.close(file_descriptor)
        return snapshot

    def _pinned_lease_json_manifest(self, descriptor: int) -> dict[str, dict[str, Any]]:
        names_before = sorted(name for name in os.listdir(descriptor) if name.endswith(".json"))
        manifest = {name: self._pinned_entry_snapshot(descriptor, self.lease_dir / name) for name in names_before}
        names_after = sorted(name for name in os.listdir(descriptor) if name.endswith(".json"))
        if names_after != names_before:
            raise CleanupError("lease directory JSON entry manifest changed during scan")
        return manifest

    def _lease_json_manifest(self) -> tuple[dict[str, dict[str, Any]], list[str]]:
        descriptor = None
        try:
            descriptor, identity = self._pin_lease_dir()
            self._require_pinned_lease_dir(descriptor, identity)
            manifest = self._pinned_lease_json_manifest(descriptor)
            self._require_pinned_lease_dir(descriptor, identity)
            if self._pinned_lease_json_manifest(descriptor) != manifest:
                raise CleanupError("lease directory JSON entry manifest changed during review")
            return manifest, []
        except (OSError, CleanupError) as exc:
            return {}, [f"lease directory JSON manifest failed:{type(exc).__name__}:{exc}"]
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read_pinned_regular(self, descriptor: int, path: Path) -> bytes:
        snapshot = self._pinned_entry_snapshot(descriptor, path)
        if snapshot.get("kind") != "regular":
            raise CleanupError(f"pinned lease evidence is not regular:{path}")
        file_descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        try:
            opened = os.fstat(file_descriptor)
            if (opened.st_dev, opened.st_ino) != (
                snapshot.get("device"),
                snapshot.get("inode"),
            ):
                raise CleanupError(f"pinned lease evidence changed during read:{path}")
            chunks = []
            while True:
                chunk = os.read(file_descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(file_descriptor)

    @staticmethod
    def _same_entry_evidence(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
        keys = ("kind", "device", "inode", "mode", "size", "digest")
        return all(actual.get(key) == expected.get(key) for key in keys)

    def _quarantine_pinned_entry(
        self,
        descriptor: int,
        directory_identity: tuple[int, int, int],
        path: Path,
        expected: dict[str, Any],
        *,
        role: str,
        json_manifest_before: dict[str, dict[str, Any]],
        json_manifest_after: dict[str, dict[str, Any]],
    ) -> Path:
        def rollback(reason: str) -> None:
            try:
                rename_noreplace(descriptor, quarantine.name, descriptor, path.name)
                os.fsync(descriptor)
            except OSError as exc:
                raise CleanupError(f"{role} quarantine could not be rolled back") from exc
            restored = self._pinned_entry_snapshot(descriptor, path)
            quarantine_after = self._pinned_entry_snapshot(descriptor, quarantine)
            if restored != expected or quarantine_after.get("kind") != "missing":
                raise CleanupError(f"{role} quarantine rollback did not restore exact reviewed source")
            raise CleanupError(reason)

        self._require_pinned_lease_dir(descriptor, directory_identity)
        actual = self._pinned_entry_snapshot(descriptor, path)
        if actual != expected or actual.get("kind") != "regular":
            raise CleanupError(f"{role} changed before quarantine")
        if self._pinned_lease_json_manifest(descriptor) != json_manifest_before:
            raise CleanupError(f"complete lease directory JSON entry manifest changed before {role} quarantine")
        quarantine = self.lease_dir / (f".lease-quarantine-{role}-{secrets.token_hex(16)}.evidence")
        rename_noreplace(descriptor, path.name, descriptor, quarantine.name)
        moved = self._pinned_entry_snapshot(descriptor, quarantine)
        if not self._same_entry_evidence(moved, expected):
            rollback(f"foreign {role} moved at quarantine boundary")
        if self._pinned_lease_json_manifest(descriptor) != json_manifest_after:
            rollback(f"complete lease directory JSON entry manifest changed at {role} quarantine boundary")
        os.fsync(descriptor)
        self._require_pinned_lease_dir(descriptor, directory_identity)
        return quarantine

    def _file_digest(self, path: Path) -> str | None:
        raw, errors = self._read_regular_bytes(path)
        return hashlib.sha256(raw).hexdigest() if raw is not None and not errors else None

    @staticmethod
    def _registration_snapshot(worktrees: Sequence[Worktree]) -> list[dict[str, Any]]:
        return [
            {
                "path": str(worktree.path),
                "head": worktree.head,
                "branch": worktree.branch,
                "branch_ref": worktree.branch_ref,
                "detached": worktree.detached,
                "locked": worktree.locked,
                "prunable": worktree.prunable,
            }
            for worktree in sorted(
                worktrees,
                key=lambda worktree: (
                    str(worktree.path),
                    worktree.head,
                    worktree.branch_ref,
                    worktree.detached,
                    worktree.locked,
                    worktree.prunable,
                ),
            )
        ]

    @staticmethod
    def _process_snapshot(scan: ProcessScan) -> dict[str, Any]:
        return {
            "complete": scan.complete,
            "uses": [
                {"pid": use.pid, "reasons": list(use.reasons)} for use in sorted(scan.uses, key=lambda use: use.pid)
            ],
            "errors": list(scan.errors),
        }

    def _require_transition_context(
        self,
        *,
        path: Path,
        reviewed: Plan,
        roles_ended: bool,
    ) -> None:
        worktrees = self.worktrees()
        main = self.shared_main(worktrees)
        boundary = self.boundary(worktrees)
        matches = [worktree for worktree in worktrees if worktree.path == path]
        current_process = self._process_snapshot(self.process_scanner.scan(path))
        failures = []
        if str(main.path) != reviewed.repository:
            failures.append("shared main changed during migration")
        if str(boundary) != reviewed.boundary:
            failures.append("canonical boundary changed during migration")
        if self._registration_snapshot(matches) != reviewed.registration_snapshot:
            failures.append("worktree registration changed during migration")
        if path.exists() != reviewed.path_exists:
            failures.append("candidate existence changed during migration")
        if roles_ended != reviewed.roles_ended_asserted:
            failures.append("authoritative role assertion changed during migration")
        if self.actor != reviewed.actor:
            failures.append("migration actor changed during migration")
        if current_process != reviewed.migration_process_snapshot:
            failures.append("process visibility or use changed during migration")
        origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
        if origin_main != reviewed.origin_main:
            failures.append("origin/main changed during migration")
        if failures:
            raise CleanupError(";".join(failures))

    def _require_entry(
        self,
        path: Path,
        expected: dict[str, Any],
        *,
        label: str,
    ) -> None:
        actual = self._entry_snapshot(path)
        if actual != expected:
            raise CleanupError(f"{label} changed during migration")

    def _require_same_regular_identity(self, first: Path, second: Path, *, label: str) -> None:
        first_snapshot = self._entry_snapshot(first)
        second_snapshot = self._entry_snapshot(second)
        if (
            first_snapshot.get("kind") != "regular"
            or second_snapshot.get("kind") != "regular"
            or first_snapshot.get("device") != second_snapshot.get("device")
            or first_snapshot.get("inode") != second_snapshot.get("inode")
            or first_snapshot.get("digest") != second_snapshot.get("digest")
        ):
            raise CleanupError(f"{label} do not share exact regular-file identity")

    @staticmethod
    def _migration_payload_valid(
        payload: Any,
        *,
        source: dict[str, Any],
        canonical_path: Path,
        source_path: str,
        source_key: str,
        actor: str,
    ) -> bool:
        if not isinstance(payload, dict) or payload.get("path") != str(canonical_path):
            return False
        migration = payload.get("path_migration")
        if not isinstance(migration, dict):
            return False
        if (
            migration.get("from_path") != source_path
            or migration.get("from_key") != source_key
            or migration.get("actor") != actor
            or not isinstance(migration.get("at"), str)
            or not migration.get("at")
        ):
            return False
        preserved = dict(payload)
        preserved.pop("path_migration", None)
        preserved["path"] = source_path
        return preserved == source

    def reconcile_lease_plan(self, *, path: Path, roles_ended: bool = False) -> Plan:
        path = canonical(path)
        lease_directory_snapshot = self._lease_directory_snapshot()
        lease_json_manifest, lease_json_manifest_errors = self._lease_json_manifest()
        worktrees = self.worktrees()
        main = self.shared_main(worktrees)
        boundary = self.boundary(worktrees)
        origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
        registered_matches = [worktree for worktree in worktrees if worktree.path == path]
        registration_snapshot = self._registration_snapshot(registered_matches)
        registered = len(registered_matches) == 1
        process = self.process_scanner.scan(path)
        process_snapshot = self._process_snapshot(process)
        errors: list[str] = []
        reasons: list[str] = []
        lookup = self._lookup_lease(
            path,
            worktrees=worktrees,
            main=main,
            boundary=boundary,
            allow_migration_artifacts=True,
        )
        target, new_record, staged_source = self._migration_paths(path)
        retained_source = self._retained_source_path(path)
        target_exists = self._entry_exists(target)
        new_record_exists = self._entry_exists(new_record)
        staged_source_exists = self._entry_exists(staged_source)
        retained_source_exists = self._entry_exists(retained_source)
        source_file = lookup.source_file
        source_key = lookup.source_key
        source_path = lookup.stored_path
        lease = lookup.lease
        lookup_source = lookup.source
        recovery_stage = "none"
        expected_alias = main.path / ".claude" / "worktrees" / path.name
        expected_alias_key = lease_key(expected_alias)
        expected_alias_file = self.lease_dir / f"{expected_alias_key}.json"

        if lease_directory_snapshot["errors"]:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.extend(lease_directory_snapshot["errors"])
        if lease_json_manifest_errors:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.extend(lease_json_manifest_errors)

        if path.parent != boundary or path == boundary or path == main.path:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.append("reconciliation path must be one direct child of canonical boundary")

        if (
            target_exists
            and new_record_exists
            and not staged_source_exists
            and not retained_source_exists
            and self._entry_exists(expected_alias_file)
        ):
            source_lease, _, source_digest, source_errors = self._read_lease_file(
                expected_alias_file,
                expected_path=str(expected_alias),
            )
            target_lease, target_raw, _, target_errors = self._read_lease_file(
                target,
                expected_path=str(path),
            )
            new_raw, new_errors = self._read_regular_bytes(new_record)
            try:
                new_lease = json.loads(new_raw) if new_raw is not None and not new_errors else None
            except json.JSONDecodeError as exc:
                new_lease = None
                new_errors.append(f"lease unreadable:{type(exc).__name__}:{new_record}")
            target_snapshot = self._entry_snapshot(target)
            new_snapshot = self._entry_snapshot(new_record)
            exact_collision_sources = {
                str(target),
                str(expected_alias_file),
            }
            observed_collision_sources = {source for source, _ in lookup.evidence_sources}
            link_first_valid = (
                not source_errors
                and source_lease is not None
                and not target_errors
                and target_lease is not None
                and not new_errors
                and new_lease is not None
                and target_raw == new_raw
                and self._same_entry_evidence(target_snapshot, new_snapshot)
                and observed_collision_sources == exact_collision_sources
                and self._migration_payload_valid(
                    target_lease,
                    source=source_lease,
                    canonical_path=path,
                    source_path=str(expected_alias),
                    source_key=expected_alias_key,
                    actor=self.actor,
                )
            )
            if link_first_valid:
                lease = source_lease
                source_file = expected_alias_file
                source_path = str(expected_alias)
                source_key = expected_alias_key
                lookup_source = "legacy-alias-recovery"
                recovery_stage = "canonical-linked-before-source-staging"
                lookup = LeaseLookup(
                    source_lease,
                    (),
                    lookup_source,
                    expected_alias_file,
                    expected_alias_key,
                    str(expected_alias),
                    path,
                    lease_key(path),
                    source_digest,
                    ((str(expected_alias_file), expected_alias_key),),
                )

        recovery_source = staged_source if staged_source_exists else retained_source
        if staged_source_exists or retained_source_exists:
            recovery_stage = "source-staged"
            staged_lease, _, staged_digest, staged_errors = self._read_lease_file(
                recovery_source,
                expected_path=str(expected_alias),
            )
            if staged_errors or staged_lease is None:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.extend(staged_errors or ["staged migration source is invalid"])
            else:
                lease = staged_lease
                source_file = recovery_source
                source_path = str(expected_alias)
                source_key = lease_key(expected_alias)
                lookup_source = "legacy-alias-recovery"
                lookup = LeaseLookup(
                    lease,
                    (),
                    lookup_source,
                    recovery_source,
                    source_key,
                    source_path,
                    path,
                    lease_key(path),
                    staged_digest,
                    ((str(recovery_source), source_key),),
                )

        if staged_source_exists and retained_source_exists:
            staged_snapshot = self._entry_snapshot(staged_source)
            retained_snapshot = self._entry_snapshot(retained_source)
            if (
                staged_snapshot.get("kind") != "regular"
                or retained_snapshot.get("kind") != "regular"
                or staged_snapshot.get("device") != retained_snapshot.get("device")
                or staged_snapshot.get("inode") != retained_snapshot.get("inode")
                or staged_snapshot.get("digest") != retained_snapshot.get("digest")
            ):
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("staged and retained source identities differ")

        if (
            target_exists
            and not (staged_source_exists or retained_source_exists)
            and recovery_stage != "canonical-linked-before-source-staging"
        ):
            if lookup.source == "canonical" and not new_record_exists:
                reasons.append(LEASE_MIGRATION_NOT_REQUIRED)
            else:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("canonical destination exists without recoverable migration provenance")

        if lease is None and not reasons:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.extend(lookup.errors)
        elif lookup_source not in {"legacy-alias", "legacy-alias-recovery", "canonical"} and not reasons:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.extend(lookup.errors or ("legacy alias evidence is unavailable",))

        new_digest = self._file_digest(new_record) if new_record_exists else None
        staged_digest = self._file_digest(staged_source) if staged_source_exists else None
        legacy_source_file = self.lease_dir / f"{source_key}.json" if source_key is not None else None
        legacy_source_exists = bool(legacy_source_file and self._entry_exists(legacy_source_file))
        if staged_source_exists and legacy_source_exists:
            legacy_digest = self._file_digest(legacy_source_file)
            if legacy_digest != staged_digest:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("staged and authoritative legacy source bytes differ")
            elif self._entry_snapshot(legacy_source_file).get("inode") != self._entry_snapshot(staged_source).get(
                "inode"
            ):
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("staged and authoritative legacy source identities differ")
        if new_record_exists:
            new_raw, new_errors = self._read_regular_bytes(new_record)
            try:
                new_payload = json.loads(new_raw) if new_raw is not None and not new_errors else None
            except json.JSONDecodeError as exc:
                new_payload = None
                errors.append(f"migration new record unreadable:{type(exc).__name__}")
            errors.extend(new_errors)
            if (
                lease is None
                or source_path is None
                or source_key is None
                or not self._migration_payload_valid(
                    new_payload,
                    source=lease,
                    canonical_path=path,
                    source_path=source_path,
                    source_key=source_key,
                    actor=self.actor,
                )
            ):
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append(
                    "migration new record does not preserve exact lifecycle meaning or current attributable actor"
                )
            elif recovery_stage == "none":
                recovery_stage = "new-record-written"

        if (staged_source_exists or retained_source_exists) and not new_record_exists and not target_exists:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.append("staged migration source has no recoverable canonical payload")

        terminal = lease.get("terminal", {}) if lease else {}
        evidence_valid = False
        merge_on_main = False
        merge_on_run = False
        if lease and lease.get("state") == "active":
            if not roles_ended:
                reasons.append(RETAIN_ACTIVE_LIFECYCLE)
                errors.append("active lease migration requires authoritative --roles-ended assertion")
        elif lease and lease.get("state") == "terminal":
            evidence_valid, evidence_error, _ = self._validate_run(lease)
            merge_on_main, merge_main_error = self._is_ancestor(str(terminal.get("merge_sha", "")), origin_main)
            merge_on_run, merge_run_error = self._is_ancestor(
                str(terminal.get("merge_sha", "")), str(terminal.get("run_head_sha", ""))
            )
            if not evidence_valid or not merge_on_main or not merge_on_run:
                reasons.append(RETAIN_TERMINAL_EVIDENCE_MISSING)
                errors.extend(
                    item
                    for item in (evidence_error, merge_main_error, merge_run_error)
                    if item and item != "not ancestor"
                )

        if target_exists and (staged_source_exists or retained_source_exists):
            recovery_stage = "canonical-linked"
            canonical_lease, _, _, canonical_errors = self._read_lease_file(
                target,
                expected_path=str(path),
            )
            if (
                canonical_errors
                or canonical_lease is None
                or lease is None
                or source_path is None
                or source_key is None
                or not self._migration_payload_valid(
                    canonical_lease,
                    source=lease,
                    canonical_path=path,
                    source_path=source_path,
                    source_key=source_key,
                    actor=self.actor,
                )
            ):
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.extend(
                    canonical_errors
                    or ["canonical migration record provenance is not recoverable by current attributable actor"]
                )
            if new_record_exists and self._file_digest(target) != new_digest:
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("canonical and migration-new payload bytes differ")

        if not process.complete:
            reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
            errors.extend(process.errors or ("process visibility is incomplete",))
        elif process.uses:
            reasons.append(RETAIN_ACTIVE_PROCESS)
            errors.append("candidate has active process use during reconciliation")

        if not reasons:
            reasons.append(ELIGIBLE_LEASE_MIGRATION)
        reasons = list(dict.fromkeys(reasons))
        errors = list(dict.fromkeys(errors))
        classification = reasons[0]
        action = f"migrate lifecycle lease {source_path or source_file} -> {path}"
        migration_entries = {
            "source": (
                self._entry_snapshot(source_file) if source_file is not None else {"path": None, "kind": "missing"}
            ),
            "legacy_source": (
                self._entry_snapshot(legacy_source_file)
                if legacy_source_file is not None
                else {"path": None, "kind": "missing"}
            ),
            "target": self._entry_snapshot(target),
            "new_record": self._entry_snapshot(new_record),
            "staged_source": self._entry_snapshot(staged_source),
            "retained_source": self._entry_snapshot(retained_source),
        }
        facts = {
            "mode": "reconcile-lease",
            "actor": self.actor,
            "repository": str(main.path),
            "common_dir": str(self.common_dir),
            "boundary": str(boundary),
            "path": str(path),
            "origin_main": origin_main,
            "lease_directory_snapshot": lease_directory_snapshot,
            "lease_json_manifest": lease_json_manifest,
            "registered_count": len(registered_matches),
            "registration_snapshot": registration_snapshot,
            "path_exists": path.exists(),
            "process": process_snapshot,
            "lookup": lookup.facts(),
            "lease": lease,
            "roles_ended_asserted": roles_ended,
            "terminal_evidence_present": isinstance(terminal, dict) and bool(terminal),
            "evidence_valid": evidence_valid,
            "merge_on_main": merge_on_main,
            "merge_on_run": merge_on_run,
            "migration_entries": migration_entries,
            "target_exists": target_exists,
            "new_record_exists": new_record_exists,
            "new_record_digest": new_digest,
            "staged_source_exists": staged_source_exists,
            "staged_source_digest": staged_digest,
            "retained_source_exists": retained_source_exists,
            "legacy_source_exists": legacy_source_exists,
            "recovery_stage": recovery_stage,
            "classification": classification,
            "reasons": reasons,
            "errors": errors,
        }
        return Plan(
            timestamp=self.now(),
            actor=self.actor,
            mode="reconcile-lease",
            repository=str(main.path),
            common_dir=str(self.common_dir),
            path=str(path),
            issue=lease.get("issue") if lease else None,
            branch=registered_matches[0].branch if registered else None,
            detached=registered_matches[0].detached if registered else False,
            head=registered_matches[0].head if registered else "",
            origin_main=origin_main,
            lease_state=lease.get("state", "missing-or-invalid") if lease else "missing-or-invalid",
            terminal_run_id=str(terminal.get("run_id")) if terminal.get("run_id") is not None else None,
            terminal_run_head=terminal.get("run_head_sha"),
            terminal_result=terminal.get("result"),
            process_ids=[use.pid for use in process.uses],
            process_reasons=[reason for use in process.uses for reason in use.reasons],
            classification=classification,
            reasons=reasons,
            requested_actions=[action] if classification == ELIGIBLE_LEASE_MIGRATION else [],
            errors=errors,
            facts=facts,
            boundary=str(boundary),
            lease_lookup_source=lookup_source,
            lease_source_file=str(source_file) if source_file else None,
            lease_stored_path=source_path,
            lease_source_key=source_key,
            lease_canonical_path=str(path),
            lease_canonical_key=lease_key(path),
            lease_content_digest=lookup.content_digest,
            lease_evidence_sources=lookup.facts()["evidence_sources"],
            registration_snapshot=registration_snapshot,
            migration_entries=migration_entries,
            lease_json_manifest=lease_json_manifest,
            migration_process_snapshot=process_snapshot,
            lease_directory_snapshot=lease_directory_snapshot,
            registered=registered,
            path_exists=path.exists(),
            roles_ended_asserted=roles_ended,
            terminal_evidence_present=isinstance(terminal, dict) and bool(terminal),
            lease_actor=lease.get("actor") if lease else None,
            lease_role=lease.get("role") if lease else None,
            lease_created_at=lease.get("created_at") if lease else None,
            lease_updated_at=lease.get("updated_at") if lease else None,
        ).seal()

    def _fsync_lease_dir(self) -> None:
        descriptor = os.open(self.lease_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _migrate_lease_v7(
        self,
        *,
        path: Path,
        plan_digest: str,
        roles_ended: bool,
    ) -> Plan:
        directory, directory_identity = self._pin_lease_dir()
        try:
            plan = self.reconcile_lease_plan(path=path, roles_ended=roles_ended)
            self._require_pinned_lease_dir(directory, directory_identity)
            reviewed_directory_identity = self._directory_identity_from_snapshot(plan.lease_directory_snapshot)
            if reviewed_directory_identity != directory_identity:
                plan.exit_status = 2
                plan.errors.append("reviewed lease directory identity does not match pinned apply directory")
                return plan
            if plan.plan_digest != plan_digest:
                plan.exit_status = 2
                plan.errors.append("reviewed reconciliation digest does not match recomputed facts")
                return plan
            if plan.classification != ELIGIBLE_LEASE_MIGRATION:
                plan.exit_status = 2
                plan.errors.append("lease is not eligible for one-record path migration")
                return plan

            target, new_record, staged_source = self._migration_paths(path)
            retained_source = self._retained_source_path(path)
            if plan.lease_source_file is None:
                plan.exit_status = 2
                plan.errors.append("migration source is unavailable")
                return plan
            source = Path(plan.lease_source_file)
            legacy_source = (
                self.lease_dir / f"{plan.lease_source_key}.json" if plan.lease_source_key is not None else None
            )
            expected_source = plan.migration_entries["source"]
            expected_legacy = plan.migration_entries["legacy_source"]
            expected_target = plan.migration_entries["target"]
            expected_new = plan.migration_entries["new_record"]
            expected_staged = plan.migration_entries["staged_source"]
            expected_retained = plan.migration_entries["retained_source"]
            expected_json_manifest = dict(plan.lease_json_manifest)
            quarantines: list[Path] = []

            def require_entry(entry: Path, expected: dict[str, Any], label: str) -> None:
                if self._pinned_entry_snapshot(directory, entry) != expected:
                    raise CleanupError(f"{label} changed during pinned migration")

            def require_all_expected_entries() -> None:
                self._require_pinned_lease_dir(directory, directory_identity)
                self._require_transition_context(
                    path=path,
                    reviewed=plan,
                    roles_ended=roles_ended,
                )
                require_entry(source, expected_source, "authoritative source")
                if legacy_source is not None:
                    require_entry(legacy_source, expected_legacy, "legacy source")
                require_entry(target, expected_target, "canonical target")
                require_entry(new_record, expected_new, "migration-new record")
                require_entry(staged_source, expected_staged, "staged source")
                require_entry(retained_source, expected_retained, "retained source")
                if self._pinned_lease_json_manifest(directory) != expected_json_manifest:
                    raise CleanupError("complete lease directory JSON entry manifest changed during migration")

            require_all_expected_entries()
            source_raw = self._read_pinned_regular(directory, source)
            if hashlib.sha256(source_raw).hexdigest() != plan.lease_content_digest:
                raise CleanupError("migration source digest changed after review")
            lease = json.loads(source_raw)

            if expected_new.get("kind") == "missing" and expected_target.get("kind") == "missing":
                migrated = dict(lease)
                migrated["path"] = str(path)
                migration_time = self.now()
                migrated["path_migration"] = {
                    "from_path": lease["path"],
                    "from_key": plan.lease_source_key,
                    "actor": self.actor,
                    "at": migration_time,
                }
                payload = (json.dumps(migrated, sort_keys=True, indent=2) + "\n").encode()
                file_descriptor = os.open(
                    new_record.name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory,
                )
                try:
                    remaining = memoryview(payload)
                    while remaining:
                        written = os.write(file_descriptor, remaining)
                        if written <= 0:
                            raise OSError("short migration-new write")
                        remaining = remaining[written:]
                    os.fsync(file_descriptor)
                    metadata = os.fstat(file_descriptor)
                    captured_new = {
                        "path": str(new_record),
                        "kind": "regular",
                        "device": metadata.st_dev,
                        "inode": metadata.st_ino,
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "size": metadata.st_size,
                        "digest": hashlib.sha256(payload).hexdigest(),
                    }
                    if metadata.st_size != len(payload):
                        raise CleanupError("migration-new fd size differs from exact payload")
                    self.migration_hook("new-record-file-synced")
                    self._require_pinned_lease_dir(directory, directory_identity)
                    if self._pinned_entry_snapshot(directory, new_record) != captured_new:
                        raise CleanupError("migration-new path changed after file fsync")
                    if self._read_pinned_regular(directory, new_record) != payload:
                        raise CleanupError("migration-new bytes changed after file fsync")
                    written_payload = json.loads(payload)
                    provenance = written_payload.get("path_migration", {})
                    if provenance.get("actor") != self.actor or provenance.get("at") != migration_time:
                        raise CleanupError("migration-new provenance differs from created fd")
                    os.fsync(directory)
                    self._require_pinned_lease_dir(directory, directory_identity)
                    if self._pinned_entry_snapshot(directory, new_record) != captured_new:
                        raise CleanupError("migration-new path changed during directory fsync")
                    expected_new = captured_new
                finally:
                    os.close(file_descriptor)
                self.migration_hook("new-record-written")
                require_all_expected_entries()

            new_payload = None
            if expected_new.get("kind") == "regular":
                new_payload = json.loads(self._read_pinned_regular(directory, new_record))
                if not self._migration_payload_valid(
                    new_payload,
                    source=lease,
                    canonical_path=path,
                    source_path=lease["path"],
                    source_key=plan.lease_source_key or "",
                    actor=self.actor,
                ):
                    raise CleanupError("migration-new provenance is not exact")

            # Establish an exact hardlink to the reviewed source before publishing
            # the canonical record. A crash at canonical publication therefore
            # always leaves an independently discoverable recovery source.
            if expected_staged.get("kind") == "missing":
                require_all_expected_entries()
                os.link(
                    source.name,
                    staged_source.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
                expected_staged = self._pinned_entry_snapshot(directory, staged_source)
                if not self._same_entry_evidence(expected_staged, expected_source):
                    raise CleanupError("staged source is not the exact authoritative inode")
                self.migration_hook("staged-source-linked")
                require_all_expected_entries()

            if expected_target.get("kind") == "missing":
                require_all_expected_entries()
                if expected_new.get("kind") != "regular":
                    raise CleanupError("canonical target has no exact migration-new source")
                os.link(
                    new_record.name,
                    target.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
                os.fsync(directory)
                expected_target = self._pinned_entry_snapshot(directory, target)
                if not self._same_entry_evidence(expected_target, expected_new):
                    raise CleanupError("canonical target is not the exact migration-new inode")
                expected_json_manifest[target.name] = expected_target
                self.migration_hook("canonical-published")
                require_all_expected_entries()
            target_payload = json.loads(self._read_pinned_regular(directory, target))
            if not self._migration_payload_valid(
                target_payload,
                source=lease,
                canonical_path=path,
                source_path=lease["path"],
                source_key=plan.lease_source_key or "",
                actor=self.actor,
            ):
                raise CleanupError("canonical target does not preserve reviewed provenance")

            self.migration_hook("source-staging-linked")
            require_all_expected_entries()
            if legacy_source is not None and expected_legacy.get("kind") == "regular":
                post_quarantine_json_manifest = dict(expected_json_manifest)
                post_quarantine_json_manifest.pop(legacy_source.name, None)
                quarantined = self._quarantine_pinned_entry(
                    directory,
                    directory_identity,
                    legacy_source,
                    expected_legacy,
                    role="legacy-source",
                    json_manifest_before=expected_json_manifest,
                    json_manifest_after=post_quarantine_json_manifest,
                )
                quarantines.append(quarantined)
                expected_legacy = self._pinned_entry_snapshot(directory, legacy_source)
                expected_json_manifest = post_quarantine_json_manifest
                if source == legacy_source:
                    source = quarantined
                    expected_source = self._pinned_entry_snapshot(directory, quarantined)

            self.migration_hook("source-staged")
            require_all_expected_entries()
            self.migration_hook("canonical-linked")
            require_all_expected_entries()

            if expected_new.get("kind") == "regular":
                require_all_expected_entries()
                quarantined = self._quarantine_pinned_entry(
                    directory,
                    directory_identity,
                    new_record,
                    expected_new,
                    role="migration-new",
                    json_manifest_before=expected_json_manifest,
                    json_manifest_after=expected_json_manifest,
                )
                quarantines.append(quarantined)
                expected_new = self._pinned_entry_snapshot(directory, new_record)
            self.migration_hook("new-record-removed")
            require_all_expected_entries()

            self.migration_hook("source-removed")
            require_all_expected_entries()
            if expected_staged.get("kind") == "regular":
                require_all_expected_entries()
                quarantined = self._quarantine_pinned_entry(
                    directory,
                    directory_identity,
                    staged_source,
                    expected_staged,
                    role="staged-source",
                    json_manifest_before=expected_json_manifest,
                    json_manifest_after=expected_json_manifest,
                )
                quarantines.append(quarantined)
                expected_staged = self._pinned_entry_snapshot(directory, staged_source)
                if source == staged_source:
                    source = quarantined
                    expected_source = self._pinned_entry_snapshot(directory, quarantined)
            if expected_retained.get("kind") == "regular":
                require_all_expected_entries()
                quarantined = self._quarantine_pinned_entry(
                    directory,
                    directory_identity,
                    retained_source,
                    expected_retained,
                    role="retained-source",
                    json_manifest_before=expected_json_manifest,
                    json_manifest_after=expected_json_manifest,
                )
                quarantines.append(quarantined)
                expected_retained = self._pinned_entry_snapshot(directory, retained_source)
                if source == retained_source:
                    source = quarantined
                    expected_source = self._pinned_entry_snapshot(directory, quarantined)

            self.migration_hook("source-quarantined")
            require_all_expected_entries()
            self.migration_hook("final-proof")
            require_all_expected_entries()
            for entry, expected, label in (
                (legacy_source, expected_legacy, "legacy source"),
                (new_record, expected_new, "migration-new record"),
                (staged_source, expected_staged, "staged source"),
                (retained_source, expected_retained, "retained source"),
            ):
                if entry is not None and expected.get("kind") != "missing":
                    raise CleanupError(f"{label} remains authoritative at final proof")
            require_entry(target, expected_target, "canonical target")
            final_raw = self._read_pinned_regular(directory, target)
            final_payload = json.loads(final_raw)
            if not self._migration_payload_valid(
                final_payload,
                source=lease,
                canonical_path=path,
                source_path=lease["path"],
                source_key=plan.lease_source_key or "",
                actor=self.actor,
            ):
                raise CleanupError("final canonical provenance differs from reviewed source")
            pinned_json = {
                name
                for name in os.listdir(directory)
                if name.endswith(".json") and name in {target.name, legacy_source.name if legacy_source else ""}
            }
            if pinned_json != {target.name}:
                raise CleanupError("pinned final scan found canonical/legacy collision")
            self._require_pinned_lease_dir(directory, directory_identity)
            final_lookup = self._lookup_lease(path, allow_migration_artifacts=True)
            self._require_pinned_lease_dir(directory, directory_identity)
            require_all_expected_entries()
            if (
                final_lookup.source != "canonical"
                or final_lookup.errors
                or final_lookup.content_digest != hashlib.sha256(final_raw).hexdigest()
            ):
                raise CleanupError("public final scan is not one exact canonical record")
            if not quarantines:
                raise CleanupError("migration did not retain reviewed recovery evidence")
            plan.completed_actions.append(plan.requested_actions[0])
            return plan
        except (OSError, CleanupError, KeyError, json.JSONDecodeError) as exc:
            plan = locals().get("plan")
            if plan is None:
                raise
            plan.exit_status = 1
            plan.errors.append(f"lease migration interrupted:{type(exc).__name__}:{exc}")
            return plan
        finally:
            os.close(directory)

    def migrate_lease(self, *, path: Path, plan_digest: str, roles_ended: bool = False) -> Plan:
        return self._migrate_lease_v7(
            path=canonical(path),
            plan_digest=plan_digest,
            roles_ended=roles_ended,
        )

    def _status_dirty(self, path: Path) -> tuple[bool | None, str]:
        result = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all", cwd=path, check=False)
        if result.returncode:
            return None, _decode(result.stderr) or f"status exit {result.returncode}"
        return bool(result.stdout), ""

    def _candidate_plan(self, wt: Worktree, *, main: Worktree, boundary: Path, origin_main: str) -> Plan:
        path = wt.path
        reasons: list[str] = []
        errors: list[str] = []
        lookup = self._lookup_lease(path, worktrees=[main, wt], main=main, boundary=boundary)
        lease, lease_errors = lookup.lease, list(lookup.errors)
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
            elif lookup.source == "legacy-alias":
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("terminal legacy alias must be reconciled before cleanup classification")

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
            "boundary": str(boundary),
            "path": str(path),
            "issue": issue,
            "branch": wt.branch,
            "detached": wt.detached,
            "head": wt.head,
            "origin_main": origin_main,
            "lease": lease,
            "lease_lookup": lookup.facts(),
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
            boundary=str(boundary),
            lease_lookup_source=lookup.source,
            lease_source_file=str(lookup.source_file) if lookup.source_file else None,
            lease_stored_path=lookup.stored_path,
            lease_source_key=lookup.source_key,
            lease_canonical_path=str(path),
            lease_canonical_key=lookup.canonical_key,
            lease_content_digest=lookup.content_digest,
            lease_evidence_sources=lookup.facts()["evidence_sources"],
            registered=True,
            path_exists=path.exists(),
            terminal_evidence_present=isinstance(lease.get("terminal"), dict) if lease else False,
            lease_actor=lease.get("actor") if lease else None,
            lease_role=lease.get("role") if lease else None,
            lease_created_at=lease.get("created_at") if lease else None,
            lease_updated_at=lease.get("updated_at") if lease else None,
        ).seal()

    def classify(self) -> list[Plan]:
        worktrees = self.worktrees()
        main = self.shared_main(worktrees)
        boundary = self.boundary(worktrees)
        origin_main = _decode(self._git("rev-parse", "origin/main").stdout)
        plans = [self._candidate_plan(wt, main=main, boundary=boundary, origin_main=origin_main) for wt in worktrees]
        registered = {wt.path for wt in worktrees}
        try:
            entries = sorted(boundary.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise CleanupError("agent worktree boundary cannot be enumerated safely") from exc
        for entry in entries:
            path = canonical(entry)
            if path in registered:
                continue
            facts = {
                "mode": "remove",
                "boundary": str(boundary),
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
            lookup = self._lookup_lease(wt.path, worktrees=worktrees, main=main, boundary=boundary)
            lease, lease_errors = lookup.lease, list(lookup.errors)
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
            elif lookup.source == "legacy-alias":
                reasons.append(RETAIN_MISSING_OR_UNCLASSIFIED)
                errors.append("terminal legacy alias must be reconciled before metadata pruning")
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
                    errors.extend(
                        item for item in (evidence_error, main_error, run_error) if item and item != "not ancestor"
                    )
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
                "boundary": str(boundary),
                "path": str(wt.path),
                "head": wt.head,
                "branch": wt.branch,
                "locked": wt.locked,
                "prunable": wt.prunable,
                "origin_main": origin_main,
                "lease": lease,
                "lease_lookup": lookup.facts(),
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
                    boundary=str(boundary),
                    lease_lookup_source=lookup.source,
                    lease_source_file=str(lookup.source_file) if lookup.source_file else None,
                    lease_stored_path=lookup.stored_path,
                    lease_source_key=lookup.source_key,
                    lease_canonical_path=str(wt.path),
                    lease_canonical_key=lookup.canonical_key,
                    lease_content_digest=lookup.content_digest,
                    lease_evidence_sources=lookup.facts()["evidence_sources"],
                    registered=True,
                    path_exists=wt.path.exists(),
                    terminal_evidence_present=isinstance(lease.get("terminal"), dict) if lease else False,
                    lease_actor=lease.get("actor") if lease else None,
                    lease_role=lease.get("role") if lease else None,
                    lease_created_at=lease.get("created_at") if lease else None,
                    lease_updated_at=lease.get("updated_at") if lease else None,
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
            + ([f"git branch -d {plan.branch}" for plan in plans if plan.branch] if delete_merged_branches else []),
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
                    result["errors"].append(f"{plan.branch}: {_decode(deleted.stderr) or 'git branch -d refused'}")
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
    "boundary",
    "path",
    "issue",
    "branch",
    "detached",
    "head",
    "origin_main",
    "lease_state",
    "lease_lookup_source",
    "lease_source_file",
    "lease_stored_path",
    "lease_source_key",
    "lease_canonical_path",
    "lease_canonical_key",
    "lease_content_digest",
    "lease_evidence_sources",
    "registration_snapshot",
    "migration_entries",
    "lease_json_manifest",
    "migration_process_snapshot",
    "lease_directory_snapshot",
    "registered",
    "path_exists",
    "roles_ended_asserted",
    "terminal_evidence_present",
    "lease_actor",
    "lease_role",
    "lease_created_at",
    "lease_updated_at",
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

    reconcile = subparsers.add_parser(
        "lease-reconcile",
        help="Review or apply one historical-alias lease path migration",
    )
    reconcile.add_argument("--path", type=Path, required=True)
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--plan-digest")
    reconcile.add_argument(
        "--roles-ended",
        action="store_true",
        help="Assert the authoritative active-agent registry has no role able to use an active lease",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = CleanupService(args.repo, actor=args.actor)
    command = args.command or "classify"
    try:
        if command == "classify":
            records = [service.classify_path(args.path)] if getattr(args, "path", None) else service.classify()
            print(
                json.dumps([record.public_dict() for record in records], sort_keys=True)
                if args.json
                else render_human(records)
            )
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
        if command == "lease-reconcile":
            if not args.apply:
                result = service.reconcile_lease_plan(
                    path=args.path,
                    roles_ended=args.roles_ended,
                )
                print(json.dumps(result.public_dict(), sort_keys=True) if args.json else render_human([result]))
                return 0
            if not args.plan_digest:
                raise CleanupError("--plan-digest is required with --apply")
            result = service.migrate_lease(
                path=args.path,
                plan_digest=args.plan_digest,
                roles_ended=args.roles_ended,
            )
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
