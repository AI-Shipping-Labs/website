"""Shared helpers for command modules.

Every command module exposes a module-level ``commands`` list that the
CLI entry point registers. This keeps registration centralized and avoids
Click's global ``@cli.command()`` decorator (which requires import order).
"""

from __future__ import annotations

from typing import Any

import click

from asl_cli.client import staff_client
from asl_cli.output import print_output

FORMAT_CHOICES = ["json", "table", "raw"]


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


def json_arg(name: str = "data", required: bool = False):
    """A Click argument that accepts a JSON string or ``@file`` path."""

    def callback(ctx, param, value):
        if value is None:
            return None
        import json
        import pathlib

        if isinstance(value, str) and value.startswith("@"):
            return json.loads(pathlib.Path(value[1:]).read_text())
        return json.loads(value)

    return click.argument(name, required=required, callback=callback)
