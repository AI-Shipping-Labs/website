"""Lexical ratchet for visible literal status-200 assertion anchors.

This rule is intentionally syntax-only. It does not resolve imports, aliases,
constants, request provenance, response types, reachability, or neighboring
assertions. Exact syntax remains visible through nested AST shapes, but values
hidden behind names or aliases, generated AST, ``getattr``, and dynamic
attributes are outside v1 and are not inferred.
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
from tests.status_200_assertion_ceilings import (
    DIRECT_STATUS_200_ASSERTION_CEILING,
    DIRECT_STATUS_200_ASSERTION_GOLDEN_SHA256,
    UNKNOWN_STATUS_200_ASSERTION_SHAPE_CEILING,
    UNKNOWN_STATUS_200_ASSERTION_SHAPE_GOLDEN_SHA256,
)
from tests.status_200_assertion_live import STATUS_200_ASSERTION_LIVE

REPO_ROOT = Path(__file__).resolve().parent.parent

RULE = "literal-status-200-assertion"
DIRECT_STATUS_200_ASSERTION = "DIRECT_STATUS_200_ASSERTION"
UNKNOWN_STATUS_200_ASSERTION_SHAPE = "UNKNOWN_STATUS_200_ASSERTION_SHAPE"
CLASSIFICATIONS = (
    DIRECT_STATUS_200_ASSERTION,
    UNKNOWN_STATUS_200_ASSERTION_SHAPE,
)

DIRECT_REWRITE_REASON = "Rewrite the redundant literal status-200 anchor as a specific behavioral response contract."
UNKNOWN_REWRITE_REASON = (
    "Rewrite the unsupported visible status-200 shape as a direct form or a specific behavioral response contract."
)

_UNARY_ASSERTION_METHODS = {
    "assertFalse",
    "assertIsNone",
    "assertIsNotNone",
    "assertTrue",
}
_BINARY_ASSERTION_METHODS = {
    "assertCountEqual",
    "assertDictEqual",
    "assertEqual",
    "assertGreater",
    "assertGreaterEqual",
    "assertIn",
    "assertIs",
    "assertIsInstance",
    "assertIsNot",
    "assertLess",
    "assertLessEqual",
    "assertListEqual",
    "assertMultiLineEqual",
    "assertNotEqual",
    "assertNotIn",
    "assertNotIsInstance",
    "assertNotRegex",
    "assertRegex",
    "assertSequenceEqual",
    "assertSetEqual",
    "assertTupleEqual",
}
_TERNARY_ASSERTION_METHODS = {
    "assertAlmostEqual",
    "assertNotAlmostEqual",
}


def _expected_spelling(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is int and node.value == 200:
        return "200"
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "OK"
        and isinstance(node.value, ast.Name)
        and node.value.id == "HTTPStatus"
    ):
        return "HTTPStatus.OK"
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "HTTP_200_OK"
        and isinstance(node.value, ast.Name)
        and node.value.id == "status"
    ):
        return "status.HTTP_200_OK"
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "HTTP_200_OK"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "status"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "rest_framework"
    ):
        return "rest_framework.status.HTTP_200_OK"
    return None


def _is_status_operand(node: ast.AST) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "status_code"


def _pair_kind(left: ast.AST, right: ast.AST) -> str | None:
    if _is_status_operand(left) and (spelling := _expected_spelling(right)):
        return f"status-first:{spelling}"
    if _expected_spelling(left) and _is_status_operand(right):
        return f"expected-first:{_expected_spelling(left)}"
    return None


def _direct_plain_assert(node: ast.Assert) -> str | None:
    comparison = node.test
    if (
        not isinstance(comparison, ast.Compare)
        or len(comparison.ops) != 1
        or not isinstance(comparison.ops[0], (ast.Eq, ast.Is))
        or len(comparison.comparators) != 1
    ):
        return None
    return _pair_kind(comparison.left, comparison.comparators[0])


def _assertion_like_call(node: ast.AST) -> ast.Call | None:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    call = node.value
    if not isinstance(call.func, ast.Attribute) or not call.func.attr.startswith("assert"):
        return None
    return call


def _direct_unittest_call(node: ast.Expr) -> str | None:
    call = _assertion_like_call(node)
    if call is None:
        return None
    if (
        not isinstance(call.func.value, ast.Name)
        or call.func.value.id != "self"
        or call.func.attr not in {"assertEqual", "assertIs"}
        or len(call.args) != 2
        or call.keywords
    ):
        return None
    if not _is_status_operand(call.args[0]):
        return None
    spelling = _expected_spelling(call.args[1])
    return f"status-first:{spelling}" if spelling else None


def _nodes_have_visible_pair(nodes: Iterable[ast.AST]) -> bool:
    descendants = tuple(descendant for node in nodes for descendant in ast.walk(node))
    return any(_is_status_operand(item) for item in descendants) and any(
        _expected_spelling(item) for item in descendants
    )


def _call_assertion_operands(call: ast.Call) -> tuple[ast.AST, ...]:
    method = call.func.attr
    if method in _UNARY_ASSERTION_METHODS:
        message_position = 1
    elif method in _TERNARY_ASSERTION_METHODS:
        message_position = 3
    elif method in _BINARY_ASSERTION_METHODS:
        message_position = 2
    else:
        message_position = None

    positional = tuple(
        argument for index, argument in enumerate(call.args) if message_position is None or index != message_position
    )
    keyword = tuple(item.value for item in call.keywords if item.arg != "msg")
    return (*positional, *keyword)


def _classification(node: ast.Assert | ast.Expr) -> tuple[str, str] | None:
    if isinstance(node, ast.Assert):
        if pair_kind := _direct_plain_assert(node):
            return DIRECT_STATUS_200_ASSERTION, pair_kind
        if _nodes_have_visible_pair((node.test,)):
            return UNKNOWN_STATUS_200_ASSERTION_SHAPE, "plain-assert"
        return None

    call = _assertion_like_call(node)
    if call is None:
        return None
    if pair_kind := _direct_unittest_call(node):
        return DIRECT_STATUS_200_ASSERTION, pair_kind
    if _nodes_have_visible_pair(_call_assertion_operands(call)):
        return UNKNOWN_STATUS_200_ASSERTION_SHAPE, "assertion-like-call"
    return None


def candidates_from_source(path: str, source: str) -> tuple[AstCandidate, ...]:
    """Classify every recognized assertion anchor in one Python source."""

    parsed = parse_source(path, source)
    scopes = lexical_scopes(parsed.tree)
    candidates: list[AstCandidate] = []
    for node in ast.walk(parsed.tree):
        if not isinstance(node, (ast.Assert, ast.Expr)):
            continue
        classified = _classification(node)
        if classified is None:
            continue
        classification, shape = classified
        candidates.append(
            AstCandidate(
                path=parsed.path,
                lexical_scope=scopes[id(node)],
                rule=RULE,
                classification=classification,
                anchor=node,
                metadata={"shape": shape},
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
    payload: Mapping[str, object] = STATUS_200_ASSERTION_LIVE,
) -> Mapping[str, Mapping[str, str]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("status-200 live manifest must use schema_version 1")
    if set(payload) != {"schema_version", *CLASSIFICATIONS}:
        raise ValueError("status-200 live manifest must contain exactly both classifications")

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
) -> tuple[RatchetCategory, RatchetCategory]:
    live = load_live_manifest() if live_manifest is None else live_manifest
    return (
        RatchetCategory(
            name=DIRECT_STATUS_200_ASSERTION,
            rule=RULE,
            classification=DIRECT_STATUS_200_ASSERTION,
            live=live[DIRECT_STATUS_200_ASSERTION],
            ceiling=DIRECT_STATUS_200_ASSERTION_CEILING,
            ceiling_golden_sha256=DIRECT_STATUS_200_ASSERTION_GOLDEN_SHA256,
        ),
        RatchetCategory(
            name=UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            rule=RULE,
            classification=UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            live=live[UNKNOWN_STATUS_200_ASSERTION_SHAPE],
            ceiling=UNKNOWN_STATUS_200_ASSERTION_SHAPE_CEILING,
            ceiling_golden_sha256=UNKNOWN_STATUS_200_ASSERTION_SHAPE_GOLDEN_SHA256,
        ),
    )


def compare_repository(
    repo_root: Path = REPO_ROOT,
    *,
    categories: Iterable[RatchetCategory] | None = None,
) -> RatchetReport:
    configured = ratchet_categories() if categories is None else tuple(categories)
    return compare_ratchet(scan_repository(repo_root), configured)
