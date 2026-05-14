"""Focused tests for tiers.yaml sync behavior."""

import os
import tempfile
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase

from content.models import SiteConfig
from integrations.services.github_sync.dispatchers.tiers import _sync_tiers_yaml


class TiersYamlSyncTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_tiers_yaml(self, content):
        with open(
            os.path.join(self.temp_dir.name, 'tiers.yaml'),
            'w',
            encoding='utf-8',
        ) as f:
            f.write(content)

    def test_valid_sync_creates_and_updates_site_config(self):
        self._write_tiers_yaml(
            """
- slug: starter
  title: Starter
- slug: pro
  title: Pro
""",
        )

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 2})
        config = SiteConfig.objects.get(key='tiers')
        self.assertEqual(
            config.data,
            [
                {'slug': 'starter', 'title': 'Starter'},
                {'slug': 'pro', 'title': 'Pro'},
            ],
        )

        self._write_tiers_yaml(
            """
- slug: enterprise
  title: Enterprise
""",
        )

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 1})
        config.refresh_from_db()
        self.assertEqual(
            config.data,
            [{'slug': 'enterprise', 'title': 'Enterprise'}],
        )

    def test_missing_tiers_yaml_returns_not_synced(self):
        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': False, 'count': 0})
        self.assertFalse(SiteConfig.objects.filter(key='tiers').exists())

    def test_malformed_yaml_soft_fails(self):
        self._write_tiers_yaml('[')

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': False, 'count': 0})
        self.assertFalse(SiteConfig.objects.filter(key='tiers').exists())

    def test_non_list_yaml_soft_fails(self):
        self._write_tiers_yaml(
            """
tiers:
  - slug: starter
""",
        )

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': False, 'count': 0})
        self.assertFalse(SiteConfig.objects.filter(key='tiers').exists())

    def test_file_read_oserror_soft_fails(self):
        self._write_tiers_yaml('[]')

        with patch('builtins.open', side_effect=OSError('cannot read')):
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': False, 'count': 0})
        self.assertFalse(SiteConfig.objects.filter(key='tiers').exists())

    def test_database_error_soft_fails(self):
        self._write_tiers_yaml(
            """
- slug: starter
  title: Starter
""",
        )

        with patch.object(
            SiteConfig.objects,
            'update_or_create',
            side_effect=DatabaseError('db unavailable'),
        ):
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': False, 'count': 0})
        self.assertFalse(SiteConfig.objects.filter(key='tiers').exists())

    def test_unexpected_type_error_from_database_call_propagates(self):
        self._write_tiers_yaml(
            """
- slug: starter
  title: Starter
""",
        )

        with patch.object(
            SiteConfig.objects,
            'update_or_create',
            side_effect=TypeError('programmer error'),
        ):
            with self.assertRaisesRegex(TypeError, 'programmer error'):
                _sync_tiers_yaml(self.temp_dir.name)

    def test_unexpected_attribute_error_from_database_call_propagates(self):
        self._write_tiers_yaml(
            """
- slug: starter
  title: Starter
""",
        )

        with patch.object(
            SiteConfig.objects,
            'update_or_create',
            side_effect=AttributeError('programmer error'),
        ):
            with self.assertRaisesRegex(AttributeError, 'programmer error'):
                _sync_tiers_yaml(self.temp_dir.name)
