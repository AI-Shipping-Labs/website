"""CLI wiring tests via Click's ``CliRunner`` (in-process, no HTTP)."""

from __future__ import annotations

import click
import pytest
from asl_cli.cli import cli
from asl_cli.commands import groups
from asl_cli.commands._shared import TIER_LEVELS, TierLevel
from click.testing import CliRunner

# Pinned contract: every command group that must stay registered. A removed
# or renamed group makes ``test_expected_groups_are_registered`` fail before
# the parametrized help guard even runs.
EXPECTED_GROUPS = {
    "articles",
    "campaigns",
    "cleanup-gates",
    "contacts",
    "crm-export",
    "event-series",
    "events",
    "hosts",
    "integrations",
    "onboarding",
    "openapi",
    "plans",
    "raw",
    "redirects",
    "ses-events",
    "sprints",
    "sync",
    "tier-overrides",
    "tier-reconcile",
    "triggers",
    "users",
    "utm-campaigns",
    "worker",
}

GROUP_NAMES = sorted(g.name for g in groups)


def test_help_exits_zero():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "asl" in result.output


def test_version_exits_zero():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "asl" in result.output


def test_expected_groups_are_registered():
    assert {g.name for g in groups} == EXPECTED_GROUPS


@pytest.mark.parametrize("group", GROUP_NAMES)
def test_every_group_help_exits_zero(group):
    result = CliRunner().invoke(cli, [group, "--help"])
    assert result.exit_code == 0, result.output


def test_raw_help_documents_method_path_escape_hatch():
    result = CliRunner().invoke(cli, ["raw", "--help"])
    assert result.exit_code == 0
    # Usage shows the METHOD choices followed by the PATH argument.
    assert "PATH" in result.output
    assert "get|post|patch|put|delete" in result.output.lower()
    assert "/api/events" in result.output


# --- Tier-name parsing (used by --required-level / --target-min-level) ---


@pytest.mark.parametrize(
    "name,expected",
    [
        ("open", 0),
        ("registered", 5),
        ("basic", 10),
        ("main", 20),
        ("premium", 30),
    ],
)
def test_tier_names_map_to_levels(name, expected):
    assert TierLevel().convert(name, None, None) == expected
    # The mapping table stays the single source of truth.
    assert TIER_LEVELS[name] == expected


def test_tier_name_parsing_is_case_insensitive():
    assert TierLevel().convert("PREMIUM", None, None) == 30


@pytest.mark.parametrize("raw", ["0", "5", "10", "20", "30", "15"])
def test_tier_accepts_raw_integers(raw):
    assert TierLevel().convert(raw, None, None) == int(raw)


def test_tier_passthrough_of_none_and_int():
    assert TierLevel().convert(None, None, None) is None
    assert TierLevel().convert(25, None, None) == 25


def test_tier_rejects_unknown_value():
    with pytest.raises(click.exceptions.UsageError):
        TierLevel().convert("nonsense", None, None)
