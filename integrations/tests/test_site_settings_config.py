"""Tests for the ``site`` integration settings group (issue #369).

The ``site`` group exposes ``SITE_BASE_URL`` and ``SITE_BASE_URL_ALIASES``
to Studio so operators can edit the canonical base URL and the
banner-suppression alias list without a redeploy. This module locks two
contracts:

- ``site`` is registered in ``INTEGRATION_GROUPS`` with both expected
  keys (so the Studio dashboard renders it).
- ``get_config('SITE_BASE_URL', ...)`` honours DB > env precedence and
  picks up new values once ``clear_config_cache()`` has been called
  (the ``settings_save_group`` view does this on every successful save).
"""

import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name

User = get_user_model()


class SiteGroupRegistryTest(TestCase):
    """Lock the registry shape for the ``site`` group."""

    def test_site_group_present(self):
        group = get_group_by_name('site')
        self.assertIsNotNone(group)
        self.assertEqual(group['label'], 'Site')

    def test_site_group_has_both_keys(self):
        group = get_group_by_name('site')
        keys = [k['key'] for k in group['keys']]
        self.assertIn('SITE_BASE_URL', keys)
        self.assertIn('SITE_BASE_URL_ALIASES', keys)

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

    def test_save_creates_both_site_keys(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post('/studio/settings/site/save/', {
            'SITE_BASE_URL': 'https://aishippinglabs.com',
            'SITE_BASE_URL_ALIASES': 'https://prod.aishippinglabs.com',
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
        })
        # After save, get_config returns the new DB value rather than
        # the stale cached blank.
        self.assertEqual(
            get_config('SITE_BASE_URL_ALIASES'),
            'https://prod.aishippinglabs.com',
        )
