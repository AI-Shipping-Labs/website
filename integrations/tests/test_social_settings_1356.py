"""Registry + resolution coverage for the footer social URLs (#1356).

Four operator-managed social URLs (`SOCIAL_YOUTUBE_URL`,
`SOCIAL_LINKEDIN_URL`, `SOCIAL_GITHUB_URL`, `SOCIAL_X_URL`) are
registered under the existing ``site`` group and read via
``get_config`` — never a raw env / settings read. Defaults are empty so
no handle is invented.
"""

from django.test import TestCase

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS

SOCIAL_KEYS = (
    'SOCIAL_YOUTUBE_URL',
    'SOCIAL_LINKEDIN_URL',
    'SOCIAL_GITHUB_URL',
    'SOCIAL_X_URL',
)


class SocialSettingsRegistryTest(TestCase):
    def _site_group(self):
        for group in INTEGRATION_GROUPS:
            if group['name'] == 'site':
                return group
        self.fail("No 'site' integration group registered")

    def test_keys_registered_under_site_group(self):
        registered = {key['key'] for key in self._site_group()['keys']}
        for key in SOCIAL_KEYS:
            self.assertIn(key, registered)

    def test_keys_are_optional_and_non_secret(self):
        by_key = {key['key']: key for key in self._site_group()['keys']}
        for key in SOCIAL_KEYS:
            entry = by_key[key]
            self.assertFalse(entry.get('is_secret', False))
            self.assertTrue(entry.get('optional', False))
            self.assertTrue(entry.get('description'))
            self.assertTrue(entry.get('docs_url'))

    def test_no_new_group_added(self):
        # A new group would break Studio's group-count expectations; the
        # social keys must live in the existing ``site`` group.
        names = [group['name'] for group in INTEGRATION_GROUPS]
        self.assertEqual(len(names), len(set(names)))


class SocialSettingsResolutionTest(TestCase):
    def setUp(self):
        IntegrationSetting.objects.filter(
            key__startswith='SOCIAL_'
        ).delete()
        clear_config_cache()

    def test_defaults_are_empty(self):
        for key in SOCIAL_KEYS:
            self.assertEqual(get_config(key, ''), '')

    def test_db_override_is_readable(self):
        IntegrationSetting.objects.create(
            key='SOCIAL_X_URL',
            value='https://x.com/aisl',
        )
        clear_config_cache()
        self.assertEqual(get_config('SOCIAL_X_URL', ''), 'https://x.com/aisl')
