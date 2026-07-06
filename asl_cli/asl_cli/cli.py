"""CLI entry point.

Defines the root ``asl`` command group and registers all subgroups
from the commands package.
"""

from __future__ import annotations

import sys

import click

from asl_cli import __version__
from asl_cli.client import APIError
from asl_cli.commands import groups


class AslGroup(click.Group):
    """Custom group with a short header above the standard help."""

    def format_help(self, ctx, formatter):
        formatter.write_heading("asl -- AI Shipping Labs CLI")
        formatter.write_paragraph()
        formatter.write_text(
            "Command-line client for the production API "
            "(https://aishippinglabs.com/api). "
            "Run 'asl <group> --help' to see subcommands, "
            "and 'asl <group> <command> --help' for flags."
        )
        super().format_help(ctx, formatter)


@click.group(cls=AslGroup)
@click.version_option(__version__, prog_name="asl")
def cli():
    """AI Shipping Labs production API CLI."""


for _group in groups:
    cli.add_command(_group)


def main():
    try:
        cli()
    except APIError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
