"""CLI entry point and shared option handling.

Defines the root ``asl`` command group and common options (``--format``,
``--base-url``). Subcommands are registered from the ``commands`` package.
"""

from __future__ import annotations

import sys

import click

from asl_cli import __version__
from asl_cli.client import APIError
from asl_cli.commands import (
    campaigns,
    contacts,
    event_series,
    events,
    integrations,
    member_api,
    misc,
    onboarding,
    plans,
    raw,
    redirects,
    sprints,
    sync,
    triggers,
    users,
    utm_campaigns,
    worker,
)


FORMAT_CHOICES = ["json", "table", "raw"]


class AslGroup(click.Group):
    """Custom group that pretty-prints the help text."""

    def format_help(self, ctx, formatter):
        formatter.write_heading("asl -- AI Shipping Labs CLI")
        formatter.write_paragraph()
        formatter.write_text(
            "Command-line client for the production API "
            "(https://aishippinglabs.com/api). "
            "All commands output JSON unless --format=table or --format=raw."
        )
        super().format_help(ctx, formatter)


def handle_api_error(func):
    """Decorate a command function to print APIError as a clean message."""

    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except APIError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    return wrapper


@click.group(cls=AslGroup)
@click.version_option(__version__, prog_name="asl")
def cli():
    """AI Shipping Labs production API CLI."""


# Register command groups
for _module in [
    users,
    events,
    event_series,
    plans,
    sprints,
    contacts,
    campaigns,
    integrations,
    sync,
    worker,
    triggers,
    onboarding,
    redirects,
    utm_campaigns,
    member_api,
    misc,
    raw,
]:
    for _cmd in _module.commands:
        cli.add_command(_cmd)


def main():
    cli()


if __name__ == "__main__":
    main()
