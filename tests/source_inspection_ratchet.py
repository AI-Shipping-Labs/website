"""Lexical ratchet for visible Python source-inspection syntax in tests.

This rule is intentionally syntax-only. It inventories exact inspect API
anchors and exact open/path-read calls whose Python suffix remains visible at
the anchor. It does not resolve imports, aliases, variables, helpers, handles,
filesystem paths, control flow, or runtime-generated names.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Mapping

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
from tests.source_inspection_ceilings import (
    DIRECT_PY_OPEN_CEILING,
    DIRECT_PY_OPEN_GOLDEN_SHA256,
    DIRECT_PY_PATH_READ_CEILING,
    DIRECT_PY_PATH_READ_GOLDEN_SHA256,
    INSPECT_API_IMPORT_CEILING,
    INSPECT_API_IMPORT_GOLDEN_SHA256,
    INSPECT_API_REFERENCE_CEILING,
    INSPECT_API_REFERENCE_GOLDEN_SHA256,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE_CEILING,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE_GOLDEN_SHA256,
    UNKNOWN_VISIBLE_PY_PATH_READ_CEILING,
    UNKNOWN_VISIBLE_PY_PATH_READ_GOLDEN_SHA256,
)
from tests.source_inspection_live import SOURCE_INSPECTION_LIVE

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = "visible-python-source-inspection"
INSPECT_NAMES = ("findsource", "getsourcelines", "getsource")
PYTHON_SUFFIXES = (".py", ".pyw")

INSPECT_API_REFERENCE = "INSPECT_API_REFERENCE"
INSPECT_API_IMPORT = "INSPECT_API_IMPORT"
UNKNOWN_DYNAMIC_INSPECT_REFERENCE = "UNKNOWN_DYNAMIC_INSPECT_REFERENCE"
DIRECT_PY_OPEN = "DIRECT_PY_OPEN"
DIRECT_PY_PATH_READ = "DIRECT_PY_PATH_READ"
UNKNOWN_VISIBLE_PY_PATH_READ = "UNKNOWN_VISIBLE_PY_PATH_READ"
CLASSIFICATIONS = (
    INSPECT_API_REFERENCE,
    INSPECT_API_IMPORT,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE,
    DIRECT_PY_OPEN,
    DIRECT_PY_PATH_READ,
    UNKNOWN_VISIBLE_PY_PATH_READ,
)

INSPECT_REFERENCE_REWRITE_REASON = (
    "Replace the visible inspect API reference with a behavioral test against the owned public outcome."
)
INSPECT_IMPORT_REWRITE_REASON = (
    "Remove the inspect API import and test the owned public outcome without source inspection."
)
DYNAMIC_INSPECT_REWRITE_REASON = (
    "Replace the literal dynamic inspect reference with a behavioral test against the owned public outcome."
)
DIRECT_OPEN_REWRITE_REASON = (
    "Replace the literal Python source open with a behavioral test against the owned public outcome."
)
DIRECT_PATH_READ_REWRITE_REASON = (
    "Replace the literal Python path read with a behavioral test against the owned public outcome."
)
UNKNOWN_PATH_REWRITE_REASON = (
    "Replace the unsupported visible Python path read with a behavioral test; opaque runtime paths are outside v1."
)
REWRITE_REASONS = {
    INSPECT_API_REFERENCE: INSPECT_REFERENCE_REWRITE_REASON,
    INSPECT_API_IMPORT: INSPECT_IMPORT_REWRITE_REASON,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE: DYNAMIC_INSPECT_REWRITE_REASON,
    DIRECT_PY_OPEN: DIRECT_OPEN_REWRITE_REASON,
    DIRECT_PY_PATH_READ: DIRECT_PATH_READ_REWRITE_REASON,
    UNKNOWN_VISIBLE_PY_PATH_READ: UNKNOWN_PATH_REWRITE_REASON,
}


def _is_exact_builtin(node: ast.AST, name: str) -> bool:
    return (isinstance(node, ast.Name) and node.id == name) or (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "builtins"
        and node.attr == name
    )


def _literal_dynamic_inspect_name(node: ast.AST) -> str | None:
    if (
        not isinstance(node, ast.Call)
        or not _is_exact_builtin(node.func, "getattr")
        or len(node.args) < 2
        or not isinstance(node.args[1], ast.Constant)
        or type(node.args[1].value) is not str
        or node.args[1].value not in INSPECT_NAMES
    ):
        return None
    return node.args[1].value


def _literal_path_parts(node: ast.AST) -> tuple[str, ...] | None:
    """Return literal path parts for the bounded syntax grammar."""

    if isinstance(node, ast.Constant) and type(node.value) is str:
        return (node.value,)
    if isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == "Path")
        or (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "pathlib"
            and node.func.attr == "Path"
        )
    ):
        if len(node.args) != 1 or node.keywords:
            return None
        return _literal_path_parts(node.args[0])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _literal_path_parts(node.left)
        right = _literal_path_parts(node.right)
        if left is not None and right is not None:
            return (*left, *right)
    return None


def _is_bounded_python_path(node: ast.AST) -> bool:
    parts = _literal_path_parts(node)
    return bool(parts and parts[-1].endswith(PYTHON_SUFFIXES))


def _has_visible_python_literal(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Constant)
        and type(item.value) is str
        and item.value.endswith(PYTHON_SUFFIXES)
        for item in ast.walk(node)
    )


def _path_read_anchor(node: ast.AST) -> tuple[str, ast.AST] | None:
    if not isinstance(node, ast.Call):
        return None
    if _is_exact_builtin(node.func, "open") and node.args:
        return "open", node.args[0]
    if isinstance(node.func, ast.Attribute) and node.func.attr in {"read_bytes", "read_text"}:
        return node.func.attr, node.func.value
    return None


def candidates_from_source(path: str, source: str) -> tuple[AstCandidate, ...]:
    """Classify every exact visible source-inspection anchor in one source."""

    parsed = parse_source(path, source)
    scopes = lexical_scopes(parsed.tree)
    candidates: list[AstCandidate] = []

    for node in ast.walk(parsed.tree):
        classification: str | None = None
        metadata: dict[str, str] = {}

        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Load)
            and node.attr in INSPECT_NAMES
        ):
            classification = INSPECT_API_REFERENCE
            metadata = {"anchor_kind": "attribute", "inspect_name": node.attr}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "inspect":
            for alias in node.names:
                if alias.name not in INSPECT_NAMES:
                    continue
                candidates.append(
                    AstCandidate(
                        path=parsed.path,
                        lexical_scope=scopes[id(alias)],
                        rule=RULE,
                        classification=INSPECT_API_IMPORT,
                        anchor=alias,
                        metadata={"anchor_kind": "import-alias", "inspect_name": alias.name},
                        line=alias.lineno,
                    )
                )
            continue
        elif inspect_name := _literal_dynamic_inspect_name(node):
            classification = UNKNOWN_DYNAMIC_INSPECT_REFERENCE
            metadata = {"anchor_kind": "literal-getattr", "inspect_name": inspect_name}
        elif path_anchor := _path_read_anchor(node):
            anchor_kind, path_node = path_anchor
            if _is_bounded_python_path(path_node):
                classification = DIRECT_PY_OPEN if anchor_kind == "open" else DIRECT_PY_PATH_READ
            elif _has_visible_python_literal(path_node):
                classification = UNKNOWN_VISIBLE_PY_PATH_READ
            else:
                continue
            metadata = {"anchor_kind": anchor_kind}
        else:
            continue

        candidates.append(
            AstCandidate(
                path=parsed.path,
                lexical_scope=scopes[id(node)],
                rule=RULE,
                classification=classification,
                anchor=node,
                metadata=metadata,
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
    payload: Mapping[str, object] = SOURCE_INSPECTION_LIVE,
) -> Mapping[str, Mapping[str, str]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("source-inspection live manifest must use schema_version 1")
    if set(payload) != {"schema_version", *CLASSIFICATIONS}:
        raise ValueError("source-inspection live manifest must contain exactly all six classifications")

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
        (INSPECT_API_REFERENCE, INSPECT_API_REFERENCE_CEILING, INSPECT_API_REFERENCE_GOLDEN_SHA256),
        (INSPECT_API_IMPORT, INSPECT_API_IMPORT_CEILING, INSPECT_API_IMPORT_GOLDEN_SHA256),
        (
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE,
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE_CEILING,
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE_GOLDEN_SHA256,
        ),
        (DIRECT_PY_OPEN, DIRECT_PY_OPEN_CEILING, DIRECT_PY_OPEN_GOLDEN_SHA256),
        (DIRECT_PY_PATH_READ, DIRECT_PY_PATH_READ_CEILING, DIRECT_PY_PATH_READ_GOLDEN_SHA256),
        (
            UNKNOWN_VISIBLE_PY_PATH_READ,
            UNKNOWN_VISIBLE_PY_PATH_READ_CEILING,
            UNKNOWN_VISIBLE_PY_PATH_READ_GOLDEN_SHA256,
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


def repository_failure_diagnostic(report: RatchetReport) -> str:
    """Append actionable guidance for every category in a failed report."""

    categories = sorted(
        {
            failure.category
            for failure in report.failures
            if failure.category in REWRITE_REASONS
        }
    )
    guidance = [f"{category} rewrite: {REWRITE_REASONS[category]}" for category in categories]
    return "\n".join((report.diagnostic(), *guidance))
