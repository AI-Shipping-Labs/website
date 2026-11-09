"""Keep installed uv-tool copies of ``asl`` on the current main revision."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

GIT_REMOTE = "https://github.com/AI-Shipping-Labs/website.git"
PACKAGE_SOURCE = "git+https://github.com/AI-Shipping-Labs/website.git"
MAIN_REF = "refs/heads/main"
UPDATE_CHECK_TIMEOUT_SECONDS = 10
UPDATE_INSTALL_TIMEOUT_SECONDS = 180


class UpdateError(RuntimeError):
    """Raised when an installed CLI cannot check or install an update."""


def is_uv_tool_environment(prefix: str | Path | None = None) -> bool:
    """Return whether this process runs from uv's installed tool environment."""
    tool_prefix = Path(prefix or sys.prefix).resolve()
    return tool_prefix.name == "asl-cli" and tool_prefix.parent.name == "tools"


def _auto_update_disabled() -> bool:
    """Return whether the operator explicitly disabled automatic updates."""
    disabled_values = {"0", "false", "no", "off"}
    value = os.environ.get("ASL_AUTO_UPDATE", "1").strip().lower()
    return value in disabled_values


def _installed_commit() -> str | None:
    """Return the installed package's Git revision when available."""
    try:
        direct_url = distribution("asl-cli").read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if direct_url is None:
        return None
    try:
        metadata = json.loads(direct_url)
    except ValueError:
        return None
    vcs_info = metadata.get("vcs_info", {})
    return vcs_info.get("commit_id")


def _latest_commit(run=None) -> str:
    """Read the current main revision with a bounded GitHub request."""
    runner = run or subprocess.run
    try:
        result = runner(
            ["git", "ls-remote", GIT_REMOTE, MAIN_REF],
            capture_output=True,
            check=False,
            text=True,
            timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"Update check timed out after {UPDATE_CHECK_TIMEOUT_SECONDS} seconds; "
            "retry or set ASL_AUTO_UPDATE=0."
        ) from exc
    except FileNotFoundError as exc:
        raise UpdateError("Git is required to check for asl CLI updates.") from exc
    except OSError as exc:
        raise UpdateError("Could not run Git to check for asl CLI updates.") from exc
    if result.returncode != 0:
        raise UpdateError("Could not check GitHub for asl updates; verify network and repository access.")
    for line in result.stdout.splitlines():
        revision, separator, ref = line.partition("\t")
        if separator and ref == MAIN_REF and revision:
            return revision
    raise UpdateError("GitHub returned no main revision for the asl CLI update check.")


def _tool_install_environment() -> tuple[dict[str, str], Path]:
    """Keep uv's reinstall in the same tool and executable directories."""
    prefix = Path(sys.prefix).resolve()
    executable = Path(sys.argv[0])
    if not executable.is_absolute():
        located = shutil.which(sys.argv[0]) or shutil.which("asl")
        if located is None:
            raise UpdateError("Could not locate the installed asl executable to restart after updating.")
        executable = Path(located)
    environment = os.environ.copy()
    environment["UV_TOOL_DIR"] = str(prefix.parent)
    environment["UV_TOOL_BIN_DIR"] = str(executable.absolute().parent)
    return environment, executable.absolute()


def _install_latest(revision: str, run=None) -> None:
    """Install the checked revision into the active uv tool environment."""
    runner = run or subprocess.run
    uv = shutil.which("uv")
    if uv is None:
        raise UpdateError("uv is required to install the latest asl CLI.")
    environment, _executable = _tool_install_environment()
    source = f"{PACKAGE_SOURCE}@{revision}#subdirectory=asl_cli"
    package = f"asl-cli @ {source}"
    command = [uv, "tool", "install", "--force", package]
    try:
        result = runner(
            command,
            capture_output=True,
            check=False,
            env=environment,
            text=True,
            timeout=UPDATE_INSTALL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpdateError(
            f"Installing the latest asl CLI timed out after "
            f"{UPDATE_INSTALL_TIMEOUT_SECONDS} seconds."
        ) from exc
    except OSError as exc:
        raise UpdateError("Could not start uv to install the latest asl CLI.") from exc
    if result.returncode != 0:
        raise UpdateError(
            f"Could not install asl from GitHub (uv exited {result.returncode}); "
            "check GitHub access and retry."
        )


def maybe_update() -> bool:
    """Update installed tools when main has moved; skip project environments."""
    if _auto_update_disabled() or not is_uv_tool_environment():
        return False
    current = _installed_commit()
    latest = _latest_commit()
    if current == latest:
        return False
    print(f"asl: updating installed CLI to main revision {latest[:8]}", file=sys.stderr)
    _install_latest(latest)
    return True


def restart_current_process() -> None:
    """Re-exec the installed launcher after uv replaced its environment."""
    _environment, executable = _tool_install_environment()
    try:
        os.execv(str(executable), [str(executable), *sys.argv[1:]])
    except OSError as exc:
        raise UpdateError("The CLI updated, but could not restart the installed asl executable.") from exc
