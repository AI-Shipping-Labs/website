"""Worktree-scoped lock for local Playwright pytest sessions."""

from __future__ import annotations

import atexit
import fcntl
import json
import os
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LOCK_RELATIVE_PATH = Path(".tmp") / "playwright-session.lock"
MAX_COMMAND_ARGS = 12
MAX_COMMAND_LENGTH = 240
SECRET_ARG_HINTS = (
    "api_key",
    "apikey",
    "auth",
    "credential",
    "database_url",
    "db_url",
    "password",
    "passwd",
    "secret",
    "token",
)
SENSITIVE_URL_SCHEMES = ("mysql://", "postgres://", "postgresql://", "redis://", "sqlite://")


class WorktreeGuardAlreadyHeld(RuntimeError):
    """Raised when another Playwright pytest session holds the worktree lock."""


def current_git_worktree_root(cwd=None):
    """Return the current git worktree root, falling back to the repo root."""
    cwd = Path(cwd or Path.cwd()).resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return Path(__file__).resolve().parents[1]

    root = result.stdout.strip()
    if not root:
        return Path(__file__).resolve().parents[1]
    return Path(root).resolve()


def _redact_key_value(value):
    if "=" not in value:
        return "<redacted>"
    key, _separator, _raw = value.partition("=")
    return f"{key}=<redacted>"


def _redact_url_credentials(value):
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    if not parsed.scheme or not parsed.netloc:
        return value

    try:
        host = parsed.hostname or ""
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
    except ValueError:
        return "<redacted-url>"

    netloc = host
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{host}"

    query = parsed.query
    if any(hint in query.lower() for hint in SECRET_ARG_HINTS):
        query = "<redacted>"

    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def _sanitize_command_arg(arg):
    arg = str(arg)
    lower = arg.lower()
    if any(lower.startswith(scheme) for scheme in SENSITIVE_URL_SCHEMES):
        return "<redacted-url>"
    if any(hint in lower for hint in SECRET_ARG_HINTS):
        return _redact_key_value(arg)
    return _redact_url_credentials(arg)


def _current_command(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if not argv:
        return "unknown"

    command = [Path(argv[0]).name or str(argv[0])]
    command.extend(_sanitize_command_arg(arg) for arg in argv[1 : MAX_COMMAND_ARGS + 1])
    if len(argv) > MAX_COMMAND_ARGS + 1:
        command.append("...")

    rendered = " ".join(command)
    if len(rendered) > MAX_COMMAND_LENGTH:
        return rendered[: MAX_COMMAND_LENGTH - 4].rstrip() + " ..."
    return rendered


def _sanitize_recorded_command(command):
    try:
        parts = shlex.split(str(command))
    except ValueError:
        parts = str(command).split()
    if not parts:
        return ""

    sanitized = [_sanitize_command_arg(part) for part in parts[: MAX_COMMAND_ARGS + 1]]
    if len(parts) > MAX_COMMAND_ARGS + 1:
        sanitized.append("...")

    rendered = " ".join(sanitized)
    if len(rendered) > MAX_COMMAND_LENGTH:
        return rendered[: MAX_COMMAND_LENGTH - 4].rstrip() + " ..."
    return rendered


def _process_start_id(pid):
    """Return a Linux process start identifier when available."""
    try:
        raw_stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        after_command = raw_stat.rsplit(") ", 1)[1].split()
    except (OSError, IndexError):
        return None
    if len(after_command) <= 19:
        return None
    return after_command[19]


def _read_metadata(lock_file):
    try:
        lock_file.seek(0)
        raw = lock_file.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        metadata = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(metadata, dict):
        return {}
    return metadata


def _format_holder(metadata):
    if not metadata:
        return "holder details unavailable"

    details = []
    if metadata.get("pid"):
        details.append(f"holder PID: {metadata['pid']}")
    if metadata.get("command"):
        details.append(f"command: {_sanitize_recorded_command(metadata['command'])!r}")
    if metadata.get("claimed_at"):
        details.append(f"claimed at: {metadata['claimed_at']}")
    if metadata.get("process_start_id"):
        details.append(f"process start id: {metadata['process_start_id']}")
    return "; ".join(details) or "holder details unavailable"


def _conflict_message(*, worktree_root, current_pid, holder_metadata):
    holder = _format_holder(holder_metadata)
    return (
        "Another Playwright session is already using this worktree.\n\n"
        f"Worktree: {worktree_root}\n"
        f"Current PID: {current_pid}\n"
        f"Holder: {holder}\n\n"
        "Remediation: wait for the other run to finish, stop it if it is stuck, "
        "or run this command from a separate git worktree.\n"
        "This guard is worktree-scoped and protects the local "
        "test_playwright_db.sqlite3 database."
    )


class PlaywrightWorktreeGuard:
    """Advisory lock held for one local Playwright pytest session."""

    def __init__(self, worktree_root):
        self.worktree_root = Path(worktree_root).resolve()
        self.lock_path = self.worktree_root / LOCK_RELATIVE_PATH
        self.token = uuid.uuid4().hex
        self._file = None

    @classmethod
    def for_current_worktree(cls, cwd=None):
        return cls(current_git_worktree_root(cwd=cwd))

    def acquire(self):
        """Acquire the worktree lock or fail fast with holder details."""
        if self._file is not None:
            return self

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder_metadata = _read_metadata(lock_file)
            lock_file.close()
            raise WorktreeGuardAlreadyHeld(
                _conflict_message(
                    worktree_root=self.worktree_root,
                    current_pid=os.getpid(),
                    holder_metadata=holder_metadata,
                )
            ) from exc

        try:
            self._write_metadata(lock_file)
        except Exception:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            raise

        self._file = lock_file
        atexit.register(self.release)
        return self

    def release(self):
        """Release the lock and remove our metadata when still owned by us."""
        if self._file is None:
            return

        lock_file = self._file
        self._file = None
        try:
            metadata = _read_metadata(lock_file)
            if metadata.get("token") == self.token and self._lock_path_points_to_open_file(lock_file):
                try:
                    self.lock_path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    def _write_metadata(self, lock_file):
        metadata = {
            "command": _current_command(),
            "claimed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "process_start_id": _process_start_id(os.getpid()),
            "token": self.token,
            "worktree": str(self.worktree_root),
        }
        lock_file.seek(0)
        lock_file.truncate()
        json.dump(metadata, lock_file, sort_keys=True)
        lock_file.write("\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())

    def _lock_path_points_to_open_file(self, lock_file):
        try:
            path_stat = self.lock_path.stat()
            file_stat = os.fstat(lock_file.fileno())
        except OSError:
            return False
        return path_stat.st_ino == file_stat.st_ino and path_stat.st_dev == file_stat.st_dev
