"""Static guard against hard-coded near-current Playwright date rot."""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

PLAYWRIGHT_DIR = Path(__file__).parent
DATE_ROT_OK = "date-rot-ok:"

DATE_STRING_RE = re.compile(
    r"(?<!\d)2026-\d{2}-\d{2}(?!\d)|(?<!\d)\d{2}/\d{2}/2026(?!\d)"
)
FUTURE_SENSITIVE_EVENT_FIELDS = {
    "start_datetime",
    "end_datetime",
    "starts_at",
    "ends_at",
}
FUTURE_SENSITIVE_FORM_FIELDS = FUTURE_SENSITIVE_EVENT_FIELDS | {
    "event_date",
    "start_date",
    "end_date",
}
FUTURE_SENSITIVE_FORM_LABELS = {
    field.replace("_", " ") for field in FUTURE_SENSITIVE_FORM_FIELDS
}
COHORT_DATE_FIELDS = {"start_date", "end_date"}
SPRINT_DATE_FIELDS = {"start_date", "end_date"}
FROZEN_OR_FIXED_NAMES = ("fixed", "frozen", "historical", "canonical")


@dataclass(frozen=True)
class DateRotViolation:
    path: Path
    line: int
    snippet: str
    reason: str

    def format(self) -> str:
        return (
            f"{self.path}:{self.line}: {self.reason}: {self.snippet}\n"
            "  Use timezone.now()/timezone.localdate() + timedelta(...), "
            "freeze time, or add a date-rot-ok: reason for an intentional fixed date."
        )


class ParentAnnotator(ast.NodeVisitor):
    def visit(self, node):
        for child in ast.iter_child_nodes(node):
            child.parent = node
        super().visit(node)


class DateRotScanner(ast.NodeVisitor):
    def __init__(self, path: Path, source: str):
        self.path = path
        self.source_lines = source.splitlines()
        self.violations: list[DateRotViolation] = []
        self._frozen_function_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        frozen = any(_is_freeze_time_call(decorator) for decorator in node.decorator_list)
        if frozen:
            self._frozen_function_depth += 1
        self.generic_visit(node)
        if frozen:
            self._frozen_function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if _is_2026_date_constructor(node) and self._is_future_sensitive(node):
            self._add_if_not_allowed(node, "hard-coded 2026 constructor in a future-sensitive field")

        for arg_index, arg in enumerate(node.args):
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and DATE_STRING_RE.search(arg.value)
                and self._call_arg_is_future_sensitive(node, arg_index)
            ):
                self._add_if_not_allowed(arg, "hard-coded 2026 form/input date")

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str) or not DATE_STRING_RE.search(node.value):
            return
        if self._is_future_sensitive(node):
            self._add_if_not_allowed(node, "hard-coded 2026 string in a future-sensitive field")
            return
        if _dict_value_key(node) in FUTURE_SENSITIVE_EVENT_FIELDS:
            self._add_if_not_allowed(node, "hard-coded 2026 string in a future-sensitive field")

    def _is_future_sensitive(self, node: ast.AST) -> bool:
        if self._frozen_function_depth:
            return False
        if _is_time_only_constructor(node):
            return False
        if _contains_fixed_intent_name(node):
            return False
        parent = getattr(node, "parent", None)
        if isinstance(parent, ast.keyword):
            if parent.arg in FUTURE_SENSITIVE_EVENT_FIELDS:
                return True
            call = getattr(parent, "parent", None)
            return (
                parent.arg in COHORT_DATE_FIELDS
                and isinstance(call, ast.Call)
                and "cohort" in _name(call.func).lower()
            ) or (
                parent.arg in SPRINT_DATE_FIELDS
                and isinstance(call, ast.Call)
                and _is_sprint_create_call(call)
            )
        key = _dict_value_key(node)
        if key in FUTURE_SENSITIVE_EVENT_FIELDS:
            return True
        target_name = _assignment_target_name(node)
        if target_name in FUTURE_SENSITIVE_EVENT_FIELDS:
            return True
        return False

    def _call_arg_is_future_sensitive(self, node: ast.Call, arg_index: int) -> bool:
        if self._frozen_function_depth or _contains_fixed_intent_name(node):
            return False

        if _call_method_name(node) == "fill" and _is_fill_value_arg(node, arg_index):
            selectors = _fill_target_strings(node)
            return any(_string_targets_future_sensitive_form_field(selector) for selector in selectors)

        return False

    def _add_if_not_allowed(self, node: ast.AST, reason: str) -> None:
        if _has_date_rot_ok(self.source_lines, node.lineno):
            return
        line = self.source_lines[node.lineno - 1].strip()
        self.violations.append(
            DateRotViolation(
                path=self.path,
                line=node.lineno,
                snippet=line,
                reason=reason,
            )
        )


def _scan_source(path: Path, source: str) -> list[DateRotViolation]:
    tree = ast.parse(source, filename=str(path))
    ParentAnnotator().visit(tree)
    scanner = DateRotScanner(path, source)
    scanner.visit(tree)
    return scanner.violations


def _scan_playwright_sources() -> list[DateRotViolation]:
    violations: list[DateRotViolation] = []
    for path in sorted(PLAYWRIGHT_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        violations.extend(_scan_source(path.relative_to(PLAYWRIGHT_DIR.parent), path.read_text()))
    return violations


def _is_2026_date_constructor(node: ast.Call) -> bool:
    if _name(node.func) not in {"date", "datetime", "datetime.date", "datetime.datetime", "dt.date", "dt.datetime"}:
        return False
    first = node.args[0] if node.args else None
    return isinstance(first, ast.Constant) and first.value == 2026


def _is_time_only_constructor(node: ast.AST) -> bool:
    parent = getattr(node, "parent", None)
    return isinstance(parent, ast.Attribute) and parent.attr == "time"


def _is_freeze_time_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and _name(node.func).endswith("freeze_time")


def _is_sprint_create_call(node: ast.Call) -> bool:
    func_name = _name(node.func)
    return func_name in {
        "Sprint.objects.create",
        "Sprint.objects.get_or_create",
        "Sprint.objects.update_or_create",
    }


def _contains_fixed_intent_name(node: ast.AST) -> bool:
    current = node
    while current is not None:
        target_name = _assignment_target_name(current) or ""
        owner_name = ""
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            owner_name = current.name
        call_name = ""
        if isinstance(current, ast.Call):
            call_name = _name(current.func)
        joined = f"{target_name} {owner_name} {call_name}".lower()
        if any(marker in joined for marker in FROZEN_OR_FIXED_NAMES):
            return True
        current = getattr(current, "parent", None)
    return False


def _dict_value_key(node: ast.AST) -> str | None:
    parent = getattr(node, "parent", None)
    if not isinstance(parent, ast.Dict):
        return None
    for key, value in zip(parent.keys, parent.values, strict=False):
        if value is node:
            return _constant_string(key)
    return None


def _assignment_target_name(node: ast.AST) -> str | None:
    current = node
    while current is not None:
        parent = getattr(current, "parent", None)
        if isinstance(parent, ast.Assign) and current is parent.value:
            if len(parent.targets) == 1:
                return _target_name(parent.targets[0])
            return None
        if isinstance(parent, ast.AnnAssign) and current is parent.value:
            return _target_name(parent.target)
        current = parent
    return None


def _target_name(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _string_targets_future_sensitive_form_field(value: str) -> bool:
    lowered = value.lower()
    if any(field in lowered for field in FUTURE_SENSITIVE_FORM_FIELDS):
        return True
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered)
    return any(label in normalized for label in FUTURE_SENSITIVE_FORM_LABELS)


def _call_method_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return _name(node.func)


def _is_fill_value_arg(node: ast.Call, arg_index: int) -> bool:
    return arg_index >= 1 if len(node.args) >= 2 else arg_index >= 0


def _fill_target_strings(node: ast.Call) -> list[str]:
    selectors: list[str] = []
    if len(node.args) >= 2:
        selector = _constant_string(node.args[0])
        if selector:
            selectors.append(selector)

    if isinstance(node.func, ast.Attribute):
        selectors.extend(_receiver_query_strings(node.func.value))

    return selectors


def _receiver_query_strings(node: ast.AST) -> list[str]:
    selectors: list[str] = []
    current: ast.AST | None = node

    while current is not None:
        if isinstance(current, ast.Call):
            if _call_method_name(current) in {
                "locator",
                "frame_locator",
                "get_by_label",
                "get_by_placeholder",
                "get_by_test_id",
                "query_selector",
            }:
                selectors.extend(_call_constant_strings(current))
            current = current.func
            continue
        if isinstance(current, ast.Attribute):
            current = current.value
            continue
        break

    return selectors


def _call_constant_strings(node: ast.Call) -> list[str]:
    strings = [value for arg in node.args if (value := _constant_string(arg))]
    strings.extend(
        value
        for keyword in node.keywords
        if (value := _constant_string(keyword.value))
    )
    return strings


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _has_date_rot_ok(source_lines: list[str], lineno: int) -> bool:
    indexes = [lineno - 1, lineno - 2]
    for index in indexes:
        if 0 <= index < len(source_lines) and DATE_ROT_OK in source_lines[index]:
            return True
    return False


def test_date_rot_guard_rejects_unsafe_future_sensitive_fixture():
    source = textwrap.dedent(
        """
        from datetime import datetime

        def test_bad():
            Event.objects.create(start_datetime=datetime(2026, 7, 8, 18, 0))
        """
    )

    violations = _scan_source(Path("sample.py"), source)

    assert len(violations) == 1
    assert "start_datetime" in violations[0].snippet
    assert "timezone.now" in violations[0].format()


def test_date_rot_guard_rejects_iso_timestamp_with_t_boundary():
    source = textwrap.dedent(
        """
        def test_bad_iso_timestamp():
            Event.objects.create(start_datetime="2026-07-18T12:00:00Z")
        """
    )

    violations = _scan_source(Path("sample.py"), source)

    assert len(violations) == 1
    assert "start_datetime" in violations[0].snippet


def test_date_rot_guard_rejects_unsafe_sprint_start_date_fixture():
    source = textwrap.dedent(
        """
        import datetime

        def test_bad_sprint():
            Sprint.objects.create(start_date=datetime.date(2026, 5, 1))
        """
    )

    violations = _scan_source(Path("sample.py"), source)

    assert len(violations) == 1
    assert "start_date" in violations[0].snippet
    assert "timezone.localdate" in violations[0].format()


def test_date_rot_guard_rejects_chained_locator_fill_for_start_date():
    source = textwrap.dedent(
        """
        def test_bad_fill(page):
            page.locator("input[name='start_date']").fill("2026-05-01")
        """
    )

    violations = _scan_source(Path("sample.py"), source)

    assert len(violations) == 1
    assert "locator" in violations[0].snippet
    assert "form/input date" in violations[0].reason


def test_date_rot_guard_rejects_chained_label_fill_for_start_date():
    source = textwrap.dedent(
        """
        def test_bad_fill(page):
            page.get_by_label("Start date").fill("2026-05-01")
        """
    )

    violations = _scan_source(Path("sample.py"), source)

    assert len(violations) == 1
    assert "get_by_label" in violations[0].snippet
    assert "form/input date" in violations[0].reason


def test_date_rot_guard_allows_reasoned_fixed_dates_and_frozen_exact_copy():
    source = textwrap.dedent(
        """
        from datetime import datetime
        from freezegun import freeze_time

        def test_reasoned():
            # date-rot-ok: canonical historical workshop URL
            page.goto("/workshops/2026-06-18-cloudflare")  # dev-goto-ok

        @freeze_time("2026-06-17T12:00:00Z")
        def test_frozen():
            Event.objects.create(start_datetime=datetime(2026, 6, 24, 16, 0))
        """
    )

    assert _scan_source(Path("sample.py"), source) == []


def test_no_unsafe_hard_coded_2026_dates_in_future_sensitive_playwright_fixtures():
    violations = _scan_playwright_sources()

    assert violations == [], "\n" + "\n".join(violation.format() for violation in violations)
