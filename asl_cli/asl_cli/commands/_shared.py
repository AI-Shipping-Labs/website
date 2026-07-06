"""Shared helpers for command modules.

Every command module exposes a module-level ``groups`` list of top-level
click.Group objects, which the CLI entry point registers directly. Nested
commands are attached to their parent group within each module.
"""

from __future__ import annotations

from typing import Any

import click

from asl_cli.client import staff_client
from asl_cli.output import print_output

FORMAT_CHOICES = ["json", "table", "raw"]

# Tier level name -> numeric value (content/access.py).
TIER_LEVELS = {
    "open": 0,
    "registered": 5,
    "basic": 10,
    "main": 20,
    "premium": 30,
}


class TierLevel(click.ParamType):
    """Click type that accepts a tier name or a raw integer."""

    name = "tier"

    def convert(self, value, param, ctx):
        if value is None:
            return None
        if isinstance(value, int):
            return value
        lowered = str(value).strip().lower()
        if lowered in TIER_LEVELS:
            return TIER_LEVELS[lowered]
        try:
            return int(lowered)
        except ValueError:
            self.fail(f"{value!r} is not a valid tier name or integer", param, ctx)


def format_option(func):
    """``--format`` option (json/table/raw), default json."""
    return click.option(
        "-f",
        "--format",
        "fmt",
        type=click.Choice(FORMAT_CHOICES),
        default="json",
        help="Output format: json (default), table, or raw.",
    )(func)


def emit(data: Any, fmt: str, columns: list[str] | None = None) -> None:
    """Print ``data`` using the chosen format."""
    print_output(data, fmt=fmt, columns=columns)


def get_client():
    """Return a fresh staff client (commands are short-lived)."""
    return staff_client()


def json_option(name="data", required=False, help_text=""):
    """A Click option that accepts a JSON string or @file path.

    Use for payloads that are too complex for individual flags (nested
    arrays of objects, etc).
    """

    def callback(ctx, param, value):
        if value is None:
            return None
        import json
        import pathlib

        if isinstance(value, str) and value.startswith("@"):
            return json.loads(pathlib.Path(value[1:]).read_text())
        return json.loads(value)

    return click.option(
        "--" + name.replace("_", "-"),
        name,
        required=required,
        callback=callback,
        help=help_text or "JSON payload (or @file.json).",
    )


def comma_list(name, help_text, item_type=str):
    """A Click option that accepts comma-separated values as a list."""

    def callback(ctx, param, value):
        if not value:
            return None
        parts = [v.strip() for v in value.split(",") if v.strip()]
        if item_type is int:
            return [int(p) for p in parts]
        return parts

    return click.option(
        "--" + name.replace("_", "-"),
        name.replace("-", "_"),
        default="",
        callback=callback,
        help=help_text,
    )


def collect_flags(ctx, exclude=None):
    """Collect non-None flag values from a Click command's params.

    Used by create/update commands to build the JSON body from individual
    --flags. ``exclude`` names params that should never go into the body
    (like ``fmt``).
    """
    exclude = exclude or set()
    exclude.add("fmt")
    result = {}
    for param in ctx.command.params:
        if param.name in exclude:
            continue
        value = ctx.params.get(param.name)
        if value is not None:
            result[param.name] = value
    return result
