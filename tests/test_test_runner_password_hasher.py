"""Tests for the Django runner's fast, test-only password hasher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest import mock

from django.conf import settings
from django.contrib.auth.hashers import identify_hasher, make_password
from django.test import SimpleTestCase, override_settings

from website.test_runner import (
    TEST_PASSWORD_HASHERS,
    FastPasswordHasherParallelTestSuite,
    PicklableTracebackRunner,
)

SECURE_PASSWORD_HASHERS = ("django.contrib.auth.hashers.PBKDF2PasswordHasher",)


def _algorithm_for_new_password() -> str:
    return identify_hasher(make_password("runner-password")).algorithm


class FastPasswordHasherRunnerLifecycleTests(SimpleTestCase):
    def _runner(self):
        return PicklableTracebackRunner(verbosity=0)

    def test_runner_activates_fast_hasher_and_restores_previous_setting(self):
        with (
            override_settings(PASSWORD_HASHERS=SECURE_PASSWORD_HASHERS),
            mock.patch("django.test.runner.DiscoverRunner.setup_test_environment"),
            mock.patch("django.test.runner.DiscoverRunner.teardown_test_environment"),
        ):
            self.assertEqual(_algorithm_for_new_password(), "pbkdf2_sha256")

            runner = self._runner()
            runner.setup_test_environment()
            self.assertEqual(tuple(settings.PASSWORD_HASHERS), TEST_PASSWORD_HASHERS)
            self.assertEqual(_algorithm_for_new_password(), "md5")

            runner.teardown_test_environment()
            self.assertEqual(tuple(settings.PASSWORD_HASHERS), SECURE_PASSWORD_HASHERS)
            self.assertEqual(_algorithm_for_new_password(), "pbkdf2_sha256")

    def test_setup_failure_restores_previous_setting(self):
        cutoff_key = "LEGACY_NUMERIC_CHECKOUT_REFERENCE_CUTOFF"
        with (
            override_settings(PASSWORD_HASHERS=SECURE_PASSWORD_HASHERS),
            mock.patch.dict(os.environ, {cutoff_key: "runtime-cutoff"}),
            mock.patch(
                "django.test.runner.DiscoverRunner.setup_test_environment",
                side_effect=RuntimeError("setup failed"),
            ),
        ):
            runner = self._runner()
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                runner.setup_test_environment()

            self.assertEqual(tuple(settings.PASSWORD_HASHERS), SECURE_PASSWORD_HASHERS)
            self.assertEqual(_algorithm_for_new_password(), "pbkdf2_sha256")
            self.assertEqual(os.environ[cutoff_key], "runtime-cutoff")

    def test_explicit_algorithm_override_wins_inside_runner(self):
        with (
            mock.patch("django.test.runner.DiscoverRunner.setup_test_environment"),
            mock.patch("django.test.runner.DiscoverRunner.teardown_test_environment"),
        ):
            runner = self._runner()
            runner.setup_test_environment()
            self.assertEqual(_algorithm_for_new_password(), "md5")

            with override_settings(PASSWORD_HASHERS=SECURE_PASSWORD_HASHERS):
                self.assertEqual(_algorithm_for_new_password(), "pbkdf2_sha256")

            self.assertEqual(_algorithm_for_new_password(), "md5")
            runner.teardown_test_environment()

    def test_spawned_worker_hook_activates_fast_hasher(self):
        suite = FastPasswordHasherParallelTestSuite([], processes=2)
        hasher_override = mock.Mock()
        with (
            mock.patch("website.test_runner.django.setup") as django_setup,
            mock.patch(
                "website.test_runner.override_settings",
                return_value=hasher_override,
            ) as override_factory,
        ):
            suite.process_setup()

        django_setup.assert_called_once_with()
        override_factory.assert_called_once_with(PASSWORD_HASHERS=TEST_PASSWORD_HASHERS)
        hasher_override.enable.assert_called_once_with()

    def test_non_test_process_keeps_django_secure_hasher_chain(self):
        script = """
import json
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "website.settings")
from django.conf import settings
print(json.dumps(settings.PASSWORD_HASHERS))
"""
        env = os.environ.copy()
        env.pop("TEST_SHARD_COUNT", None)
        env.pop("TEST_SHARD_INDEX", None)
        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        )

        runtime_hashers = json.loads(result.stdout)
        self.assertEqual(
            runtime_hashers[0],
            "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        )
        self.assertNotIn(TEST_PASSWORD_HASHERS[0], runtime_hashers)


class ParallelWorkerFastHasherProbeOne(SimpleTestCase):
    def test_worker_uses_fast_hasher(self):
        self.assertEqual(_algorithm_for_new_password(), "md5")


class ParallelWorkerFastHasherProbeTwo(SimpleTestCase):
    def test_worker_uses_fast_hasher(self):
        self.assertEqual(_algorithm_for_new_password(), "md5")
