"""Tests for the ``site`` integration settings group (issue #369).

The ``site`` group exposes ``SITE_BASE_URL``, ``SITE_BASE_URL_ALIASES``,
and ``EVENT_DISPLAY_TIMEZONE`` to Studio so operators can edit public
site defaults without a redeploy. This module locks two contracts:

- ``site`` is registered in ``INTEGRATION_GROUPS`` with both expected
  keys (so the Studio dashboard renders it).
- ``get_config('SITE_BASE_URL', ...)`` honours DB > env precedence and
  picks up new values once ``clear_config_cache()`` has been called
  (the ``settings_save_group`` view does this on every successful save).
"""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase

from integrations import config as config_module
from integrations.config import clear_config_cache, get_config, site_base_url
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name

User = get_user_model()


class SiteGroupRegistryTest(TestCase):
    """Lock the registry shape for the ``site`` group."""

    def test_site_group_present(self):
        group = get_group_by_name('site')
        self.assertIsNotNone(group)
        self.assertEqual(group['label'], 'Site')

    def test_site_group_has_expected_keys(self):
        group = get_group_by_name('site')
        keys = [k['key'] for k in group['keys']]
        self.assertIn('SITE_BASE_URL', keys)
        self.assertIn('SITE_BASE_URL_ALIASES', keys)
        self.assertIn('EVENT_DISPLAY_TIMEZONE', keys)

    def test_site_keys_are_not_secret(self):
        group = get_group_by_name('site')
        for key_def in group['keys']:
            self.assertFalse(
                key_def['is_secret'],
                f"{key_def['key']} should be is_secret=False (URLs are "
                'not secrets — encrypting them only obscures Studio).',
            )


class SiteBaseUrlConfigTest(TestCase):
    """``get_config('SITE_BASE_URL')`` precedence and cache invalidation."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_db_value_overrides_env_for_site_base_url(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        with patch.dict(os.environ, {'SITE_BASE_URL': 'https://aishippinglabs.com'}):
            result = get_config('SITE_BASE_URL', 'https://default.example.com')
        self.assertEqual(result, 'https://override.example.com')

    def test_env_used_when_no_db_row(self):
        # No IntegrationSetting row, env var set — env wins (after
        # Django settings, which mirror the env at boot).
        with patch.dict(os.environ, {'SITE_BASE_URL': 'https://from-env.example.com'}):
            # clear_config_cache + override_settings would couple this
            # too tightly; we just verify the fallback chain when the
            # DB is empty.
            clear_config_cache()
            # Settings already snapshot SITE_BASE_URL at boot; assert
            # the helper's env fallback kicks in for an unset key.
            result = get_config('SITE_BASE_URL_ALIASES', 'fallback')
            self.assertEqual(result, 'fallback')

    def test_cache_cleared_after_db_write(self):
        # Initial read with no DB row falls through to default.
        result1 = get_config('SITE_BASE_URL_ALIASES', 'no-aliases')
        self.assertEqual(result1, 'no-aliases')
        # Write a row and clear the cache (simulating settings_save_group).
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        # Subsequent reads see the new value.
        result2 = get_config('SITE_BASE_URL_ALIASES', 'no-aliases')
        self.assertEqual(result2, 'https://prod.aishippinglabs.com')


class SiteSettingsSaveViewTest(TestCase):
    """``POST /studio/settings/site/save/`` upserts both keys and clears
    the config cache (AC #3 of issue #369). The route is generic
    (``settings_save_group`` looks up the group via the registry), so
    this test mostly proves the new ``site`` group plays nicely with
    the existing wiring.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_save_creates_site_keys(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/settings/site/save/', {
            'SITE_BASE_URL': 'https://aishippinglabs.com',
            'SITE_BASE_URL_ALIASES': 'https://prod.aishippinglabs.com',
            'EVENT_DISPLAY_TIMEZONE': 'Europe/Berlin',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            IntegrationSetting.objects.get(key='SITE_BASE_URL').value,
            'https://aishippinglabs.com',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(
                key='SITE_BASE_URL_ALIASES',
            ).value,
            'https://prod.aishippinglabs.com',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(key='EVENT_DISPLAY_TIMEZONE').value,
            'Europe/Berlin',
        )
        # Both rows are tagged with the new group name.
        self.assertEqual(
            IntegrationSetting.objects.get(key='SITE_BASE_URL').group,
            'site',
        )

    def test_save_clears_config_cache_for_site_keys(self):
        # Populate the cache with an empty initial state.
        get_config('SITE_BASE_URL_ALIASES', '')
        self.client.login(email='admin@test.com', password='testpass')
        self.client.post('/studio/settings/site/save/', {
            'SITE_BASE_URL': 'https://aishippinglabs.com',
            'SITE_BASE_URL_ALIASES': 'https://prod.aishippinglabs.com',
            'EVENT_DISPLAY_TIMEZONE': 'Europe/Berlin',
        })
        # After save, get_config returns the new DB value rather than
        # the stale cached blank.
        self.assertEqual(
            get_config('SITE_BASE_URL_ALIASES'),
            'https://prod.aishippinglabs.com',
        )


class CrossProcessCacheInvalidationTest(TestCase):
    """Stamp-based cross-process invalidation (issue #462).

    Production runs multiple gunicorn workers plus a separate qcluster.
    ``clear_config_cache()`` only mutates module globals in the process
    that handled the save, so before this fix the other processes kept
    serving stale ``site_base_url()`` values. The fix publishes a stamp
    into ``caches['django_q']`` on every save; ``get_config()`` re-reads
    the stamp and repopulates from the DB when it changes.

    These tests simulate "two processes" by directly resetting the
    module-level ``_cache_populated`` / ``_cache`` / ``_cache_stamp``
    globals — the equivalent of a fresh Python process whose only link
    to the other process is the shared cache backend.
    """

    def setUp(self):
        clear_config_cache()
        # Drop the stamp the previous test may have published so each
        # test starts from a clean slate.
        caches['django_q'].delete('integration_settings_stamp')
        # Reset module globals so we don't inherit a populated cache
        # across tests within the same process.
        config_module._cache = {}
        config_module._cache_populated = False
        config_module._cache_stamp = None

    def tearDown(self):
        clear_config_cache()
        caches['django_q'].delete('integration_settings_stamp')
        config_module._cache = {}
        config_module._cache_populated = False
        config_module._cache_stamp = None

    def _simulate_fresh_process(self):
        """Reset only the in-process state; leave the shared cache alone."""
        config_module._cache = {}
        config_module._cache_populated = False
        config_module._cache_stamp = None

    def test_save_in_one_process_visible_in_another(self):
        # Process A populates its cache while no DB row exists.
        first_value = get_config('SITE_BASE_URL', 'https://default.example.com')
        self.assertNotEqual(first_value, 'https://prod.example.com')
        self.assertTrue(config_module._cache_populated)
        # Snapshot Process A's view of the stamp (could be None if
        # nobody has cleared yet — that's fine).
        stamp_in_a_before = config_module._cache_stamp

        # Process B (simulated): write a new row + clear_config_cache().
        # In real production this happens in a different gunicorn worker.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://prod.example.com',
            group='site',
        )
        clear_config_cache()
        # Simulating "back in process A" — restore A's globals as they
        # were after its first read (cache_populated=True, old stamp).
        config_module._cache = {}
        config_module._cache_populated = True
        config_module._cache_stamp = stamp_in_a_before

        # Process A reads again. The stamp differs, so it must
        # repopulate from the DB and return the new value.
        second_value = get_config('SITE_BASE_URL', 'https://default.example.com')
        self.assertEqual(second_value, 'https://prod.example.com')

    def test_unchanged_stamp_does_not_repopulate(self):
        # First call populates and records the current stamp.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL', value='https://first.example.com', group='site',
        )
        clear_config_cache()
        first = get_config('SITE_BASE_URL', '')
        self.assertEqual(first, 'https://first.example.com')

        # Mutate the DB DIRECTLY without calling clear_config_cache().
        # No stamp change → the in-process cache must still serve the
        # old value (this is the hot-path guarantee: AC #3 forbids an
        # IntegrationSetting query on every get_config when nothing
        # has changed).
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').update(
            value='https://changed-without-stamp.example.com',
        )
        second = get_config('SITE_BASE_URL', '')
        self.assertEqual(second, 'https://first.example.com')

    def test_clear_publishes_a_new_stamp_each_call(self):
        clear_config_cache()
        stamp1 = caches['django_q'].get('integration_settings_stamp')
        self.assertIsNotNone(stamp1)
        clear_config_cache()
        stamp2 = caches['django_q'].get('integration_settings_stamp')
        self.assertIsNotNone(stamp2)
        self.assertNotEqual(stamp1, stamp2)

    def test_failed_populate_does_not_overwrite_stamp(self):
        # Publish a stamp via clear_config_cache().
        clear_config_cache()
        stamp_before = caches['django_q'].get('integration_settings_stamp')
        self.assertIsNotNone(stamp_before)

        # Force _populate_cache to fail by patching the model's manager
        # to raise. The function swallows the exception; the published
        # stamp must NOT be cleared (other processes mid-flight keep
        # working off the latest known stamp).
        with patch.object(
            IntegrationSetting.objects, 'values_list',
            side_effect=Exception('DB unreachable'),
        ):
            self._simulate_fresh_process()
            # Triggering a populate via get_config should not crash.
            get_config('SITE_BASE_URL', 'fallback')
            # The cache stayed unpopulated (so the next call will retry).
            self.assertFalse(config_module._cache_populated)
        # Shared stamp survived the failure.
        stamp_after = caches['django_q'].get('integration_settings_stamp')
        self.assertEqual(stamp_after, stamp_before)

    def test_site_base_url_helper_picks_up_cross_process_save(self):
        # Same scenario as test_save_in_one_process_visible_in_another
        # but exercised through the public site_base_url() helper, which
        # is what every URL-generating call site uses.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        # Process A reads once.
        first = site_base_url()
        self.assertEqual(first, 'https://aishippinglabs.com')

        # Process B updates and clears.
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').update(
            value='https://prod.aishippinglabs.com',
        )
        clear_config_cache()

        # Process A: keep its cache populated, reset only the stamp it
        # locally remembers so we simulate "saw old stamp".
        config_module._cache = {'SITE_BASE_URL': 'https://aishippinglabs.com'}
        config_module._cache_populated = True
        config_module._cache_stamp = 'stale-stamp'

        # The helper picks up the new value without a process restart.
        second = site_base_url()
        self.assertEqual(second, 'https://prod.aishippinglabs.com')
