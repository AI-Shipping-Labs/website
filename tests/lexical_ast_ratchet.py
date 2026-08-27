"""Rule-neutral primitives for deterministic lexical AST occurrence ratchets.

Rule modules are responsible for classification.  This module only parses and
discovers Python source, identifies lexical scopes, gives already-classified
AST anchors stable occurrence IDs, and compares those IDs with caller-owned
shrink-only manifests.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import tokenize
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence

CANONICAL_PAYLOAD_VERSION = 1
MODULE_SCOPE = "<module>"

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class FailureKind(StrEnum):
    NEW = "new"
    STALE = "stale"
    REPLACEMENT = "replacement"
    DUPLICATE_ID = "duplicate-id"
    OVERLAP = "overlap"
    MISSING_REASON = "missing-reason"
    CEILING_GROWTH = "ceiling-growth"
    GOLDEN_DRIFT = "golden-drift"
    FINGERPRINT_COLLISION = "fingerprint-collision"
    PARSE = "parse"
    DISCOVERY = "discovery"
    ORDER = "order"


@dataclass(frozen=True, order=True)
class RatchetFailure:
    kind: FailureKind
    message: str
    category: str = ""
    occurrence_id: str = ""

    def diagnostic(self) -> str:
        location = f" [{self.category}]" if self.category else ""
        identity = f" {self.occurrence_id}" if self.occurrence_id else ""
        return f"{self.kind.value}{location}:{identity} {self.message}"


class LexicalRatchetError(ValueError):
    """A deterministic parse, discovery, identity, or ordering failure."""

    kind: FailureKind

    def __init__(self, kind: FailureKind, message: str):
        self.failure = RatchetFailure(kind=kind, message=message)
        super().__init__(self.failure.diagnostic())


class SourceParseError(LexicalRatchetError):
    def __init__(self, path: str, error: SyntaxError):
        line = error.lineno or 0
        column = error.offset or 0
        detail = error.msg or "invalid syntax"
        super().__init__(FailureKind.PARSE, f"{path}:{line}:{column}: {detail}")
        self.path = path
        self.line = line
        self.column = column


class SourceDiscoveryError(LexicalRatchetError):
    def __init__(self, message: str):
        super().__init__(FailureKind.DISCOVERY, message)


class OccurrenceOrderError(LexicalRatchetError):
    def __init__(self, message: str):
        super().__init__(FailureKind.ORDER, message)


class FingerprintCollisionError(LexicalRatchetError):
    def __init__(self, fingerprint: str):
        super().__init__(
            FailureKind.FINGERPRINT_COLLISION,
            f"SHA-256 {fingerprint} identifies different canonical payloads",
        )


@dataclass(frozen=True)
class ParsedSource:
    path: str
    source: str
    tree: ast.Module


@dataclass(frozen=True)
class AstCandidate:
    """One rule-classified anchor before hashing and ordinal assignment."""

    path: str
    lexical_scope: str
    rule: str
    classification: str
    anchor: ast.AST
    metadata: Mapping[str, JsonValue]
    line: int


@dataclass(frozen=True, order=True)
class AstOccurrence:
    occurrence_id: str
    path: str
    lexical_scope: str
    rule: str
    classification: str
    fingerprint: str
    duplicate_ordinal: int
    line: int
    canonical_payload: str

    def diagnostic(self) -> str:
        return f"{self.path}:{self.line}: {self.occurrence_id} ({self.classification})"


@dataclass(frozen=True)
class RatchetCategory:
    """A caller-owned live allowance and its independently reviewed ceiling."""

    name: str
    rule: str
    classification: str
    live: Mapping[str, str]
    ceiling: Sequence[str]
    ceiling_golden_sha256: str


@dataclass(frozen=True)
class RatchetReport:
    failures: tuple[RatchetFailure, ...]

    @property
    def passed(self) -> bool:
        return not self.failures

    def failures_of_kind(self, kind: FailureKind) -> tuple[RatchetFailure, ...]:
        return tuple(failure for failure in self.failures if failure.kind == kind)

    def diagnostic(self) -> str:
        return "\n".join(failure.diagnostic() for failure in self.failures)


def normalize_repo_path(path: str | PurePosixPath) -> str:
    """Validate and return one normalized repository-relative POSIX path."""

    raw = str(path)
    normalized = PurePosixPath(raw)
    if (
        not raw
        or "\\" in raw
        or normalized.is_absolute()
        or raw != normalized.as_posix()
        or normalized.as_posix() == "."
        or ".." in normalized.parts
        or "::" in raw
    ):
        raise ValueError(f"source path must be normalized and repository-relative: {raw!r}")
    return raw


def parse_source(path: str, source: str) -> ParsedSource:
    """Parse source without importing or executing it."""

    normalized_path = normalize_repo_path(path)
    try:
        tree = ast.parse(source, filename=normalized_path)
    except SyntaxError as error:
        raise SourceParseError(normalized_path, error) from error
    return ParsedSource(path=normalized_path, source=source, tree=tree)


def parse_file(repo_root: Path, path: str) -> ParsedSource:
    """Read and parse one discovered file using its declared Python encoding."""

    normalized_path = normalize_repo_path(path)
    root = repo_root.resolve(strict=True)
    source_path = (root / normalized_path).resolve(strict=True)
    if not source_path.is_relative_to(root) or not source_path.is_file():
        raise SourceDiscoveryError(f"source is not a repository file: {normalized_path}")
    try:
        with tokenize.open(source_path) as source_file:
            source = source_file.read()
    except (OSError, UnicodeError, SyntaxError) as error:
        raise SourceDiscoveryError(f"cannot read {normalized_path}: {error}") from error
    return parse_source(normalized_path, source)


def _walk_python_root(repo_root: Path, source_root: Path) -> set[str]:
    if source_root.is_file():
        candidates = (source_root,)
    else:
        candidates = source_root.rglob("*.py")

    discovered: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise SourceDiscoveryError(f"source changed during discovery: {candidate}") from error
        if not resolved.is_relative_to(repo_root):
            raise SourceDiscoveryError(f"discovered source escapes repository: {candidate}")
        if resolved.is_file() and resolved.suffix == ".py":
            discovered.add(resolved.relative_to(repo_root).as_posix())
    return discovered


def _discover_once(repo_root: Path, roots: tuple[str | Path, ...]) -> set[str]:
    discovered: set[str] = set()
    for requested_root in roots:
        source_root = Path(requested_root)
        if not source_root.is_absolute():
            source_root = repo_root / source_root
        try:
            source_root = source_root.resolve(strict=True)
        except OSError as error:
            raise SourceDiscoveryError(f"source root does not exist: {requested_root}") from error
        if not source_root.is_relative_to(repo_root):
            raise SourceDiscoveryError(f"source root escapes repository: {requested_root}")
        if source_root.is_file() and source_root.suffix != ".py":
            raise SourceDiscoveryError(f"explicit source file is not Python: {requested_root}")
        discovered.update(_walk_python_root(repo_root, source_root))
    return discovered


def discover_python_sources(repo_root: Path, roots: Iterable[str | Path]) -> tuple[str, ...]:
    """Discover Python files below explicit roots and return a stable file list."""

    try:
        resolved_repo = repo_root.resolve(strict=True)
    except OSError as error:
        raise SourceDiscoveryError(f"repository root does not exist: {repo_root}") from error
    if not resolved_repo.is_dir():
        raise SourceDiscoveryError(f"repository root is not a directory: {repo_root}")

    requested_roots = tuple(roots)
    if not requested_roots:
        raise SourceDiscoveryError("at least one explicit source root is required")
    first = _discover_once(resolved_repo, requested_roots)
    second = _discover_once(resolved_repo, requested_roots)
    if first != second:
        added = sorted(second - first)
        removed = sorted(first - second)
        raise SourceDiscoveryError(
            f"source discovery changed while scanning; added={added!r}, removed={removed!r}"
        )
    return tuple(sorted(first))


def _nested_qualname(stack: tuple[tuple[str, str], ...], name: str) -> str:
    if not stack:
        return name
    parent_qualname, parent_kind = stack[-1]
    separator = ".<locals>." if parent_kind == "function" else "."
    return f"{parent_qualname}{separator}{name}"


class _LexicalScopeVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.stack: tuple[tuple[str, str], ...] = ()
        self.scopes: dict[int, str] = {}

    @property
    def current_scope(self) -> str:
        return self.stack[-1][0] if self.stack else MODULE_SCOPE

    def generic_visit(self, node: ast.AST) -> None:
        self.scopes[id(node)] = self.current_scope
        super().generic_visit(node)

    def _visit_named_scope(self, node: ast.AST, name: str, kind: str) -> None:
        self.scopes[id(node)] = self.current_scope
        qualname = _nested_qualname(self.stack, name)
        previous = self.stack
        self.stack = (*self.stack, (qualname, kind))
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self.stack = previous

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_named_scope(node, node.name, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_named_scope(node, node.name, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_named_scope(node, node.name, "function")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_named_scope(node, "<lambda>", "function")


def lexical_scopes(tree: ast.Module) -> Mapping[int, str]:
    """Map AST object identities to their containing class/function qualname."""

    visitor = _LexicalScopeVisitor()
    visitor.visit(tree)
    return MappingProxyType(visitor.scopes)


def lexical_scope_for(tree: ast.Module, anchor: ast.AST) -> str:
    """Return ``<module>`` or the exact named lexical scope containing anchor."""

    try:
        return lexical_scopes(tree)[id(anchor)]
    except KeyError as error:
        raise ValueError("anchor does not belong to the supplied AST") from error


def _normalize_json(value: object, *, location: str) -> JsonValue:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{location} must not contain NaN or infinity")
        return value
    if isinstance(value, list):
        return [_normalize_json(item, location=f"{location}[]") for item in value]
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError(f"{location} object keys must be strings")
        return {
            key: _normalize_json(value[key], location=f"{location}.{key}")
            for key in sorted(value)
        }
    raise ValueError(f"{location} must be deterministic JSON data, got {type(value).__name__}")


def canonicalize_candidate(candidate: AstCandidate) -> str:
    """Build the versioned canonical payload used for SHA-256 identity."""

    normalize_repo_path(candidate.path)
    if not candidate.lexical_scope or "::" in candidate.lexical_scope:
        raise ValueError("lexical scope must be non-empty and must not contain '::'")
    if not candidate.rule or "::" in candidate.rule:
        raise ValueError("rule must be non-empty and must not contain '::'")
    if not candidate.classification:
        raise ValueError("classification must be non-empty")
    if not isinstance(candidate.anchor, ast.AST):
        raise ValueError("anchor must be an AST node")
    if not isinstance(candidate.line, int) or isinstance(candidate.line, bool) or candidate.line < 1:
        raise ValueError("diagnostic line must be a positive integer")
    normalized_metadata = _normalize_json(candidate.metadata, location="metadata")
    if not isinstance(normalized_metadata, dict):
        raise ValueError("metadata must be a JSON object")
    return json.dumps(
        {
            "anchor": ast.dump(candidate.anchor, annotate_fields=True, include_attributes=False),
            "classification": candidate.classification,
            "metadata": normalized_metadata,
            "rule": candidate.rule,
            "version": CANONICAL_PAYLOAD_VERSION,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_position(candidate: AstCandidate) -> tuple[int, int, int, int]:
    line = getattr(candidate.anchor, "lineno", candidate.line)
    column = getattr(candidate.anchor, "col_offset", -1)
    end_line = getattr(candidate.anchor, "end_lineno", line)
    end_column = getattr(candidate.anchor, "end_col_offset", column)
    if line != candidate.line:
        raise OccurrenceOrderError(
            f"{candidate.path}:{candidate.line}: diagnostic line does not match anchor line {line}"
        )
    if column < 0 or end_line is None or end_column is None:
        raise OccurrenceOrderError(
            f"{candidate.path}:{candidate.line}: anchor lacks complete lexical source coordinates"
        )
    return line, column, end_line, end_column


def build_occurrences(candidates: Iterable[AstCandidate]) -> tuple[AstOccurrence, ...]:
    """Hash every candidate and assign 1-based duplicate ordinals in AST order."""

    prepared: list[tuple[AstCandidate, str, str, tuple[int, int, int, int]]] = []
    payload_by_fingerprint: dict[str, str] = {}
    for candidate in candidates:
        payload = canonicalize_candidate(candidate)
        fingerprint = _sha256_hex(payload)
        prior_payload = payload_by_fingerprint.setdefault(fingerprint, payload)
        if prior_payload != payload:
            raise FingerprintCollisionError(fingerprint)
        prepared.append((candidate, payload, fingerprint, _candidate_position(candidate)))

    prepared.sort(
        key=lambda item: (
            item[0].path,
            item[3],
            item[0].lexical_scope,
            item[0].rule,
            item[2],
        )
    )

    seen_positions: dict[tuple[str, str, str, str, tuple[int, int, int, int]], int] = Counter()
    for candidate, _, fingerprint, position in prepared:
        key = (candidate.path, candidate.lexical_scope, candidate.rule, fingerprint, position)
        seen_positions[key] += 1
        if seen_positions[key] > 1:
            raise OccurrenceOrderError(
                f"{candidate.path}:{candidate.line}: identical candidates share one lexical position"
            )

    ordinals: defaultdict[tuple[str, str, str, str], int] = defaultdict(int)
    occurrences: list[AstOccurrence] = []
    for candidate, payload, fingerprint, _ in prepared:
        ordinal_key = (candidate.path, candidate.lexical_scope, candidate.rule, fingerprint)
        ordinals[ordinal_key] += 1
        ordinal = ordinals[ordinal_key]
        occurrence_id = (
            f"{candidate.path}::{candidate.lexical_scope}::{candidate.rule}::{fingerprint}::{ordinal}"
        )
        occurrences.append(
            AstOccurrence(
                occurrence_id=occurrence_id,
                path=candidate.path,
                lexical_scope=candidate.lexical_scope,
                rule=candidate.rule,
                classification=candidate.classification,
                fingerprint=fingerprint,
                duplicate_ordinal=ordinal,
                line=candidate.line,
                canonical_payload=payload,
            )
        )
    return tuple(occurrences)


def ceiling_sha256(ids: Iterable[str]) -> str:
    """Return SHA-256 of the deterministic JSON representation of sorted IDs."""

    ordered = sorted(ids)
    payload = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _replacement_context(occurrence_id: str) -> str | None:
    parts = occurrence_id.split("::")
    if len(parts) != 5:
        return None
    return "::".join(parts[:3])


def compare_ratchet(
    actual_occurrences: Iterable[AstOccurrence],
    categories: Iterable[RatchetCategory],
) -> RatchetReport:
    """Compare actual IDs to exact live mappings and immutable ceilings."""

    actual = tuple(actual_occurrences)
    configured = tuple(categories)
    failures: list[RatchetFailure] = []

    actual_counts = Counter(occurrence.occurrence_id for occurrence in actual)
    for occurrence_id, count in sorted(actual_counts.items()):
        if count > 1:
            failures.append(
                RatchetFailure(
                    FailureKind.DUPLICATE_ID,
                    f"actual scan emitted the ID {count} times",
                    occurrence_id=occurrence_id,
                )
            )

    payloads_by_fingerprint: defaultdict[str, set[str]] = defaultdict(set)
    for occurrence in actual:
        payloads_by_fingerprint[occurrence.fingerprint].add(occurrence.canonical_payload)
    for fingerprint, payloads in sorted(payloads_by_fingerprint.items()):
        if len(payloads) > 1:
            failures.append(
                RatchetFailure(
                    FailureKind.FINGERPRINT_COLLISION,
                    f"SHA-256 {fingerprint} identifies {len(payloads)} canonical payloads",
                )
            )

    category_keys = Counter((category.rule, category.classification) for category in configured)
    for (rule, classification), count in sorted(category_keys.items()):
        if count > 1:
            failures.append(
                RatchetFailure(
                    FailureKind.OVERLAP,
                    f"{count} categories claim {rule}/{classification}",
                )
            )

    category_names = Counter(category.name for category in configured)
    for category_name, count in sorted(category_names.items()):
        if count > 1:
            failures.append(
                RatchetFailure(
                    FailureKind.OVERLAP,
                    f"category name is configured {count} times",
                    category=category_name,
                )
            )

    actual_by_category: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for occurrence in actual:
        actual_by_category[(occurrence.rule, occurrence.classification)].add(occurrence.occurrence_id)

    claimed_keys = set(category_keys)
    for (rule, classification), ids in sorted(actual_by_category.items()):
        if (rule, classification) not in claimed_keys:
            for occurrence_id in sorted(ids):
                failures.append(
                    RatchetFailure(
                        FailureKind.NEW,
                        f"no category is configured for {rule}/{classification}",
                        occurrence_id=occurrence_id,
                    )
                )

    memberships_by_rule: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for category in configured:
        live_ids = set(category.live)
        ceiling_counts = Counter(category.ceiling)
        ceiling_ids = set(category.ceiling)
        actual_ids = actual_by_category[(category.rule, category.classification)]

        for occurrence_id, count in sorted(ceiling_counts.items()):
            if count > 1:
                failures.append(
                    RatchetFailure(
                        FailureKind.DUPLICATE_ID,
                        f"immutable ceiling contains the ID {count} times",
                        category=category.name,
                        occurrence_id=occurrence_id,
                    )
                )

        for occurrence_id, reason in sorted(category.live.items()):
            if not isinstance(reason, str) or not reason.strip():
                failures.append(
                    RatchetFailure(
                        FailureKind.MISSING_REASON,
                        "live occurrence requires a non-empty reason",
                        category=category.name,
                        occurrence_id=occurrence_id,
                    )
                )

        if ceiling_sha256(category.ceiling) != category.ceiling_golden_sha256:
            failures.append(
                RatchetFailure(
                    FailureKind.GOLDEN_DRIFT,
                    "immutable ceiling does not match its reviewed golden SHA-256",
                    category=category.name,
                )
            )

        for occurrence_id in sorted(live_ids - ceiling_ids):
            failures.append(
                RatchetFailure(
                    FailureKind.CEILING_GROWTH,
                    "live occurrence is outside the immutable ceiling",
                    category=category.name,
                    occurrence_id=occurrence_id,
                )
            )

        new_ids = actual_ids - live_ids
        stale_ids = live_ids - actual_ids
        for occurrence_id in sorted(new_ids):
            failures.append(
                RatchetFailure(
                    FailureKind.NEW,
                    "actual occurrence is not in the live mapping",
                    category=category.name,
                    occurrence_id=occurrence_id,
                )
            )
        for occurrence_id in sorted(stale_ids):
            failures.append(
                RatchetFailure(
                    FailureKind.STALE,
                    "live occurrence is no longer emitted",
                    category=category.name,
                    occurrence_id=occurrence_id,
                )
            )

        new_by_context: defaultdict[str, set[str]] = defaultdict(set)
        stale_by_context: defaultdict[str, set[str]] = defaultdict(set)
        for occurrence_id in new_ids:
            if context := _replacement_context(occurrence_id):
                new_by_context[context].add(occurrence_id)
        for occurrence_id in stale_ids:
            if context := _replacement_context(occurrence_id):
                stale_by_context[context].add(occurrence_id)
        for context in sorted(new_by_context.keys() & stale_by_context.keys()):
            failures.append(
                RatchetFailure(
                    FailureKind.REPLACEMENT,
                    f"{context} replaced {sorted(stale_by_context[context])!r} "
                    f"with {sorted(new_by_context[context])!r}",
                    category=category.name,
                )
            )

        for occurrence_id in live_ids | ceiling_ids:
            memberships_by_rule[category.rule][occurrence_id].add(category.name)

    for rule, memberships in sorted(memberships_by_rule.items()):
        for occurrence_id, category_names in sorted(memberships.items()):
            if len(category_names) > 1:
                failures.append(
                    RatchetFailure(
                        FailureKind.OVERLAP,
                        f"ID belongs to multiple {rule} categories: {sorted(category_names)!r}",
                        occurrence_id=occurrence_id,
                    )
                )

    return RatchetReport(tuple(sorted(set(failures))))
