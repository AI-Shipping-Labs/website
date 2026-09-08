"""Compatibility tests for the ``integrations.config`` shim (issue #1584).

The shim keeps the donor import path and donor resolution semantics
(DB -> Django settings -> env -> default) while reading values from
``community_base.config`` storage (``cb_config.Setting`` rows). These
tests pin that contract. They inject their own registry definitions via
``mock.patch.dict`` so they neither depend on the site key declarations
in ``integrations.settings_keys`` nor pollute the process-wide package
registry for other tests.
"""

import os
from unittest import mock

from community_base.config import service as cb_service
from community_base.config.crypto import encrypt
from community_base.config.models import Setting
from community_base.config.registry import Definition, definition
from django.core.cache import cache
from django.test import TestCase, override_settings

from integrations import config as config_module
from integrations.config import (
    _STAMP_CACHE_KEY,
    clear_config_cache,
    get_config,
    reset_local_config_cache,
    resolve_source,
)

_SHIM_KEYS = {
    "SHIM_TEST_PLAIN": Definition(
        key="SHIM_TEST_PLAIN",
        group="shim_tests",
        label="Shim test plain key",
        description="Injected by integrations.tests.test_config_shim.",
        value_type="str",
        default="",
    ),
    "SHIM_TEST_SECRET": Definition(
        key="SHIM_TEST_SECRET",
        group="shim_tests",
        label="Shim test secret key",
        description="Injected by integrations.tests.test_config_shim.",
        value_type="str",
        default="",
        secret=True,
    ),
    "SHIM_TEST_INT": Definition(
        key="SHIM_TEST_INT",
        group="shim_tests",
        label="Shim test int key",
        description="Injected by integrations.tests.test_config_shim.",
        value_type="int",
        default=0,
    ),
}


def _put_setting(key, value, *, value_type="str"):
    """Create or update a cb_config Setting row directly."""
    Setting.objects.update_or_create(
        key=key,
        defaults={"value": value, "value_type": value_type, "source": "test"},
    )
    return Setting.objects.get(key=key)


class ShimKeysMixin:
    """Inject the shim test definitions into the package registry."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(
            "community_base.config.registry._definitions",
            _SHIM_KEYS,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.assertIs(definition("SHIM_TEST_SECRET"), _SHIM_KEYS["SHIM_TEST_SECRET"])
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()
        super().tearDown()


class ConfigShimResolutionTest(ShimKeysMixin, TestCase):
    """``get_config`` preserves the donor resolution order on package storage."""

    def test_db_value_wins_over_settings_and_env(self):
        _put_setting("SHIM_TEST_PLAIN", "from_db")
        with (
            override_settings(SHIM_TEST_PLAIN="from_settings"),
            mock.patch.dict(
                os.environ,
                {"SHIM_TEST_PLAIN": "from_env"},
            ),
        ):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_db")

    def test_settings_layer_respected_and_suppressed_by_use_settings(self):
        with (
            override_settings(SHIM_TEST_PLAIN="from_settings"),
            mock.patch.dict(
                os.environ,
                {"SHIM_TEST_PLAIN": "from_env"},
            ),
        ):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_settings")
            self.assertEqual(get_config("SHIM_TEST_PLAIN", use_settings=False), "from_env")

    def test_env_fallback_when_no_db_and_no_setting(self):
        with mock.patch.dict(os.environ, {"SHIM_TEST_PLAIN": "from_env"}):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_env")

    def test_default_returned_last(self):
        self.assertEqual(get_config("SHIM_TEST_UNSET_KEY", "fallback"), "fallback")
        self.assertEqual(get_config("SHIM_TEST_PLAIN", "fallback"), "fallback")

    def test_empty_db_row_treated_as_unset(self):
        _put_setting("SHIM_TEST_PLAIN", "")
        with override_settings(SHIM_TEST_PLAIN="from_settings"):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_settings")

    def test_secret_row_round_trips_to_plaintext(self):
        _put_setting("SHIM_TEST_SECRET", encrypt("s3cr3t-plaintext"))
        with (
            override_settings(SHIM_TEST_SECRET="from_settings"),
            mock.patch.dict(
                os.environ,
                {"SHIM_TEST_SECRET": "from_env"},
            ),
        ):
            self.assertEqual(get_config("SHIM_TEST_SECRET"), "s3cr3t-plaintext")

    def test_undecryptable_secret_row_falls_through(self):
        _put_setting("SHIM_TEST_SECRET", "not-fernet-ciphertext")
        with mock.patch.dict(os.environ, {"SHIM_TEST_SECRET": "from_env"}):
            with self.assertLogs("integrations.config", level="WARNING"):
                self.assertEqual(get_config("SHIM_TEST_SECRET"), "from_env")

    def test_unknown_key_is_legal_and_ignores_db_row(self):
        _put_setting("SHIM_TEST_UNDECLARED", "from_db")
        with (
            override_settings(SHIM_TEST_UNDECLARED="from_settings"),
            mock.patch.dict(
                os.environ,
                {"SHIM_TEST_UNDECLARED": "from_env"},
            ),
        ):
            self.assertEqual(get_config("SHIM_TEST_UNDECLARED"), "from_settings")
        self.assertEqual(get_config("SHIM_TEST_UNDECLARED", "fallback"), "fallback")

    def test_stored_values_returned_raw_without_package_coercion(self):
        _put_setting("SHIM_TEST_INT", "007", value_type="int")
        self.assertEqual(get_config("SHIM_TEST_INT"), "007")


class ConfigShimResolveSourceTest(ShimKeysMixin, TestCase):
    """``resolve_source`` keeps the donor enum and never exposes values."""

    def test_none_when_unset_everywhere(self):
        self.assertIsNone(resolve_source("SHIM_TEST_PLAIN"))

    def test_db_when_row_has_nonempty_value(self):
        _put_setting("SHIM_TEST_PLAIN", "from_db")
        self.assertEqual(resolve_source("SHIM_TEST_PLAIN"), "db")

    def test_django_settings(self):
        with override_settings(SHIM_TEST_PLAIN="from_settings"):
            self.assertEqual(resolve_source("SHIM_TEST_PLAIN"), "django_settings")

    def test_env(self):
        with mock.patch.dict(os.environ, {"SHIM_TEST_PLAIN": "from_env"}):
            self.assertEqual(resolve_source("SHIM_TEST_PLAIN"), "env")

    def test_default(self):
        self.assertEqual(resolve_source("SHIM_TEST_PLAIN", registry_default="fb"), "default")

    def test_db_wins_over_other_layers(self):
        _put_setting("SHIM_TEST_PLAIN", "from_db")
        with (
            override_settings(SHIM_TEST_PLAIN="from_settings"),
            mock.patch.dict(
                os.environ,
                {"SHIM_TEST_PLAIN": "from_env"},
            ),
        ):
            self.assertEqual(resolve_source("SHIM_TEST_PLAIN"), "db")

    def test_undeclared_key_has_no_db_layer(self):
        _put_setting("SHIM_TEST_UNDECLARED", "from_db")
        self.assertIsNone(resolve_source("SHIM_TEST_UNDECLARED"))

    def test_never_exposes_secret_plaintext(self):
        _put_setting("SHIM_TEST_SECRET", encrypt("s3cr3t-plaintext"))
        source = resolve_source("SHIM_TEST_SECRET")
        self.assertEqual(source, "db")
        self.assertNotIn("s3cr3t-plaintext", str(source))


class ConfigShimCacheInvalidationTest(ShimKeysMixin, TestCase):
    """The donor cache pattern, with the package stamp in the default cache."""

    def test_clear_config_cache_picks_up_new_db_value(self):
        with mock.patch.dict(os.environ, {"SHIM_TEST_PLAIN": "from_env"}):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_env")
            _put_setting("SHIM_TEST_PLAIN", "from_db")
            # The in-process cache is still stale until invalidation.
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_env")
            clear_config_cache()
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_db")

    def test_clear_config_cache_publishes_package_stamp_to_default_cache(self):
        before = cache.get(cb_service.STAMP_KEY)
        clear_config_cache()
        after = cache.get(cb_service.STAMP_KEY)
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_reset_local_config_cache_does_not_publish(self):
        clear_config_cache()
        stamp = cache.get(cb_service.STAMP_KEY)
        reset_local_config_cache()
        self.assertEqual(cache.get(cb_service.STAMP_KEY), stamp)

    def test_stamp_key_matches_package(self):
        self.assertEqual(_STAMP_CACHE_KEY, cb_service.STAMP_KEY)

    def test_worker_bypass_reads_fresh_db_value_with_cold_cache(self):
        with mock.patch.dict(os.environ, {"SHIM_TEST_PLAIN": "from_env"}):
            # Warm the non-worker cache first; it must be ignored below.
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "from_env")
        _put_setting("SHIM_TEST_PLAIN", "worker_v1")
        with mock.patch.object(config_module, "running_in_worker_process", return_value=True):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "worker_v1")
        _put_setting("SHIM_TEST_PLAIN", "worker_v2")
        with mock.patch.object(config_module, "running_in_worker_process", return_value=True):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "worker_v2")

    def test_worker_bypass_never_populates_the_shared_cache(self):
        _put_setting("SHIM_TEST_PLAIN", "worker_v1")
        with mock.patch.object(config_module, "running_in_worker_process", return_value=True):
            self.assertEqual(get_config("SHIM_TEST_PLAIN"), "worker_v1")
        self.assertFalse(config_module._cache_populated)
        self.assertNotIn("SHIM_TEST_PLAIN", config_module._cache)
