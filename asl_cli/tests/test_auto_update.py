"""Version-check and self-update behavior for installed ``asl`` tools."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from asl_cli import auto_update
from asl_cli import cli as cli_module

MAIN_SHA = "a" * 40


def test_only_uv_tool_installations_are_auto_updated():
    tool_prefix = Path("/home/alexey/.local/share/uv/tools/asl-cli")

    assert auto_update.is_uv_tool_environment(tool_prefix)
    assert not auto_update.is_uv_tool_environment("/repo/.venv")


def test_auto_update_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("ASL_AUTO_UPDATE", "off")

    assert auto_update._auto_update_disabled()


def test_latest_commit_reads_main_with_a_timeout():
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=f"{MAIN_SHA}\t{auto_update.MAIN_REF}\n",
        )

    assert auto_update._latest_commit(run=fake_run) == MAIN_SHA
    assert calls[0][0] == ["git", "ls-remote", auto_update.GIT_REMOTE, auto_update.MAIN_REF]
    assert calls[0][1]["timeout"] == auto_update.UPDATE_CHECK_TIMEOUT_SECONDS


def test_latest_commit_reports_timeout_clearly():
    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("git ls-remote", 10)

    with pytest.raises(auto_update.UpdateError, match="timed out after 10 seconds"):
        auto_update._latest_commit(run=timed_out)


def test_latest_commit_rejects_a_missing_main_ref():
    def missing_ref(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="")

    with pytest.raises(auto_update.UpdateError, match="no main revision"):
        auto_update._latest_commit(run=missing_ref)


def test_matching_revision_skips_reinstall(monkeypatch):
    monkeypatch.setattr(auto_update, "is_uv_tool_environment", lambda: True)
    monkeypatch.setattr(auto_update, "_installed_commit", lambda: MAIN_SHA)
    monkeypatch.setattr(auto_update, "_latest_commit", lambda: MAIN_SHA)
    monkeypatch.setattr(
        auto_update,
        "_install_latest",
        lambda _revision: pytest.fail("matching revision should not reinstall"),
    )

    assert not auto_update.maybe_update()


def test_changed_revision_installs_and_requests_restart(monkeypatch):
    monkeypatch.setattr(auto_update, "is_uv_tool_environment", lambda: True)
    monkeypatch.setattr(auto_update, "_installed_commit", lambda: "b" * 40)
    monkeypatch.setattr(auto_update, "_latest_commit", lambda: MAIN_SHA)
    installed = []
    monkeypatch.setattr(auto_update, "_install_latest", installed.append)

    assert auto_update.maybe_update()
    assert installed == [MAIN_SHA]


def test_auto_update_skips_project_environment(monkeypatch):
    monkeypatch.setattr(auto_update, "is_uv_tool_environment", lambda: False)
    monkeypatch.setattr(
        auto_update,
        "_latest_commit",
        lambda: pytest.fail("project environments should not make network calls"),
    )

    assert not auto_update.maybe_update()


def test_main_restarts_before_running_command_when_updated(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_module, "maybe_update", lambda: calls.append("check") or True)
    monkeypatch.setattr(cli_module, "restart_current_process", lambda: calls.append("restart"))
    monkeypatch.setattr(cli_module, "cli", lambda: calls.append("command"))

    cli_module.main()

    assert calls == ["check", "restart", "command"]


def test_main_reports_update_failures_without_traceback(monkeypatch, capsys):
    def fail_update():
        raise auto_update.UpdateError("Could not check GitHub for asl updates.")

    monkeypatch.setattr(cli_module, "maybe_update", fail_update)

    with pytest.raises(SystemExit) as exit_info:
        cli_module.main()

    captured = capsys.readouterr()
    assert exit_info.value.code == 1
    assert "Error: Could not check GitHub" in captured.err
    assert "Traceback" not in captured.err


def test_install_pins_the_checked_revision_and_preserves_tool_paths(monkeypatch):
    calls = []
    environment = {"UV_TOOL_DIR": "/tools", "UV_TOOL_BIN_DIR": "/bin"}
    monkeypatch.setattr(auto_update.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        auto_update,
        "_tool_install_environment",
        lambda: (environment, Path("/bin/asl")),
    )

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    auto_update._install_latest(MAIN_SHA, run=fake_run)

    command, kwargs = calls[0]
    assert command[:4] == ["/usr/bin/uv", "tool", "install", "--force"]
    assert command[4].startswith("asl-cli @ git+https://")
    assert f"@{MAIN_SHA}#subdirectory=asl_cli" in command[4]
    assert kwargs["env"] is environment
    assert kwargs["timeout"] == auto_update.UPDATE_INSTALL_TIMEOUT_SECONDS


def test_install_timeout_is_reported(monkeypatch):
    monkeypatch.setattr(auto_update.shutil, "which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        auto_update,
        "_tool_install_environment",
        lambda: ({}, Path("/bin/asl")),
    )

    def timed_out(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("uv tool install", 180)

    with pytest.raises(auto_update.UpdateError, match="timed out after 180 seconds"):
        auto_update._install_latest(MAIN_SHA, run=timed_out)
