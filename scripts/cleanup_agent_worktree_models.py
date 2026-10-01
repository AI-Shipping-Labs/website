"""Value models for the agent worktree lifecycle helper."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


class CleanupError(RuntimeError):
    """A fail-closed classification or lifecycle error."""


@dataclass(frozen=True)
class CommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int


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
        if not self.branch_ref.startswith(prefix):
            return None
        return self.branch_ref[len(prefix) :]


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
        source_file = None
        if self.source_file:
            source_file = str(self.source_file)
        return {
            "lookup_source": self.source,
            "source_file": source_file,
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
    terminal_issue_disposition: str | None = None
    terminal_issue_number: int | None = None
    terminal_issue_state: str | None = None
    terminal_issue_labels: list[str] | None = None
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
