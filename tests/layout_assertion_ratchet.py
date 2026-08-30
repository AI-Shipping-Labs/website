"""Exact-local-marker ratchet for direct layout-token assertion literals.

The scanner is intentionally lexical.  It does not evaluate Python values,
resolve imports or aliases, or follow strings through names, calls,
collections, formatters, parametrization, or helpers.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from tests.layout_assertion_ceilings import (
    UNKNOWN_UNMARKED_ASSERTION_CALL_CEILING,
    UNKNOWN_UNMARKED_ASSERTION_CALL_GOLDEN_SHA256,
    UNMARKED_DIRECT_LAYOUT_ASSERTION_CEILING,
    UNMARKED_DIRECT_LAYOUT_ASSERTION_GOLDEN_SHA256,
)
from tests.layout_assertion_live import LAYOUT_ASSERTION_LIVE
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

RULE = "direct-layout-token-assertion"
UNMARKED_DIRECT_LAYOUT_ASSERTION = "UNMARKED_DIRECT_LAYOUT_ASSERTION"
UNKNOWN_UNMARKED_ASSERTION_CALL = "UNKNOWN_UNMARKED_ASSERTION_CALL"
CLASSIFICATIONS = (UNMARKED_DIRECT_LAYOUT_ASSERTION, UNKNOWN_UNMARKED_ASSERTION_CALL)

DIRECT_MARKER_REMEDIATION = (
    "Add exact local @pytest.mark.visual_regression ownership or replace the layout-token assertion with a behavioral contract."
)
UNKNOWN_MARKER_REMEDIATION = (
    "Review the unsupported assertion-like call, then add exact local @pytest.mark.visual_regression ownership or rewrite it."
)

PYTHON_ASSERTION_REGISTRY_VERSION = "python>=3.13-unittest-v1"
PLAYWRIGHT_ASSERTION_REGISTRY_VERSION = "playwright-1.58.0-sync-expect-v1"
ASCII_WHITESPACE_TOKENIZER_VERSION = "ascii-whitespace-v1"
LAYOUT_TOKEN_GRAMMAR_VERSION = "tailwind-layout-token-v1"


@dataclass(frozen=True)
class OperandSpec:
    positional: tuple[int, ...]
    keywords: tuple[str, ...]
    variadic_from: int | None = None
    variadic_keywords: bool = False


def _spec(
    positional: tuple[int, ...],
    keywords: tuple[str, ...],
    *,
    variadic_from: int | None = None,
    variadic_keywords: bool = False,
) -> OperandSpec:
    return OperandSpec(positional, keywords, variadic_from, variadic_keywords)


# Exact Python 3.13 TestCase public assertion methods and documented semantic
# operands. ``msg`` positions/keywords are deliberately absent.
UNITTEST_ASSERTION_OPERANDS: Mapping[str, OperandSpec] = MappingProxyType(
    {
        "assertAlmostEqual": _spec((0, 1, 2, 4), ("first", "second", "places", "delta")),
        "assertCountEqual": _spec((0, 1), ("first", "second")),
        "assertDictEqual": _spec((0, 1), ("d1", "d2")),
        "assertEqual": _spec((0, 1), ("first", "second")),
        "assertFalse": _spec((0,), ("expr",)),
        "assertGreater": _spec((0, 1), ("a", "b")),
        "assertGreaterEqual": _spec((0, 1), ("a", "b")),
        "assertIn": _spec((0, 1), ("member", "container")),
        "assertIs": _spec((0, 1), ("expr1", "expr2")),
        "assertIsInstance": _spec((0, 1), ("obj", "cls")),
        "assertIsNone": _spec((0,), ("obj",)),
        "assertIsNot": _spec((0, 1), ("expr1", "expr2")),
        "assertIsNotNone": _spec((0,), ("obj",)),
        "assertLess": _spec((0, 1), ("a", "b")),
        "assertLessEqual": _spec((0, 1), ("a", "b")),
        "assertListEqual": _spec((0, 1), ("list1", "list2")),
        "assertLogs": _spec((0, 1), ("logger", "level")),
        "assertMultiLineEqual": _spec((0, 1), ("first", "second")),
        "assertNoLogs": _spec((0, 1), ("logger", "level")),
        "assertNotAlmostEqual": _spec((0, 1, 2, 4), ("first", "second", "places", "delta")),
        "assertNotEqual": _spec((0, 1), ("first", "second")),
        "assertNotIn": _spec((0, 1), ("member", "container")),
        "assertNotIsInstance": _spec((0, 1), ("obj", "cls")),
        "assertNotRegex": _spec((0, 1), ("text", "unexpected_regex")),
        "assertRaises": _spec(
            (0,),
            ("expected_exception",),
            variadic_from=1,
            variadic_keywords=True,
        ),
        "assertRaisesRegex": _spec(
            (0, 1),
            ("expected_exception", "expected_regex"),
            variadic_from=2,
            variadic_keywords=True,
        ),
        "assertRegex": _spec((0, 1), ("text", "expected_regex")),
        "assertSequenceEqual": _spec((0, 1, 3), ("seq1", "seq2", "seq_type")),
        "assertSetEqual": _spec((0, 1), ("set1", "set2")),
        "assertTrue": _spec((0,), ("expr",)),
        "assertTupleEqual": _spec((0, 1), ("tuple1", "tuple2")),
        "assertWarns": _spec(
            (0,),
            ("expected_warning",),
            variadic_from=1,
            variadic_keywords=True,
        ),
        "assertWarnsRegex": _spec(
            (0, 1),
            ("expected_warning", "expected_regex"),
            variadic_from=2,
            variadic_keywords=True,
        ),
    }
)


_NO_MATCHER_OPERANDS = _spec((), ())

# Exact public sync expect matchers in locked Playwright 1.58.0.  ``timeout`` is
# absent; behavior-affecting keyword options remain semantic operands.
PLAYWRIGHT_MATCHER_OPERANDS: Mapping[str, OperandSpec] = MappingProxyType(
    {
        "not_to_be_attached": _spec((), ("attached",)),
        "not_to_be_checked": _NO_MATCHER_OPERANDS,
        "not_to_be_disabled": _NO_MATCHER_OPERANDS,
        "not_to_be_editable": _spec((), ("editable",)),
        "not_to_be_empty": _NO_MATCHER_OPERANDS,
        "not_to_be_enabled": _spec((), ("enabled",)),
        "not_to_be_focused": _NO_MATCHER_OPERANDS,
        "not_to_be_hidden": _NO_MATCHER_OPERANDS,
        "not_to_be_in_viewport": _spec((), ("ratio",)),
        "not_to_be_visible": _spec((), ("visible",)),
        "not_to_contain_class": _spec((0,), ("expected",)),
        "not_to_contain_text": _spec((0,), ("expected", "use_inner_text", "ignore_case")),
        "not_to_have_accessible_description": _spec((0,), ("name", "ignore_case")),
        "not_to_have_accessible_error_message": _spec((0,), ("error_message", "ignore_case")),
        "not_to_have_accessible_name": _spec((0,), ("name", "ignore_case")),
        "not_to_have_attribute": _spec((0, 1), ("name", "value", "ignore_case")),
        "not_to_have_class": _spec((0,), ("expected",)),
        "not_to_have_count": _spec((0,), ("count",)),
        "not_to_have_css": _spec((0, 1), ("name", "value")),
        "not_to_have_id": _spec((0,), ("id",)),
        "not_to_have_js_property": _spec((0, 1), ("name", "value")),
        "not_to_have_role": _spec((0,), ("role",)),
        "not_to_have_text": _spec((0,), ("expected", "use_inner_text", "ignore_case")),
        "not_to_have_title": _spec((0,), ("title_or_reg_exp",)),
        "not_to_have_url": _spec((0,), ("url_or_reg_exp", "ignore_case")),
        "not_to_have_value": _spec((0,), ("value",)),
        "not_to_have_values": _spec((0,), ("values",)),
        "not_to_match_aria_snapshot": _spec((0,), ("expected",)),
        "not_to_be_ok": _NO_MATCHER_OPERANDS,
        "to_be_attached": _spec((), ("attached",)),
        "to_be_checked": _spec((), ("checked", "indeterminate")),
        "to_be_disabled": _NO_MATCHER_OPERANDS,
        "to_be_editable": _spec((), ("editable",)),
        "to_be_empty": _NO_MATCHER_OPERANDS,
        "to_be_enabled": _spec((), ("enabled",)),
        "to_be_focused": _NO_MATCHER_OPERANDS,
        "to_be_hidden": _NO_MATCHER_OPERANDS,
        "to_be_in_viewport": _spec((), ("ratio",)),
        "to_be_ok": _NO_MATCHER_OPERANDS,
        "to_be_visible": _spec((), ("visible",)),
        "to_contain_class": _spec((0,), ("expected",)),
        "to_contain_text": _spec((0,), ("expected", "use_inner_text", "ignore_case")),
        "to_have_accessible_description": _spec((0,), ("description", "ignore_case")),
        "to_have_accessible_error_message": _spec((0,), ("error_message", "ignore_case")),
        "to_have_accessible_name": _spec((0,), ("name", "ignore_case")),
        "to_have_attribute": _spec((0, 1), ("name", "value", "ignore_case")),
        "to_have_class": _spec((0,), ("expected",)),
        "to_have_count": _spec((0,), ("count",)),
        "to_have_css": _spec((0, 1), ("name", "value")),
        "to_have_id": _spec((0,), ("id",)),
        "to_have_js_property": _spec((0, 1), ("name", "value")),
        "to_have_role": _spec((0,), ("role",)),
        "to_have_text": _spec((0,), ("expected", "use_inner_text", "ignore_case")),
        "to_have_title": _spec((0,), ("title_or_reg_exp",)),
        "to_have_url": _spec((0,), ("url_or_reg_exp", "ignore_case")),
        "to_have_value": _spec((0,), ("value",)),
        "to_have_values": _spec((0,), ("values",)),
        "to_match_aria_snapshot": _spec((0,), ("expected",)),
    }
)


def operand_registry_sha256(registry: Mapping[str, OperandSpec]) -> str:
    payload = json.dumps(
        {
            name: {
                "keywords": spec.keywords,
                "positional": spec.positional,
                "variadic_from": spec.variadic_from,
                "variadic_keywords": spec.variadic_keywords,
            }
            for name, spec in sorted(registry.items())
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


UNITTEST_ASSERTION_REGISTRY_GOLDEN_SHA256 = "01faaffaf881bc4cf3dfd66daad29047f32c999daeeb243a515b14f276f6b292"
PLAYWRIGHT_ASSERTION_REGISTRY_GOLDEN_SHA256 = "d1e3aa98a6581a07484a7836735e4a62d53621f4997fd8ff43e326f99bb1a126"


LAYOUT_TOKEN_EXACT = (
    "absolute",
    "block",
    "contents",
    "fixed",
    "flex",
    "flow-root",
    "grid",
    "hidden",
    "inline",
    "inline-block",
    "inline-flex",
    "inline-grid",
    "relative",
    "sr-only",
    "static",
    "sticky",
    "table",
    "truncate",
)
LAYOUT_TOKEN_PREFIXES = (
    "aspect-", "auto-cols-", "auto-rows-", "basis-", "bg-", "border-", "bottom-",
    "box-", "break-", "clear-", "col-", "columns-", "container-", "cursor-", "divide-",
    "flex-", "float-", "gap-", "grid-cols-", "grid-flow-", "grid-rows-", "h-", "inset-",
    "items-", "justify-", "leading-", "left-", "m-", "max-h-", "max-w-", "mb-", "min-h-",
    "min-w-", "ml-", "mr-", "mt-", "mx-", "my-", "object-", "opacity-", "order-",
    "overflow-", "p-", "pb-", "place-", "pl-", "pr-", "pt-", "px-", "py-", "right-",
    "ring-", "rounded-", "row-", "self-", "shadow-", "size-", "space-", "text-", "top-",
    "w-", "whitespace-", "z-",
)
LAYOUT_VARIANTS = (
    "2xl", "active", "dark", "disabled", "focus", "focus-visible", "group-hover", "hover",
    "landscape", "lg", "max-2xl", "max-lg", "max-md", "max-sm", "max-xl", "md",
    "motion-reduce", "motion-safe", "peer-checked", "portrait", "print", "sm", "xl",
)
_ASCII_WHITESPACE_RE = re.compile(r"[ \t\n\r\f\v]+")
_HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{1}|[0-9A-Fa-f]{3}|[0-9A-Fa-f]{5})?\Z")
_TAILWIND_SUFFIX_RE = re.compile(r"[A-Za-z0-9_./%#\[\]()+*=-]+\Z")
LAYOUT_VOCABULARY_GOLDEN_SHA256 = "740ec9fd54c2c04294ea5eeebae84bfd7c8cfced41fd641ee1e7f8336f2ecfd1"


def vocabulary_sha256() -> str:
    payload = json.dumps(
        {
            "exact": LAYOUT_TOKEN_EXACT,
            "grammar": LAYOUT_TOKEN_GRAMMAR_VERSION,
            "hex_pattern": _HEX_COLOR_RE.pattern,
            "prefixes": LAYOUT_TOKEN_PREFIXES,
            "suffix_pattern": _TAILWIND_SUFFIX_RE.pattern,
            "tokenizer": ASCII_WHITESPACE_TOKENIZER_VERSION,
            "variants": LAYOUT_VARIANTS,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _strip_variants(token: str) -> str:
    remaining = token
    while ":" in remaining:
        variant, suffix = remaining.split(":", 1)
        if variant not in LAYOUT_VARIANTS:
            break
        remaining = suffix
    return remaining.removeprefix("!").removeprefix("-")


def is_layout_token(token: str) -> bool:
    """Match one complete ASCII-whitespace-delimited review vocabulary token."""

    candidate = _strip_variants(token)
    return bool(
        candidate in LAYOUT_TOKEN_EXACT
        or _HEX_COLOR_RE.fullmatch(candidate)
        or any(
            candidate.startswith(prefix)
            and (suffix := candidate[len(prefix) :])
            and _TAILWIND_SUFFIX_RE.fullmatch(suffix)
            for prefix in LAYOUT_TOKEN_PREFIXES
        )
    )


def _literal_tokens(node: ast.AST) -> tuple[tuple[int, str], ...]:
    literal_nodes: tuple[ast.Constant, ...]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        literal_nodes = (node,)
    elif isinstance(node, ast.JoinedStr):
        literal_nodes = tuple(
            part for part in node.values if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    elif isinstance(node, ast.BoolOp):
        return tuple(item for value in node.values for item in _literal_tokens(value))
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _literal_tokens(node.operand)
    elif isinstance(node, ast.Compare):
        return tuple(
            item
            for operand in (node.left, *node.comparators)
            for item in _literal_tokens(operand)
        )
    else:
        return ()

    matches: list[tuple[int, str]] = []
    for literal in literal_nodes:
        for token in _ASCII_WHITESPACE_RE.split(literal.value):
            if token and is_layout_token(token):
                matches.append((id(literal), token))
    return tuple(matches)


def _operands(call: ast.Call, spec: OperandSpec) -> tuple[ast.AST, ...]:
    indexes = set(spec.positional)
    if spec.variadic_from is not None:
        indexes.update(range(spec.variadic_from, len(call.args)))
    positional = [argument for index, argument in enumerate(call.args) if index in indexes]
    keyword_names = set(spec.keywords)
    keyword = [
        item.value
        for item in call.keywords
        if item.arg is not None
        and item.arg != "msg"
        and (item.arg in keyword_names or spec.variadic_keywords)
    ]
    return (*positional, *keyword)


def _exact_visual_marker(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "visual_regression"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "pytest"
    )


def _parents(tree: ast.AST) -> Mapping[int, ast.AST]:
    return {id(child): parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _scope_parent(node: ast.AST, parents: Mapping[int, ast.AST]) -> ast.AST | None:
    parent = parents.get(id(node))
    while parent is not None and not isinstance(
        parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    ):
        parent = parents.get(id(parent))
    return parent


def _is_marked(anchor: ast.AST, parents: Mapping[int, ast.AST]) -> bool:
    owner = _scope_parent(anchor, parents)
    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
        _exact_visual_marker(item) for item in owner.decorator_list
    ):
        return True
    if isinstance(owner, ast.ClassDef):
        return any(_exact_visual_marker(item) for item in owner.decorator_list)
    if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
        class_owner = _scope_parent(owner, parents)
        if isinstance(class_owner, ast.ClassDef):
            return any(_exact_visual_marker(item) for item in class_owner.decorator_list)
    return False


@dataclass(frozen=True)
class _Anchor:
    node: ast.Assert | ast.Call
    classification: str
    shape: str
    operands: tuple[ast.AST, ...]


def _call_anchor(call: ast.Call) -> _Anchor | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    method = call.func.attr
    if (
        isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
        and method in UNITTEST_ASSERTION_OPERANDS
    ):
        return _Anchor(
            call,
            UNMARKED_DIRECT_LAYOUT_ASSERTION,
            f"unittest:{method}",
            _operands(call, UNITTEST_ASSERTION_OPERANDS[method]),
        )
    receiver = call.func.value
    if (
        method in PLAYWRIGHT_MATCHER_OPERANDS
        and isinstance(receiver, ast.Call)
        and isinstance(receiver.func, ast.Name)
        and receiver.func.id == "expect"
    ):
        expect_operands = receiver.args[:1]
        expect_operands += tuple(
            item.value for item in receiver.keywords if item.arg == "actual"
        )
        return _Anchor(
            call,
            UNMARKED_DIRECT_LAYOUT_ASSERTION,
            f"playwright:{method}",
            (*expect_operands, *_operands(call, PLAYWRIGHT_MATCHER_OPERANDS[method])),
        )
    if method.startswith(("assert", "to_", "not_to_")):
        return _Anchor(
            call,
            UNKNOWN_UNMARKED_ASSERTION_CALL,
            f"unknown:{method}",
            tuple(call.args),
        )
    return None


def _collect_anchors(tree: ast.Module) -> tuple[_Anchor, ...]:
    anchors: list[_Anchor] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            anchors.append(
                _Anchor(node, UNMARKED_DIRECT_LAYOUT_ASSERTION, "plain-assert", (node.test,))
            )
        elif isinstance(node, ast.Call) and (anchor := _call_anchor(node)) is not None:
            anchors.append(anchor)
    return tuple(anchors)


def _depth(node: ast.AST, parents: Mapping[int, ast.AST]) -> int:
    depth = 0
    current = node
    while id(current) in parents:
        depth += 1
        current = parents[id(current)]
    return depth


def _identity_anchor(anchor: _Anchor, tokens: tuple[str, ...]) -> ast.Expr:
    normalized = ast.Expr(
        value=ast.Tuple(
            elts=[ast.Constant(anchor.shape), *(ast.Constant(token) for token in tokens)],
            ctx=ast.Load(),
        )
    )
    ast.copy_location(normalized, anchor.node)
    normalized.end_lineno = anchor.node.end_lineno
    normalized.end_col_offset = anchor.node.end_col_offset
    return normalized


def candidates_from_source(path: str, source: str) -> tuple[AstCandidate, ...]:
    parsed = parse_source(path, source)
    scopes = lexical_scopes(parsed.tree)
    parents = _parents(parsed.tree)
    claimed_literal_nodes: set[int] = set()
    candidates: list[AstCandidate] = []
    anchors = sorted(_collect_anchors(parsed.tree), key=lambda item: -_depth(item.node, parents))
    for anchor in anchors:
        matches = tuple(item for operand in anchor.operands for item in _literal_tokens(operand))
        if not matches:
            continue
        current = tuple((node_id, token) for node_id, token in matches if node_id not in claimed_literal_nodes)
        claimed_literal_nodes.update(node_id for node_id, _ in matches)
        tokens = tuple(sorted({token for _, token in current}))
        if not tokens or _is_marked(anchor.node, parents):
            continue
        identity = _identity_anchor(anchor, tokens)
        candidates.append(
            AstCandidate(
                path=parsed.path,
                lexical_scope=scopes[id(anchor.node)],
                rule=RULE,
                classification=anchor.classification,
                anchor=identity,
                metadata={"shape": anchor.shape, "tokens": list(tokens)},
                line=anchor.node.lineno,
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
    payload: Mapping[str, object] = LAYOUT_ASSERTION_LIVE,
) -> Mapping[str, Mapping[str, str]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("layout assertion live manifest must use schema_version 1")
    if set(payload) != {"schema_version", *CLASSIFICATIONS}:
        raise ValueError("layout assertion live manifest must contain exactly both classifications")
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
            name=UNMARKED_DIRECT_LAYOUT_ASSERTION,
            rule=RULE,
            classification=UNMARKED_DIRECT_LAYOUT_ASSERTION,
            live=live[UNMARKED_DIRECT_LAYOUT_ASSERTION],
            ceiling=UNMARKED_DIRECT_LAYOUT_ASSERTION_CEILING,
            ceiling_golden_sha256=UNMARKED_DIRECT_LAYOUT_ASSERTION_GOLDEN_SHA256,
        ),
        RatchetCategory(
            name=UNKNOWN_UNMARKED_ASSERTION_CALL,
            rule=RULE,
            classification=UNKNOWN_UNMARKED_ASSERTION_CALL,
            live=live[UNKNOWN_UNMARKED_ASSERTION_CALL],
            ceiling=UNKNOWN_UNMARKED_ASSERTION_CALL_CEILING,
            ceiling_golden_sha256=UNKNOWN_UNMARKED_ASSERTION_CALL_GOLDEN_SHA256,
        ),
    )


def compare_repository(
    repo_root: Path = REPO_ROOT,
    *,
    categories: Iterable[RatchetCategory] | None = None,
) -> RatchetReport:
    configured = ratchet_categories() if categories is None else tuple(categories)
    return compare_ratchet(scan_repository(repo_root), configured)
