"""Lexical ratchet for visible exact ``assert_called_once`` references.

This rule is intentionally syntax-only. It classifies the exact attribute
anchor without resolving receiver provenance, aliases, call history, control
flow, neighboring assertions, or observable outcomes. A literal
``getattr(receiver, "assert_called_once")`` is the only supported dynamic
form because its prohibited name remains statically visible.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Mapping

from tests.assert_called_once_ceilings import (
    DIRECT_BARE_CALL_CEILING,
    DIRECT_BARE_CALL_GOLDEN_SHA256,
    NONCALL_REFERENCE_CEILING,
    NONCALL_REFERENCE_GOLDEN_SHA256,
    UNKNOWN_CALL_SHAPE_CEILING,
    UNKNOWN_CALL_SHAPE_GOLDEN_SHA256,
    UNKNOWN_DYNAMIC_REFERENCE_CEILING,
    UNKNOWN_DYNAMIC_REFERENCE_GOLDEN_SHA256,
)
from tests.assert_called_once_live import ASSERT_CALLED_ONCE_LIVE
from tests.lexical_ast_ratchet import (
    AstCandidate,
    AstOccurrence,
    RatchetCategory,
    RatchetReport,
    SourceDiscoveryError,
    build_occurrences,
    compare_ratchet,
    discover_python_sources,
    lexical_scopes,
    parse_file,
    parse_source,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = "visible-assert-called-once-reference"
PROHIBITED_ATTRIBUTE = "assert_called_once"

DIRECT_BARE_CALL = "DIRECT_BARE_CALL"
UNKNOWN_CALL_SHAPE = "UNKNOWN_CALL_SHAPE"
NONCALL_REFERENCE = "NONCALL_REFERENCE"
UNKNOWN_DYNAMIC_REFERENCE = "UNKNOWN_DYNAMIC_REFERENCE"
CLASSIFICATIONS = (
    DIRECT_BARE_CALL,
    UNKNOWN_CALL_SHAPE,
    NONCALL_REFERENCE,
    UNKNOWN_DYNAMIC_REFERENCE,
)

DIRECT_REWRITE_REASON = "Replace the bare call with an argument-aware assertion or assert the owned observable effect."
UNKNOWN_CALL_REWRITE_REASON = (
    "Replace the unsupported call shape with a direct argument-aware assertion or observable-effect assertion."
)
NONCALL_REWRITE_REASON = "Replace the bound or passed-through reference with a direct argument-aware assertion."
DYNAMIC_REWRITE_REASON = "Replace the literal dynamic reference with a direct argument-aware assertion."


def _parent_nodes(tree: ast.Module) -> dict[int, ast.AST]:
    return {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _attribute_classification(anchor: ast.Attribute, parent: ast.AST | None) -> str:
    if isinstance(parent, ast.Call) and parent.func is anchor:
        if not parent.args and not parent.keywords:
            return DIRECT_BARE_CALL
        return UNKNOWN_CALL_SHAPE
    return NONCALL_REFERENCE


def _is_literal_dynamic_reference(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and type(node.args[1].value) is str
        and node.args[1].value == PROHIBITED_ATTRIBUTE
    )


def candidates_from_source(path: str, source: str) -> tuple[AstCandidate, ...]:
    """Classify every visible exact attribute or literal dynamic anchor."""

    parsed = parse_source(path, source)
    scopes = lexical_scopes(parsed.tree)
    parents = _parent_nodes(parsed.tree)
    candidates: list[AstCandidate] = []

    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and node.attr == PROHIBITED_ATTRIBUTE:
            classification = _attribute_classification(node, parents.get(id(node)))
            candidates.append(
                AstCandidate(
                    path=parsed.path,
                    lexical_scope=scopes[id(node)],
                    rule=RULE,
                    classification=classification,
                    anchor=node,
                    metadata={"anchor_kind": "attribute"},
                    line=node.lineno,
                )
            )
        elif _is_literal_dynamic_reference(node):
            candidates.append(
                AstCandidate(
                    path=parsed.path,
                    lexical_scope=scopes[id(node)],
                    rule=RULE,
                    classification=UNKNOWN_DYNAMIC_REFERENCE,
                    anchor=node,
                    metadata={"anchor_kind": "literal-getattr"},
                    line=node.lineno,
                )
            )

    return tuple(candidates)


def occurrences_from_source(path: str, source: str) -> tuple[AstOccurrence, ...]:
    return build_occurrences(candidates_from_source(path, source))


def _discover_test_roots_once(repo_root: Path) -> tuple[str, ...]:
    roots = {"tests", "playwright_tests"}
    try:
        children = tuple(repo_root.iterdir())
    except OSError as error:
        raise SourceDiscoveryError(f"cannot enumerate repository test roots: {error}") from error
    for child in children:
        tests_root = child / "tests"
        if child.is_dir() and tests_root.is_dir():
            roots.add(tests_root.relative_to(repo_root).as_posix())
    return tuple(sorted(roots))


def discover_test_sources(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Discover root, app, CLI, and Playwright Python test sources."""

    resolved_root = repo_root.resolve(strict=True)
    first = _discover_test_roots_once(resolved_root)
    second = _discover_test_roots_once(resolved_root)
    if first != second:
        raise SourceDiscoveryError(f"test roots changed while scanning; first={first!r}, second={second!r}")
    return discover_python_sources(resolved_root, first)


def scan_repository(repo_root: Path = REPO_ROOT) -> tuple[AstOccurrence, ...]:
    candidates: list[AstCandidate] = []
    for path in discover_test_sources(repo_root):
        parsed = parse_file(repo_root, path)
        candidates.extend(candidates_from_source(parsed.path, parsed.source))
    return build_occurrences(candidates)


def load_live_manifest(
    payload: Mapping[str, object] = ASSERT_CALLED_ONCE_LIVE,
) -> Mapping[str, Mapping[str, str]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("assert-called-once live manifest must use schema_version 1")
    if set(payload) != {"schema_version", *CLASSIFICATIONS}:
        raise ValueError("assert-called-once live manifest must contain exactly all four classifications")

    loaded: dict[str, dict[str, str]] = {}
    for classification in CLASSIFICATIONS:
        entries = payload[classification]
        if not isinstance(entries, dict) or any(
            not isinstance(key, str) or not isinstance(reason, str) for key, reason in entries.items()
        ):
            raise ValueError(f"{classification} live manifest must be an ID-to-reason object")
        loaded[classification] = dict(entries)
    return loaded


def ratchet_categories(
    live_manifest: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[RatchetCategory, ...]:
    live = load_live_manifest() if live_manifest is None else live_manifest
    configuration = (
        (
            DIRECT_BARE_CALL,
            DIRECT_BARE_CALL_CEILING,
            DIRECT_BARE_CALL_GOLDEN_SHA256,
        ),
        (
            UNKNOWN_CALL_SHAPE,
            UNKNOWN_CALL_SHAPE_CEILING,
            UNKNOWN_CALL_SHAPE_GOLDEN_SHA256,
        ),
        (
            NONCALL_REFERENCE,
            NONCALL_REFERENCE_CEILING,
            NONCALL_REFERENCE_GOLDEN_SHA256,
        ),
        (
            UNKNOWN_DYNAMIC_REFERENCE,
            UNKNOWN_DYNAMIC_REFERENCE_CEILING,
            UNKNOWN_DYNAMIC_REFERENCE_GOLDEN_SHA256,
        ),
    )
    return tuple(
        RatchetCategory(
            name=classification,
            rule=RULE,
            classification=classification,
            live=live[classification],
            ceiling=ceiling,
            ceiling_golden_sha256=golden,
        )
        for classification, ceiling, golden in configuration
    )


def compare_repository(
    repo_root: Path = REPO_ROOT,
    *,
    categories: Iterable[RatchetCategory] | None = None,
) -> RatchetReport:
    configured = ratchet_categories() if categories is None else tuple(categories)
    return compare_ratchet(scan_repository(repo_root), configured)
