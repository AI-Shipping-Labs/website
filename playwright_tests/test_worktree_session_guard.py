"""Pure tests for the Playwright same-worktree session guard.

Covers the #1151 guard itself plus the #1470 pytest-xdist isolation that sits on
top of it: worker-id detection, the per-worker SQLite database naming rule, and
the guard-claim decision that lets sibling workers of ONE `pytest -n N`
invocation through while still blocking a genuinely separate second invocation.

No browser, dev server, or real xdist worker is spawned.
"""

import json
import os
from types import SimpleNamespace

import pytest

from playwright_tests import conftest
from playwright_tests.worktree_guard import (
    LOCK_RELATIVE_PATH,
    XDIST_WORKER_ENV_VAR,
    PlaywrightWorktreeGuard,
    WorktreeGuardAlreadyHeld,
    current_xdist_worker_id,
    is_xdist_worker,
)
from website.test_database_guard import is_database_test_scoped

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
    # This test asserts controller/serial behavior, so it must not inherit the
    # ambient PYTEST_XDIST_WORKER when the suite itself runs under `pytest -n`
    # (#1470: workers deliberately skip the claim).
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
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


# ---------------------------------------------------------------------------
# Worker-id detection
# ---------------------------------------------------------------------------


def test_controller_process_has_no_worker_id():
    """No ``PYTEST_XDIST_WORKER`` means controller/serial run, not a worker."""
    assert current_xdist_worker_id(environ={}) is None
    assert is_xdist_worker(environ={}) is False


@pytest.mark.parametrize("raw", ["gw0", "gw1", "gw11"])
def test_worker_process_reports_its_id(raw):
    environ = {XDIST_WORKER_ENV_VAR: raw}
    assert current_xdist_worker_id(environ=environ) == raw
    assert is_xdist_worker(environ=environ) is True


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_worker_env_is_treated_as_controller(raw):
    """A blank value must not produce a bogus ``test_playwright_db_.sqlite3``."""
    environ = {XDIST_WORKER_ENV_VAR: raw}
    assert current_xdist_worker_id(environ=environ) is None


def test_worker_id_is_sanitized_for_filesystem_use():
    """A hostile worker id must never escape the worktree directory."""
    environ = {XDIST_WORKER_ENV_VAR: "../../etc/gw0"}
    worker_id = current_xdist_worker_id(environ=environ)
    assert worker_id == "etcgw0"
    assert "/" not in worker_id
    assert ".." not in worker_id


# ---------------------------------------------------------------------------
# Per-worker database naming
# ---------------------------------------------------------------------------


def test_serial_run_keeps_the_historical_database_name(tmp_path):
    """Serial runs must stay on the #885 per-worktree filename, unsuffixed."""
    name = conftest.playwright_test_database_name(tmp_path, worker_id=None)
    assert name == str(tmp_path / "test_playwright_db.sqlite3")


def test_each_worker_gets_a_distinct_database_file(tmp_path):
    first = conftest.playwright_test_database_name(tmp_path, worker_id="gw0")
    second = conftest.playwright_test_database_name(tmp_path, worker_id="gw1")

    assert first == str(tmp_path / "test_playwright_db_gw0.sqlite3")
    assert second == str(tmp_path / "test_playwright_db_gw1.sqlite3")
    assert first != second


def test_worker_databases_stay_inside_their_own_worktree(tmp_path):
    """Per-worker suffixing must not break #885's per-worktree isolation."""
    worktree_a = tmp_path / "worktree-a"
    worktree_b = tmp_path / "worktree-b"

    name_a = conftest.playwright_test_database_name(worktree_a, worker_id="gw0")
    name_b = conftest.playwright_test_database_name(worktree_b, worker_id="gw0")

    assert name_a.startswith(str(worktree_a))
    assert name_b.startswith(str(worktree_b))
    assert name_a != name_b


def test_worker_database_name_defaults_to_the_ambient_worker(tmp_path, monkeypatch):
    monkeypatch.setenv(XDIST_WORKER_ENV_VAR, "gw3")
    assert conftest.playwright_test_database_name(tmp_path) == str(
        tmp_path / "test_playwright_db_gw3.sqlite3"
    )


def test_ambient_serial_run_gets_the_unsuffixed_name(tmp_path, monkeypatch):
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    assert conftest.playwright_test_database_name(tmp_path) == str(
        tmp_path / "test_playwright_db.sqlite3"
    )


def test_worker_database_name_still_passes_the_unsafe_database_guard(tmp_path):
    """``website.test_database_guard`` must still recognise it as test-owned."""
    name = conftest.playwright_test_database_name(tmp_path, worker_id="gw0")
    assert is_database_test_scoped(
        {"ENGINE": "django.db.backends.sqlite3", "NAME": name},
        base_dir=tmp_path,
    )


def test_db_settings_hook_applies_the_worker_suffix(tmp_path, monkeypatch):
    """The pytest-django hook wires the per-worker name into DATABASES."""
    monkeypatch.setenv(XDIST_WORKER_ENV_VAR, "gw2")
    database_settings = {"ENGINE": "django.db.backends.sqlite3"}

    conftest.apply_playwright_test_database(database_settings, tmp_path)

    assert database_settings["TEST"]["NAME"] == str(
        tmp_path / "test_playwright_db_gw2.sqlite3"
    )


def test_db_settings_hook_overrides_an_earlier_test_name(tmp_path):
    """Pre-#1470 precedence: the Playwright name wins over DJANGO_TEST_DB_NAME.

    Two parallel workers must never both inherit one pinned unittest-runner
    database, so the per-worker name has to be authoritative here.
    """
    database_settings = {
        "ENGINE": "django.db.backends.sqlite3",
        "TEST": {"NAME": str(tmp_path / "pinned_test_db.sqlite3")},
    }

    conftest.apply_playwright_test_database(database_settings, tmp_path, worker_id="gw2")

    assert database_settings["TEST"]["NAME"] == str(
        tmp_path / "test_playwright_db_gw2.sqlite3"
    )


def test_db_settings_hook_leaves_non_sqlite_engines_alone(tmp_path):
    """Postgres runs keep pytest-django's own test database handling."""
    database_settings = {"ENGINE": "django.db.backends.postgresql", "NAME": "aisl"}

    conftest.apply_playwright_test_database(database_settings, tmp_path, worker_id="gw0")

    assert "TEST" not in database_settings


# ---------------------------------------------------------------------------
# Guard-claim decision under xdist
# ---------------------------------------------------------------------------


def test_xdist_worker_does_not_claim_the_worktree_guard(monkeypatch):
    """Workers must skip the claim their own controller already made."""

    class GuardShouldNotBeUsed:
        @classmethod
        def for_current_worktree(cls):
            raise AssertionError("xdist workers must not claim the worktree guard")

    monkeypatch.setenv(XDIST_WORKER_ENV_VAR, "gw0")
    monkeypatch.setattr(conftest, "_resolved_base_url", lambda: "http://127.0.0.1:8123")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)
    monkeypatch.setattr(conftest, "PlaywrightWorktreeGuard", GuardShouldNotBeUsed)

    config = SimpleNamespace()
    assert conftest._claim_playwright_worktree_guard(config) is None
    assert not hasattr(config, "_playwright_worktree_guard")


def test_xdist_controller_still_claims_the_worktree_guard(monkeypatch):
    """``pytest -n 4`` still takes the lock once, in the controller."""
    events = []

    class FakeGuard:
        @classmethod
        def for_current_worktree(cls):
            return cls()

        def acquire(self):
            events.append("acquire")

    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    monkeypatch.setattr(conftest, "_resolved_base_url", lambda: "http://127.0.0.1:8123")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)
    monkeypatch.setattr(conftest, "PlaywrightWorktreeGuard", FakeGuard)

    config = SimpleNamespace()
    guard = conftest._claim_playwright_worktree_guard(config)

    assert isinstance(guard, FakeGuard)
    assert events == ["acquire"]


def test_second_separate_invocation_is_still_blocked(tmp_path, monkeypatch):
    """A separate controller in the same worktree must still fail fast.

    This is the behavior #1151 shipped and #1470 must not weaken: only the
    *worker* path is exempt, and a second real invocation never has
    ``PYTEST_XDIST_WORKER`` set in its controller.
    """
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    holder = PlaywrightWorktreeGuard(tmp_path).acquire()
    monkeypatch.setattr(
        conftest,
        "PlaywrightWorktreeGuard",
        SimpleNamespace(for_current_worktree=lambda: PlaywrightWorktreeGuard(tmp_path)),
    )
    monkeypatch.setattr(conftest, "_resolved_base_url", lambda: "http://127.0.0.1:8123")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)

    try:
        with pytest.raises(pytest.exit.Exception) as exc:
            conftest._claim_playwright_worktree_guard(SimpleNamespace())
    finally:
        holder.release()

    message = str(exc.value)
    assert "Another Playwright session is already using this worktree." in message
    assert f"holder PID: {os.getpid()}" in message


# ---------------------------------------------------------------------------
# Pinned port + parallelism is rejected at configure time
# ---------------------------------------------------------------------------


def _config(numprocesses):
    return SimpleNamespace(option=SimpleNamespace(numprocesses=numprocesses))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 0), (0, 0), (1, 1), (4, 4), ("4", 4), (-2, 0), ("auto", 0)],
)
def test_requested_worker_count_is_read_from_the_n_option(raw, expected):
    assert conftest.requested_xdist_worker_count(_config(raw)) == expected


def test_pinned_port_with_parallelism_fails_fast(monkeypatch):
    """One pinned port cannot serve N workers, so reject the combination."""
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "8765")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)

    with pytest.raises(pytest.UsageError) as exc:
        conftest._assert_pinned_port_is_not_parallel(_config(4))

    message = str(exc.value)
    assert "PLAYWRIGHT_DJANGO_PORT cannot be combined" in message
    assert "-n 4" in message
    assert "PLAYWRIGHT_XDIST_WORKERS=0" in message


def test_pinned_port_is_allowed_without_parallelism(monkeypatch):
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "8765")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)

    assert conftest._assert_pinned_port_is_not_parallel(_config(0)) is None
    assert conftest._assert_pinned_port_is_not_parallel(_config(None)) is None


def test_parallelism_is_allowed_without_a_pinned_port(monkeypatch):
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    monkeypatch.delenv("PLAYWRIGHT_DJANGO_PORT", raising=False)
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)

    assert conftest._assert_pinned_port_is_not_parallel(_config(4)) is None


@pytest.mark.parametrize("raw", ["0", "garbage", "  "])
def test_invalid_pinned_port_does_not_block_parallelism(monkeypatch, raw):
    """Values that already fall back to a free port are not a real pin (#911)."""
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", raw)
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)

    assert conftest._assert_pinned_port_is_not_parallel(_config(4)) is None


def test_remote_base_url_ignores_the_pinned_port_check(monkeypatch):
    """No local server is started against dev/prod, so no port is bound."""
    monkeypatch.delenv(XDIST_WORKER_ENV_VAR, raising=False)
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "8765")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: False)

    assert conftest._assert_pinned_port_is_not_parallel(_config(4)) is None


def test_worker_process_does_not_re_run_the_pinned_port_check(monkeypatch):
    """Workers inherit the env; the controller already rejected bad combos."""
    monkeypatch.setenv(XDIST_WORKER_ENV_VAR, "gw0")
    monkeypatch.setenv("PLAYWRIGHT_DJANGO_PORT", "8765")
    monkeypatch.setattr(conftest, "_base_url_is_local", lambda url: True)

    assert conftest._assert_pinned_port_is_not_parallel(_config(4)) is None


# ---------------------------------------------------------------------------
# Cross-test IntegrationSetting cache isolation (#1470)
# ---------------------------------------------------------------------------
#
# These two run in declaration order inside one file, and `--dist loadfile`
# keeps a file on a single worker, so the pair is a valid ordered assertion
# under xdist as well as serially. The first poisons the process-wide config
# cache exactly the way a real module does (write a DB override, warm the
# cache, then let the row disappear); the second proves the autouse
# `_reset_integration_config_cache` fixture in conftest wiped it. Without that
# fixture the second test fails, which is the regression that made
# test_workshop_copy_file.py and test_plan_sprints_ingest_889.py
# order-dependent.

_CACHE_POISON_KEY = "AISL_1470_CACHE_POISON_PROBE"


@pytest.mark.django_db
def test_config_cache_poison_does_not_survive_into_the_next_test():
    """``django_db`` because ``get_config`` reads the DB-backed stamp cache."""
    from integrations import config as integration_config

    integration_config._cache[_CACHE_POISON_KEY] = "poison"
    integration_config._cache_populated = True
    assert integration_config.get_config(_CACHE_POISON_KEY, "clean") == "poison"


@pytest.mark.django_db
def test_next_test_sees_a_clean_config_cache():
    """``django_db`` because a cleared cache repopulates from the database.

    That repopulate is the point: the reset must not merely drop the key, it
    must send the next lookup back to the real rows.
    """
    from integrations import config as integration_config

    assert _CACHE_POISON_KEY not in integration_config._cache
    assert integration_config.get_config(_CACHE_POISON_KEY, "clean") == "clean"
