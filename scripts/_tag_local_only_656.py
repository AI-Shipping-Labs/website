"""One-off helper for issue #656: tag local-helper-using Playwright tests
with ``pytestmark = pytest.mark.local_only`` (or extend an existing
``pytestmark``). Idempotent; safe to re-run.

Usage:
    uv run python scripts/_tag_local_only_656.py
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

HELPER_NAMES = (
    "create_user",
    "create_staff_user",
    "auth_context",
    "create_session_for_user",
    "ensure_tiers",
    "ensure_site_config_tiers",
)

ROOT = Path(__file__).resolve().parent.parent
PLAYWRIGHT_DIR = ROOT / "playwright_tests"

PYTESTMARK_LINE = re.compile(r"^pytestmark\s*=\s*(.+)$", re.MULTILINE)


def uses_helpers(source: str) -> bool:
    for name in HELPER_NAMES:
        if re.search(rf"\b{name}\b", source):
            return True
    return False


def already_has_local_only(source: str) -> bool:
    return "pytest.mark.local_only" in source


def find_insertion_offset(source: str) -> int:
    """Pick an offset to insert a new top-level ``pytestmark`` statement.

    Insert after the last top-level import / `os.environ.setdefault(...)` /
    module docstring, but before the first def/class. Falls back to byte 0
    if nothing matched (the file is non-standard).
    """
    tokens = list(tokenize.tokenize(io.BytesIO(source.encode()).readline))
    last_safe_lineno = 0
    in_paren = 0
    for tok in tokens:
        if tok.type == tokenize.OP and tok.string in "([{":
            in_paren += 1
        elif tok.type == tokenize.OP and tok.string in ")]}":
            in_paren -= 1

    # Walk lines: stop at the first def/class/decorated definition.
    lines = source.splitlines(keepends=True)
    stop_lineno = len(lines)
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("def ", "class ", "@")):
            stop_lineno = i
            break

    # Now walk top-level statements before stop_lineno and remember the
    # END of the last "safe" one (import, os.environ.setdefault, docstring).
    for tok in tokens:
        if tok.type != tokenize.NEWLINE:
            continue
        lineno = tok.end[0]
        if lineno >= stop_lineno:
            break
        # Inspect the line text.
        line_text = lines[lineno - 1].rstrip("\n")
        s = line_text.lstrip()
        # docstring close (triple-quote) handled via STRING token below
        if (
            s.startswith(("import ", "from "))
            or s.startswith("os.environ")
            or s.startswith("pytestmark")
        ):
            last_safe_lineno = lineno

    # Also accept a leading module docstring as safe.
    # tokenize STRING at top level at line 1 = docstring.
    for tok in tokens:
        if tok.type == tokenize.STRING and tok.start[1] == 0 and tok.start[0] == 1:
            last_safe_lineno = max(last_safe_lineno, tok.end[0])
            break

    if last_safe_lineno == 0:
        return 0

    return sum(len(line) for line in lines[:last_safe_lineno])


def extend_existing_pytestmark(source: str) -> str | None:
    """If a single-value ``pytestmark`` exists, convert to a list including
    ``pytest.mark.local_only``. Returns new source or None when no change.
    """
    match = PYTESTMARK_LINE.search(source)
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith("["):
        # Already a list — insert the marker if missing.
        if "pytest.mark.local_only" in value:
            return None
        new_value = value.rstrip("]") + ", pytest.mark.local_only]"
        return source[: match.start(1)] + new_value + source[match.end(1):]
    # Single value — wrap into list.
    if value == "pytest.mark.local_only":
        return None
    new_value = f"[{value}, pytest.mark.local_only]"
    return source[: match.start(1)] + new_value + source[match.end(1):]


def insert_new_pytestmark(source: str) -> str:
    offset = find_insertion_offset(source)
    prefix = source[:offset]
    suffix = source[offset:]
    # Ensure we land on a blank line.
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    block = (
        "\n# Issue #656: this module uses local-only fixtures (DB seeding,\n"
        "# session-cookie injection, etc.) and cannot run against the\n"
        "# deployed dev environment. See _docs/testing-guidelines.md.\n"
        "pytestmark = pytest.mark.local_only\n"
    )
    return prefix + block + suffix


def ensure_pytest_import(source: str) -> str:
    if re.search(r"^\s*import\s+pytest\b", source, re.MULTILINE):
        return source
    if re.search(r"^\s*from\s+pytest\b", source, re.MULTILINE):
        return source
    # Insert an "import pytest" after the first import block.
    lines = source.splitlines(keepends=True)
    inserted = False
    for i, line in enumerate(lines):
        s = line.lstrip()
        if s.startswith(("import ", "from ")):
            # Insert after the LAST contiguous import line.
            j = i
            while j < len(lines):
                t = lines[j].lstrip()
                if t.startswith(("import ", "from ")) or t.strip() == "":
                    j += 1
                else:
                    break
            lines.insert(j, "import pytest\n")
            inserted = True
            break
    if not inserted:
        return "import pytest\n" + source
    return "".join(lines)


def process(path: Path) -> str:
    source = path.read_text()
    if not uses_helpers(source):
        return "skip-no-helpers"
    if already_has_local_only(source):
        return "skip-already-tagged"
    source = ensure_pytest_import(source)
    extended = extend_existing_pytestmark(source)
    if extended is not None:
        path.write_text(extended)
        return "extended"
    new_source = insert_new_pytestmark(source)
    path.write_text(new_source)
    return "inserted"


def main():
    counts: dict[str, int] = {}
    for path in sorted(PLAYWRIGHT_DIR.glob("test_*.py")):
        result = process(path)
        counts[result] = counts.get(result, 0) + 1
        if result in ("inserted", "extended"):
            print(f"  {result}: {path.name}")
    print()
    for k, v in sorted(counts.items()):
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
