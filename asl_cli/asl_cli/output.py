"""Output formatting helpers.

Default output is pretty-printed JSON. The ``--table`` flag selects a
compact key-value table for list endpoints. ``--raw`` emits the raw JSON
string with no reformatting.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Sequence


def print_json(data: Any, *, indent: int = 2) -> None:
    """Pretty-print data as JSON to stdout."""
    json.dump(data, sys.stdout, indent=indent, ensure_ascii=False)
    sys.stdout.write("\n")


def print_table(rows: Sequence[dict], columns: Sequence[str] | None = None) -> None:
    """Print a list of dicts as a simple aligned table.

    If ``columns`` is None, uses the keys of the first row. Values are
    truncated to 50 chars for display.
    """
    if not rows:
        return
    cols = list(columns) if columns else list(rows[0].keys())
    widths = {c: len(c) for c in cols}
    formatted_rows: list[dict[str, str]] = []
    for row in rows:
        formatted = {}
        for col in cols:
            value = row.get(col, "")
            text = _format_cell(value)
            if len(text) > 50:
                text = text[:47] + "..."
            formatted[col] = text
            widths[col] = max(widths[col], len(text))
        formatted_rows.append(formatted)

    header = "  ".join(c.ljust(widths[c]) for c in cols)
    separator = "  ".join("-" * widths[c] for c in cols)
    sys.stdout.write(header + "\n")
    sys.stdout.write(separator + "\n")
    for row in formatted_rows:
        sys.stdout.write("  ".join(row[c].ljust(widths[c]) for c in cols) + "\n")


def _format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def print_output(data: Any, *, fmt: str = "json", columns: Sequence[str] | None = None) -> None:
    """Dispatch to the right formatter based on ``fmt``.

    ``fmt`` is ``"json"``, ``"table"``, or ``"raw"``.
    """
    if fmt == "raw":
        sys.stdout.write(json.dumps(data, ensure_ascii=False))
        sys.stdout.write("\n")
    elif fmt == "table":
        rows = data if isinstance(data, list) else [data] if data else []
        print_table(rows, columns=columns)
    else:
        print_json(data)
