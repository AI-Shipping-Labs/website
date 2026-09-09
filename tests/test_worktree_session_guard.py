"""Native policy tests for the Playwright same-worktree session guard.

These tests exercise harness policy without launching a browser, dev server,
or real xdist worker. Each filesystem case owns a temporary worktree root and
asserts that its lock metadata and database artifacts are gone at teardown.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from django.test import SimpleTestCase, TestCase

from integrations import config as integration_config
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


def _lock_metadata(worktree_root: Path) -> dict:
    return json.loads(
        (worktree_root / LOCK_RELATIVE_PATH).read_text(encoding="utf-8")
    )


def _config(numprocesses):
    return SimpleNamespace(option=SimpleNamespace(numprocesses=numprocesses))


class TemporaryWorktreePolicyTests(SimpleTestCase):
    """Give each policy node an isolated synthetic worktree."""

    def setUp(self):
        super().setUp()
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="worktree-session-guard-1490-"
        )
        self.worktree_root = Path(self._temporary_directory.name)

    def tearDown(self):
        lock_artifacts = list(self.worktree_root.rglob("playwright-session.lock"))
        database_artifacts = list(
            self.worktree_root.rglob("test_playwright_db*.sqlite3")
        )
        self._temporary_directory.cleanup()
        super().tearDown()
        self.assertEqual(lock_artifacts, [], "guard lock metadata leaked")
        self.assertEqual(database_artifacts, [], "Playwright database artifact leaked")


class WorktreeExclusionPolicyTests(TemporaryWorktreePolicyTests):
    def test_same_worktree_conflict_fails_fast_with_holder_details(self):
        guard = PlaywrightWorktreeGuard(self.worktree_root).acquire()
        try:
            with self.assertRaises(WorktreeGuardAlreadyHeld) as raised:
                PlaywrightWorktreeGuard(self.worktree_root).acquire()
        finally:
            guard.release()

        message = str(raised.exception)
        self.assertIn("Another Playwright session is already using this worktree.", message)
        self.assertIn(f"Worktree: {self.worktree_root.resolve()}", message)
        self.assertIn(f"Current PID: {os.getpid()}", message)
        self.assertIn(f"holder PID: {os.getpid()}", message)
        self.assertIn("command:", message)
        self.assertIn("claimed at:", message)
        self.assertIn("wait for the other run to finish", message)
        self.assertIn("stop it if it is stuck", message)
        self.assertIn("separate git worktree", message)
        self.assertIn("test_playwright_db.sqlite3", message)

    def test_conflict_message_sanitizes_recorded_holder_command(self):
        guard = PlaywrightWorktreeGuard(self.worktree_root).acquire()
        try:
            (self.worktree_root / LOCK_RELATIVE_PATH).write_text(
                json.dumps(
                    {
                        "claimed_at": "2026-07-09T00:00:00+00:00",
                        "command": (
                            "pytest --token=secret "
                            "postgresql://user:pass@db.example/app"
                        ),
                        "pid": os.getpid(),
                        "token": guard.token,
                        "worktree": str(self.worktree_root),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(WorktreeGuardAlreadyHeld) as raised:
                PlaywrightWorktreeGuard(self.worktree_root).acquire()
        finally:
            guard.release()

        message = str(raised.exception)
        self.assertNotIn("secret", message)
        self.assertNotIn("user:pass", message)
        self.assertIn("--token=<redacted>", message)
        self.assertIn("<redacted-url>", message)

    def test_separate_worktree_roots_are_allowed_concurrently(self):
        guard_a = PlaywrightWorktreeGuard(
            self.worktree_root / "worktree-a"
        ).acquire()
        guard_b = PlaywrightWorktreeGuard(
            self.worktree_root / "worktree-b"
        ).acquire()
        try:
            self.assertNotEqual(guard_a.lock_path, guard_b.lock_path)
            self.assertTrue(guard_a.lock_path.exists())
            self.assertTrue(guard_b.lock_path.exists())
        finally:
            guard_b.release()
            guard_a.release()

    def test_release_allows_retry_in_same_worktree(self):
        first = PlaywrightWorktreeGuard(self.worktree_root).acquire()
        first.release()

        second = PlaywrightWorktreeGuard(self.worktree_root).acquire()
        try:
            metadata = _lock_metadata(self.worktree_root)
            self.assertEqual(metadata["pid"], os.getpid())
            self.assertEqual(metadata["token"], second.token)
        finally:
            second.release()

    def test_dead_holder_metadata_does_not_block_future_session(self):
        lock_path = self.worktree_root / LOCK_RELATIVE_PATH
        lock_path.parent.mkdir(parents=True)
        lock_path.write_text(
            json.dumps(
                {
                    "command": "pytest old-run",
                    "pid": 999999999,
                    "token": "stale",
                    "worktree": str(self.worktree_root),
                }
            ),
            encoding="utf-8",
        )

        guard = PlaywrightWorktreeGuard(self.worktree_root).acquire()
        try:
            metadata = _lock_metadata(self.worktree_root)
            self.assertEqual(metadata["pid"], os.getpid())
            self.assertEqual(metadata["token"], guard.token)
            self.assertEqual(metadata["worktree"], str(self.worktree_root.resolve()))
        finally:
            guard.release()


class SessionLifecyclePolicyTests(SimpleTestCase):
    def test_sessionstart_claims_guard_for_local_direct_pytest(self):
        events = []

        class FakeGuard:
            @classmethod
            def for_current_worktree(cls):
                return cls()

            def acquire(self):
                events.append("acquire")

        with (
            mock.patch.dict(os.environ),
            mock.patch.object(
                conftest, "_resolved_base_url", return_value="http://127.0.0.1:8123"
            ),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
            mock.patch.object(conftest, "PlaywrightWorktreeGuard", FakeGuard),
        ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            config = SimpleNamespace()
            guard = conftest._claim_playwright_worktree_guard(config)

        self.assertIsInstance(guard, FakeGuard)
        self.assertIs(config._playwright_worktree_guard, guard)
        self.assertEqual(events, ["acquire"])

    def test_non_local_playwright_base_url_does_not_claim_guard(self):
        class GuardShouldNotBeUsed:
            @classmethod
            def for_current_worktree(cls):
                raise AssertionError(
                    "remote Playwright sessions must not claim the local guard"
                )

        with (
            mock.patch.object(
                conftest,
                "_resolved_base_url",
                return_value="https://dev.aishippinglabs.com",
            ),
            mock.patch.object(conftest, "_base_url_is_local", return_value=False),
            mock.patch.object(
                conftest, "PlaywrightWorktreeGuard", GuardShouldNotBeUsed
            ),
        ):
            config = SimpleNamespace()
            guard = conftest._claim_playwright_worktree_guard(config)

        self.assertIsNone(guard)
        self.assertFalse(hasattr(config, "_playwright_worktree_guard"))

    def test_sessionfinish_releases_and_clears_guard(self):
        events = []

        class FakeGuard:
            def release(self):
                events.append("release")

        config = SimpleNamespace(_playwright_worktree_guard=FakeGuard())
        conftest._release_playwright_worktree_guard(config)

        self.assertEqual(events, ["release"])
        self.assertFalse(hasattr(config, "_playwright_worktree_guard"))


class WorkerIdentityPolicyTests(SimpleTestCase):
    def test_controller_process_has_no_worker_id(self):
        self.assertIsNone(current_xdist_worker_id(environ={}))
        self.assertIs(is_xdist_worker(environ={}), False)

    def test_worker_process_reports_its_id(self):
        for raw in ("gw0", "gw1", "gw11"):
            with self.subTest(raw=raw):
                environ = {XDIST_WORKER_ENV_VAR: raw}
                self.assertEqual(current_xdist_worker_id(environ=environ), raw)
                self.assertIs(is_xdist_worker(environ=environ), True)

    def test_blank_worker_env_is_treated_as_controller(self):
        for raw in ("", "   "):
            with self.subTest(raw=raw):
                environ = {XDIST_WORKER_ENV_VAR: raw}
                self.assertIsNone(current_xdist_worker_id(environ=environ))

    def test_worker_id_is_sanitized_for_filesystem_use(self):
        environ = {XDIST_WORKER_ENV_VAR: "../../etc/gw0"}
        worker_id = current_xdist_worker_id(environ=environ)
        self.assertEqual(worker_id, "etcgw0")
        self.assertNotIn("/", worker_id)
        self.assertNotIn("..", worker_id)


class DatabaseNamingPolicyTests(TemporaryWorktreePolicyTests):
    def test_serial_run_keeps_the_historical_database_name(self):
        name = conftest.playwright_test_database_name(
            self.worktree_root, worker_id=None
        )
        self.assertEqual(name, str(self.worktree_root / "test_playwright_db.sqlite3"))

    def test_each_worker_gets_a_distinct_database_file(self):
        first = conftest.playwright_test_database_name(
            self.worktree_root, worker_id="gw0"
        )
        second = conftest.playwright_test_database_name(
            self.worktree_root, worker_id="gw1"
        )
        self.assertEqual(
            first, str(self.worktree_root / "test_playwright_db_gw0.sqlite3")
        )
        self.assertEqual(
            second, str(self.worktree_root / "test_playwright_db_gw1.sqlite3")
        )
        self.assertNotEqual(first, second)

    def test_worker_databases_stay_inside_their_own_worktree(self):
        worktree_a = self.worktree_root / "worktree-a"
        worktree_b = self.worktree_root / "worktree-b"
        name_a = conftest.playwright_test_database_name(worktree_a, worker_id="gw0")
        name_b = conftest.playwright_test_database_name(worktree_b, worker_id="gw0")
        self.assertTrue(name_a.startswith(str(worktree_a)))
        self.assertTrue(name_b.startswith(str(worktree_b)))
        self.assertNotEqual(name_a, name_b)

    def test_worker_database_name_defaults_to_the_ambient_worker(self):
        with mock.patch.dict(os.environ, {XDIST_WORKER_ENV_VAR: "gw3"}):
            name = conftest.playwright_test_database_name(self.worktree_root)
        self.assertEqual(
            name, str(self.worktree_root / "test_playwright_db_gw3.sqlite3")
        )

    def test_ambient_serial_run_gets_the_unsuffixed_name(self):
        with mock.patch.dict(os.environ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            name = conftest.playwright_test_database_name(self.worktree_root)
        self.assertEqual(name, str(self.worktree_root / "test_playwright_db.sqlite3"))

    def test_worker_database_name_still_passes_the_unsafe_database_guard(self):
        name = conftest.playwright_test_database_name(
            self.worktree_root, worker_id="gw0"
        )
        self.assertTrue(
            is_database_test_scoped(
                {"ENGINE": "django.db.backends.sqlite3", "NAME": name},
                base_dir=self.worktree_root,
            )
        )

    def test_db_settings_hook_applies_the_worker_suffix(self):
        database_settings = {"ENGINE": "django.db.backends.sqlite3"}
        with mock.patch.dict(os.environ, {XDIST_WORKER_ENV_VAR: "gw2"}):
            conftest.apply_playwright_test_database(
                database_settings, self.worktree_root
            )
        self.assertEqual(
            database_settings["TEST"]["NAME"],
            str(self.worktree_root / "test_playwright_db_gw2.sqlite3"),
        )

    def test_db_settings_hook_overrides_an_earlier_test_name(self):
        database_settings = {
            "ENGINE": "django.db.backends.sqlite3",
            "TEST": {
                "NAME": str(self.worktree_root / "pinned_test_db.sqlite3")
            },
        }
        conftest.apply_playwright_test_database(
            database_settings, self.worktree_root, worker_id="gw2"
        )
        self.assertEqual(
            database_settings["TEST"]["NAME"],
            str(self.worktree_root / "test_playwright_db_gw2.sqlite3"),
        )

    def test_db_settings_hook_leaves_non_sqlite_engines_alone(self):
        database_settings = {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": "aisl",
        }
        conftest.apply_playwright_test_database(
            database_settings, self.worktree_root, worker_id="gw0"
        )
        self.assertNotIn("TEST", database_settings)


class GuardClaimPolicyTests(TemporaryWorktreePolicyTests):
    def test_xdist_worker_does_not_claim_the_worktree_guard(self):
        class GuardShouldNotBeUsed:
            @classmethod
            def for_current_worktree(cls):
                raise AssertionError("xdist workers must not claim the worktree guard")

        with (
            mock.patch.dict(os.environ, {XDIST_WORKER_ENV_VAR: "gw0"}),
            mock.patch.object(
                conftest, "_resolved_base_url", return_value="http://127.0.0.1:8123"
            ),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
            mock.patch.object(
                conftest, "PlaywrightWorktreeGuard", GuardShouldNotBeUsed
            ),
        ):
            config = SimpleNamespace()
            guard = conftest._claim_playwright_worktree_guard(config)

        self.assertIsNone(guard)
        self.assertFalse(hasattr(config, "_playwright_worktree_guard"))

    def test_xdist_controller_still_claims_the_worktree_guard(self):
        events = []

        class FakeGuard:
            @classmethod
            def for_current_worktree(cls):
                return cls()

            def acquire(self):
                events.append("acquire")

        with (
            mock.patch.dict(os.environ),
            mock.patch.object(
                conftest, "_resolved_base_url", return_value="http://127.0.0.1:8123"
            ),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
            mock.patch.object(conftest, "PlaywrightWorktreeGuard", FakeGuard),
        ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            guard = conftest._claim_playwright_worktree_guard(SimpleNamespace())

        self.assertIsInstance(guard, FakeGuard)
        self.assertEqual(events, ["acquire"])

    def test_second_separate_invocation_is_still_blocked(self):
        holder = PlaywrightWorktreeGuard(self.worktree_root).acquire()
        fake_guard_factory = SimpleNamespace(
            for_current_worktree=lambda: PlaywrightWorktreeGuard(self.worktree_root)
        )
        try:
            with (
                mock.patch.dict(os.environ),
                mock.patch.object(
                    conftest, "PlaywrightWorktreeGuard", fake_guard_factory
                ),
                mock.patch.object(
                    conftest,
                    "_resolved_base_url",
                    return_value="http://127.0.0.1:8123",
                ),
                mock.patch.object(
                    conftest, "_base_url_is_local", return_value=True
                ),
            ):
                os.environ.pop(XDIST_WORKER_ENV_VAR, None)
                with self.assertRaises(pytest.exit.Exception) as raised:
                    conftest._claim_playwright_worktree_guard(SimpleNamespace())
        finally:
            holder.release()

        message = str(raised.exception)
        self.assertIn("Another Playwright session is already using this worktree.", message)
        self.assertIn(f"holder PID: {os.getpid()}", message)


class PinnedPortPolicyTests(SimpleTestCase):
    def test_requested_worker_count_is_read_from_the_n_option(self):
        cases = ((None, 0), (0, 0), (1, 1), (4, 4), ("4", 4), (-2, 0), ("auto", 0))
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(
                    conftest.requested_xdist_worker_count(_config(raw)), expected
                )

    def test_pinned_port_with_parallelism_fails_fast(self):
        with (
            mock.patch.dict(os.environ, {"PLAYWRIGHT_DJANGO_PORT": "8765"}),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
        ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            with self.assertRaises(pytest.UsageError) as raised:
                conftest._assert_pinned_port_is_not_parallel(_config(4))

        message = str(raised.exception)
        self.assertIn("PLAYWRIGHT_DJANGO_PORT cannot be combined", message)
        self.assertIn("-n 4", message)
        self.assertIn("PLAYWRIGHT_XDIST_WORKERS=0", message)

    def test_pinned_port_is_allowed_without_parallelism(self):
        with (
            mock.patch.dict(os.environ, {"PLAYWRIGHT_DJANGO_PORT": "8765"}),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
        ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            self.assertIsNone(conftest._assert_pinned_port_is_not_parallel(_config(0)))
            self.assertIsNone(
                conftest._assert_pinned_port_is_not_parallel(_config(None))
            )

    def test_parallelism_is_allowed_without_a_pinned_port(self):
        with (
            mock.patch.dict(os.environ),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
        ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            os.environ.pop("PLAYWRIGHT_DJANGO_PORT", None)
            self.assertIsNone(conftest._assert_pinned_port_is_not_parallel(_config(4)))

    def test_invalid_pinned_port_does_not_block_parallelism(self):
        for raw in ("0", "garbage", "  "):
            with self.subTest(raw=raw):
                with (
                    mock.patch.dict(
                        os.environ, {"PLAYWRIGHT_DJANGO_PORT": raw}
                    ),
                    mock.patch.object(
                        conftest, "_base_url_is_local", return_value=True
                    ),
                ):
                    os.environ.pop(XDIST_WORKER_ENV_VAR, None)
                    self.assertIsNone(
                        conftest._assert_pinned_port_is_not_parallel(_config(4))
                    )

    def test_remote_base_url_ignores_the_pinned_port_check(self):
        with (
            mock.patch.dict(os.environ, {"PLAYWRIGHT_DJANGO_PORT": "8765"}),
            mock.patch.object(conftest, "_base_url_is_local", return_value=False),
        ):
            os.environ.pop(XDIST_WORKER_ENV_VAR, None)
            self.assertIsNone(conftest._assert_pinned_port_is_not_parallel(_config(4)))

    def test_worker_process_does_not_re_run_the_pinned_port_check(self):
        with (
            mock.patch.dict(
                os.environ,
                {
                    XDIST_WORKER_ENV_VAR: "gw0",
                    "PLAYWRIGHT_DJANGO_PORT": "8765",
                },
            ),
            mock.patch.object(conftest, "_base_url_is_local", return_value=True),
        ):
            self.assertIsNone(conftest._assert_pinned_port_is_not_parallel(_config(4)))


_CACHE_POISON_KEY = "AISL_1470_CACHE_POISON_PROBE"


class IntegrationConfigCacheIsolationTests(TestCase):
    """Exercise the real Playwright cache-reset fixture around native nodes."""

    def setUp(self):
        super().setUp()
        self._cache_fixture = conftest._reset_integration_config_cache.__wrapped__()
        next(self._cache_fixture)

    def tearDown(self):
        try:
            with self.assertRaises(StopIteration):
                next(self._cache_fixture)
            self.assertNotIn(_CACHE_POISON_KEY, integration_config._cache)
        finally:
            integration_config.reset_local_config_cache()
            super().tearDown()

    def test_config_cache_poison_does_not_survive_into_the_next_test(self):
        # Warm the cache through the real read path so its local stamp matches
        # any shared stamp published by an earlier test in this worker.  A
        # manually populated cache with a missing stamp is correctly treated
        # as stale by get_config(), which made this node depend on suite order.
        with mock.patch.object(
            integration_config, "_read_stamp", return_value="published-stamp"
        ):
            self.assertEqual(
                integration_config.get_config(_CACHE_POISON_KEY, "clean"), "clean"
            )
            integration_config._cache[_CACHE_POISON_KEY] = "poison"
            self.assertEqual(
                integration_config.get_config(_CACHE_POISON_KEY, "clean"), "poison"
            )

    def test_next_test_sees_a_clean_config_cache(self):
        self.assertNotIn(_CACHE_POISON_KEY, integration_config._cache)
        self.assertEqual(
            integration_config.get_config(_CACHE_POISON_KEY, "clean"), "clean"
        )
