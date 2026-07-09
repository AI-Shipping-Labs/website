"""Pure tests for the Playwright same-worktree session guard."""

import json
import os
from types import SimpleNamespace

import pytest

from playwright_tests import conftest
from playwright_tests.worktree_guard import LOCK_RELATIVE_PATH, PlaywrightWorktreeGuard, WorktreeGuardAlreadyHeld

pytestmark = pytest.mark.core


def _lock_metadata(worktree_root):
    return json.loads((worktree_root / LOCK_RELATIVE_PATH).read_text(encoding="utf-8"))


def test_same_worktree_conflict_fails_fast_with_holder_details(tmp_path):
    guard = PlaywrightWorktreeGuard(tmp_path).acquire()
    try:
        with pytest.raises(WorktreeGuardAlreadyHeld) as exc:
            PlaywrightWorktreeGuard(tmp_path).acquire()
    finally:
        guard.release()

    message = str(exc.value)
    assert "Another Playwright session is already using this worktree." in message
    assert f"Worktree: {tmp_path.resolve()}" in message
    assert f"Current PID: {os.getpid()}" in message
    assert f"holder PID: {os.getpid()}" in message
    assert "command:" in message
    assert "claimed at:" in message
    assert "wait for the other run to finish" in message
    assert "stop it if it is stuck" in message
    assert "separate git worktree" in message
    assert "test_playwright_db.sqlite3" in message


def test_conflict_message_sanitizes_recorded_holder_command(tmp_path):
    guard = PlaywrightWorktreeGuard(tmp_path).acquire()
    try:
        (tmp_path / LOCK_RELATIVE_PATH).write_text(
            json.dumps(
                {
                    "claimed_at": "2026-07-09T00:00:00+00:00",
                    "command": "pytest --token=secret postgresql://user:pass@db.example/app",
                    "pid": os.getpid(),
                    "token": guard.token,
                    "worktree": str(tmp_path),
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(WorktreeGuardAlreadyHeld) as exc:
            PlaywrightWorktreeGuard(tmp_path).acquire()
    finally:
        guard.release()

    message = str(exc.value)
    assert "secret" not in message
    assert "user:pass" not in message
    assert "--token=<redacted>" in message
    assert "<redacted-url>" in message


def test_separate_worktree_roots_are_allowed_concurrently(tmp_path):
    guard_a = PlaywrightWorktreeGuard(tmp_path / "worktree-a").acquire()
    guard_b = PlaywrightWorktreeGuard(tmp_path / "worktree-b").acquire()
    try:
        assert guard_a.lock_path != guard_b.lock_path
        assert guard_a.lock_path.exists()
        assert guard_b.lock_path.exists()
    finally:
        guard_b.release()
        guard_a.release()


def test_release_allows_retry_in_same_worktree(tmp_path):
    first = PlaywrightWorktreeGuard(tmp_path).acquire()
    first.release()

    second = PlaywrightWorktreeGuard(tmp_path).acquire()
    try:
        metadata = _lock_metadata(tmp_path)
        assert metadata["pid"] == os.getpid()
        assert metadata["token"] == second.token
    finally:
        second.release()


def test_dead_holder_metadata_does_not_block_future_session(tmp_path):
    lock_path = tmp_path / LOCK_RELATIVE_PATH
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        json.dumps(
            {
                "command": "pytest old-run",
                "pid": 999999999,
                "token": "stale",
                "worktree": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    guard = PlaywrightWorktreeGuard(tmp_path).acquire()
    try:
        metadata = _lock_metadata(tmp_path)
        assert metadata["pid"] == os.getpid()
        assert metadata["token"] == guard.token
        assert metadata["worktree"] == str(tmp_path.resolve())
    finally:
        guard.release()


def test_sessionstart_claims_guard_for_local_direct_pytest(monkeypatch):
    events = []

    class FakeGuard:
        @classmethod
        def for_current_worktree(cls):
            return cls()

        def acquire(self):
            events.append("acquire")

    monkeypatch.setattr(conftest, "_resolved_base_url", lambda: "http://127.0.0.1:8123")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)
    monkeypatch.setattr(conftest, "PlaywrightWorktreeGuard", FakeGuard)

    config = SimpleNamespace()
    guard = conftest._claim_playwright_worktree_guard(config)

    assert isinstance(guard, FakeGuard)
    assert config._playwright_worktree_guard is guard
    assert events == ["acquire"]


def test_non_local_playwright_base_url_does_not_claim_guard(monkeypatch):
    class GuardShouldNotBeUsed:
        @classmethod
        def for_current_worktree(cls):
            raise AssertionError("remote Playwright sessions must not claim the local guard")

    monkeypatch.setattr(conftest, "_resolved_base_url", lambda: "https://dev.aishippinglabs.com")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: False)
    monkeypatch.setattr(conftest, "PlaywrightWorktreeGuard", GuardShouldNotBeUsed)

    config = SimpleNamespace()
    assert conftest._claim_playwright_worktree_guard(config) is None
    assert not hasattr(config, "_playwright_worktree_guard")


def test_sessionfinish_releases_and_clears_guard():
    events = []

    class FakeGuard:
        def release(self):
            events.append("release")

    config = SimpleNamespace(_playwright_worktree_guard=FakeGuard())
    conftest._release_playwright_worktree_guard(config)

    assert events == ["release"]
    assert not hasattr(config, "_playwright_worktree_guard")
