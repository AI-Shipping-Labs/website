#!/usr/bin/env python3
"""Run django-q in dev and restart it when Python source files change."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

ROOT = Path(__file__).resolve().parents[1]
WATCHED_SUFFIXES = {".py"}
IGNORED_DIR_NAMES = {
    ".claude",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
RESTART_DEBOUNCE_SECONDS = 0.5
POLL_INTERVAL_SECONDS = 0.2


def _iter_event_paths(event):
    """Yield every filesystem path referenced by a watchdog event."""
    src_path = getattr(event, "src_path", None)
    if src_path:
        yield Path(src_path)

    dest_path = getattr(event, "dest_path", None)
    if dest_path:
        yield Path(dest_path)


def _should_watch(path):
    """Return True if a path should trigger a worker restart."""
    try:
        relative = path.resolve().relative_to(ROOT)
    except Exception:
        return False

    if any(part in IGNORED_DIR_NAMES for part in relative.parts):
        return False
    return path.suffix in WATCHED_SUFFIXES


class RestartOnChangeHandler(FileSystemEventHandler):
    """Track whether a relevant filesystem event occurred recently."""

    def __init__(self):
        self._restart_requested_at = None
        self._last_trigger = None

    def on_any_event(self, event):
        if event.is_directory:
            return

        for path in _iter_event_paths(event):
            if _should_watch(path):
                self._restart_requested_at = time.monotonic()
                self._last_trigger = (event.event_type, str(path))
                return

    def consume_trigger(self):
        """Return and clear the event that requested the pending restart."""
        trigger = self._last_trigger
        self._last_trigger = None
        return trigger

    def ready_to_restart(self):
        """Return True once the debounce window has elapsed."""
        if self._restart_requested_at is None:
            return False
        return (
            time.monotonic() - self._restart_requested_at
            >= RESTART_DEBOUNCE_SECONDS
        )

    def clear(self):
        """Reset the pending-restart marker."""
        self._restart_requested_at = None


def _start_worker():
    """Spawn a qcluster process in its own process group."""
    command = [sys.executable, "manage.py", "qcluster"]
    print("[dev-qcluster] starting worker:", " ".join(command), flush=True)
    return subprocess.Popen(
        command,
        cwd=ROOT,
        start_new_session=True,
    )


def _stop_worker(process):
    """Terminate the qcluster process group cleanly, then force-kill if needed."""
    if process is None or process.poll() is not None:
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _process_snapshot():
    """Return a mapping of ``{pid: ppid}`` for the current process table."""
    output = subprocess.check_output(
        ["ps", "-eo", "pid=", "-o", "ppid="],
        text=True,
        cwd=ROOT,
    )
    tree = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_str, ppid_str = stripped.split(None, 1)
        tree[int(pid_str)] = int(ppid_str)
    return tree


def _descendant_pids(root_pid):
    """Return every descendant PID for ``root_pid``."""
    tree = _process_snapshot()
    descendants = []
    frontier = [root_pid]

    while frontier:
        parent = frontier.pop()
        children = [pid for pid, ppid in tree.items() if ppid == parent]
        descendants.extend(children)
        frontier.extend(children)

    return descendants


def _process_command(pid):
    """Return the current command line for ``pid``."""
    result = subprocess.run(
        ["ps", "-o", "args=", "-p", str(pid)],
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _worker_is_busy(process):
    """Return True if any django-q worker subprocess is currently executing a task."""
    if process is None or process.poll() is not None:
        return False

    for pid in _descendant_pids(process.pid):
        command = _process_command(pid)
        if command.startswith("qcluster ") and " processing " in command:
            return True
    return False


def main():
    """Start the worker and restart it whenever watched files change."""
    handler = RestartOnChangeHandler()
    observer = Observer()
    observer.schedule(handler, str(ROOT), recursive=True)
    observer.start()

    worker = _start_worker()
    stop_requested = False
    waiting_for_idle_log = False

    def _handle_signal(signum, _frame):
        nonlocal stop_requested
        stop_requested = True
        print(f"[dev-qcluster] received signal {signum}, shutting down", flush=True)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not stop_requested:
            if worker.poll() is not None:
                if stop_requested:
                    break
                print(
                    "[dev-qcluster] worker exited; restarting in dev mode",
                    flush=True,
                )
                worker = _start_worker()
                handler.clear()
                time.sleep(0.5)
                continue

            if handler.ready_to_restart():
                if _worker_is_busy(worker):
                    if not waiting_for_idle_log:
                        print(
                            "[dev-qcluster] Python change detected; "
                            "waiting for current task to finish before restart",
                            flush=True,
                        )
                        waiting_for_idle_log = True
                else:
                    trigger = handler.consume_trigger()
                    trigger_detail = (
                        f" ({trigger[0]}: {trigger[1]})" if trigger else ""
                    )
                    print(
                        f"[dev-qcluster] Python change detected; restarting worker{trigger_detail}",
                        flush=True,
                    )
                    _stop_worker(worker)
                    worker = _start_worker()
                    handler.clear()
                    waiting_for_idle_log = False

            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        observer.stop()
        observer.join()
        _stop_worker(worker)


if __name__ == "__main__":
    main()
