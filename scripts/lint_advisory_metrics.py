#!/usr/bin/env python3
"""Report non-blocking lint trend metrics that Ruff does not summarize."""

from __future__ import annotations

import ast
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FunctionMetric:
    path: Path
    name: str
    lines: int
    lineno: int


@dataclass(frozen=True)
class BroadExceptMetric:
    path: Path
    lineno: int
    kind: str


class MetricsVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.scope: list[str] = []
        self.functions: list[FunctionMetric] = []
        self.broad_excepts: list[BroadExceptMetric] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is None:
            self.broad_excepts.append(BroadExceptMetric(self.path, node.lineno, "bare"))
        elif _is_broad_exception(node.type):
            self.broad_excepts.append(BroadExceptMetric(self.path, node.lineno, "Exception"))
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        end_lineno = getattr(node, "end_lineno", node.lineno)
        qualified_name = ".".join([*self.scope, node.name])
        self.functions.append(FunctionMetric(self.path, qualified_name, end_lineno - node.lineno + 1, node.lineno))
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()


def _is_broad_exception(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id in {"Exception", "BaseException"}
    if isinstance(node, ast.Attribute):
        return node.attr in {"Exception", "BaseException"}
    if isinstance(node, ast.Tuple):
        return any(_is_broad_exception(element) for element in node.elts)
    return False


def _tracked_python_files() -> list[Path]:
    output = subprocess.check_output(["git", "ls-files", "*.py"], text=True)
    return [Path(line) for line in output.splitlines() if line]


def _bucket(path: Path) -> str:
    parts = path.parts
    if "migrations" in parts:
        return "migrations"
    if parts[0] in {"playwright_tests", "scripts", "deploy"}:
        return parts[0]
    if len(parts) >= 2 and parts[1] in {"tests", "admin", "management"}:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def main() -> int:
    functions: list[FunctionMetric] = []
    broad_excepts: list[BroadExceptMetric] = []
    syntax_errors: list[Path] = []

    for path in _tracked_python_files():
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:
            syntax_errors.append(path)
            continue

        visitor = MetricsVisitor(path)
        visitor.visit(tree)
        functions.extend(visitor.functions)
        broad_excepts.extend(visitor.broad_excepts)

    function_count = len(functions)
    longest = max(functions, key=lambda item: item.lines, default=None)
    runtime_broad_excepts = [item for item in broad_excepts if "migrations" not in item.path.parts]

    print("\nAdvisory trend metrics")
    print("----------------------")
    print(f"ADVISORY_METRIC python_files={len(_tracked_python_files())}")
    print(f"ADVISORY_METRIC function_count={function_count}")
    if longest:
        print(
            "ADVISORY_METRIC "
            f"max_function_lines={longest.lines} path={longest.path}:{longest.lineno} function={longest.name}"
        )
    print(f"ADVISORY_METRIC broad_except_total={len(broad_excepts)}")
    print(f"ADVISORY_METRIC broad_except_non_migration={len(runtime_broad_excepts)}")
    if syntax_errors:
        print(f"ADVISORY_METRIC syntax_error_files={len(syntax_errors)}")

    print("\nTop function lengths")
    for item in sorted(functions, key=lambda value: value.lines, reverse=True)[:10]:
        print(f"{item.lines:4d} {item.path}:{item.lineno} {item.name}")

    print("\nBroad exception counts by area")
    for area, count in Counter(_bucket(item.path) for item in runtime_broad_excepts).most_common(10):
        print(f"{count:4d} {area}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
