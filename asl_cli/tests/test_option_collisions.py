"""Ratchet: no leaf command may declare the same option string twice (#1733).

Click 8.4 does not reject a duplicate option string. It emits a ``UserWarning``
and lets the last parser entry win, which silently makes one of the two options
unreachable. This walk keeps that failure mode out of the shipped CLI.
"""

from __future__ import annotations

import warnings

import click
import pytest
from asl_cli.cli import cli
from click.testing import CliRunner


def _iter_leaf_commands(command, path=()):
    if isinstance(command, click.Group):
        for name, sub in command.commands.items():
            yield from _iter_leaf_commands(sub, (*path, name))
    else:
        yield path, command


LEAF_COMMANDS = sorted(_iter_leaf_commands(cli), key=lambda item: item[0])
LEAF_IDS = [" ".join(path) for path, _command in LEAF_COMMANDS]


def test_the_walk_actually_reaches_the_command_tree():
    # Guards the two parametrized tests below from passing on an empty walk.
    assert len(LEAF_COMMANDS) > 50
    assert "contacts export" in LEAF_IDS
    assert "users list" in LEAF_IDS


@pytest.mark.parametrize(("path", "command"), LEAF_COMMANDS, ids=LEAF_IDS)
def test_no_leaf_command_claims_an_option_string_twice(path, command):
    owners: dict[str, str] = {}
    for param in command.params:
        for opt in [*param.opts, *param.secondary_opts]:
            assert opt not in owners, (
                f"'asl {' '.join(path)}' declares {opt} on both "
                f"'{owners[opt]}' and '{param.name}'; Click keeps only one"
            )
            owners[opt] = param.name


@pytest.mark.parametrize(("path", "command"), LEAF_COMMANDS, ids=LEAF_IDS)
def test_leaf_command_help_raises_no_user_warning(path, command):
    runner = CliRunner()
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        result = runner.invoke(cli, [*path, "--help"], catch_exceptions=False)
    assert result.exit_code == 0, result.output
