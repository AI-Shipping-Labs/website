"""Focused tests for tiers.yaml sync behavior."""

import logging
import os
import tempfile
from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase

from content.models import SiteConfig
from integrations.services.github_sync.dispatchers.tiers import _sync_tiers_yaml
from payments.models import Tier


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


class TiersYamlStripePriceIdSyncTest(TestCase):
    """Cover the stripe_price_id_monthly/yearly write-through to payments.Tier."""

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

    def test_non_empty_yaml_fields_write_both_price_ids_onto_tier(self):
        Tier.objects.filter(slug='main').update(
            stripe_price_id_monthly='',
            stripe_price_id_yearly='',
        )
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  stripe_price_id_monthly: price_monthly_main_new
  stripe_price_id_yearly: price_yearly_main_new
""",
        )

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 1})
        tier = Tier.objects.get(slug='main')
        self.assertEqual(tier.stripe_price_id_monthly, 'price_monthly_main_new')
        self.assertEqual(tier.stripe_price_id_yearly, 'price_yearly_main_new')

    def test_missing_yaml_field_leaves_db_column_unchanged(self):
        Tier.objects.filter(slug='basic').update(
            stripe_price_id_monthly='price_monthly_basic_admin',
            stripe_price_id_yearly='price_yearly_basic_admin',
        )
        # yaml only carries the monthly value; yearly is omitted entirely.
        self._write_tiers_yaml(
            """
- name: Basic
  stripe_key: basic
  stripe_price_id_monthly: price_monthly_basic_new
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='basic')
        self.assertEqual(tier.stripe_price_id_monthly, 'price_monthly_basic_new')
        self.assertEqual(tier.stripe_price_id_yearly, 'price_yearly_basic_admin')

    def test_empty_string_yaml_value_leaves_db_column_unchanged(self):
        Tier.objects.filter(slug='premium').update(
            stripe_price_id_monthly='price_monthly_premium_admin',
            stripe_price_id_yearly='price_yearly_premium_admin',
        )
        self._write_tiers_yaml(
            """
- name: Premium
  stripe_key: premium
  stripe_price_id_monthly: ''
  stripe_price_id_yearly: ''
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='premium')
        self.assertEqual(
            tier.stripe_price_id_monthly, 'price_monthly_premium_admin'
        )
        self.assertEqual(
            tier.stripe_price_id_yearly, 'price_yearly_premium_admin'
        )

    def test_yaml_value_overwrites_existing_admin_set_db_value(self):
        Tier.objects.filter(slug='main').update(
            stripe_price_id_monthly='price_monthly_main_admin_edit',
            stripe_price_id_yearly='price_yearly_main_admin_edit',
        )
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  stripe_price_id_monthly: price_monthly_main_yaml_wins
  stripe_price_id_yearly: price_yearly_main_yaml_wins
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='main')
        self.assertEqual(
            tier.stripe_price_id_monthly, 'price_monthly_main_yaml_wins'
        )
        self.assertEqual(
            tier.stripe_price_id_yearly, 'price_yearly_main_yaml_wins'
        )

    def test_matches_each_known_paid_slug_by_stripe_key(self):
        Tier.objects.filter(slug__in=['basic', 'main', 'premium']).update(
            stripe_price_id_monthly='',
            stripe_price_id_yearly='',
        )
        self._write_tiers_yaml(
            """
- name: Basic
  stripe_key: basic
  stripe_price_id_monthly: price_basic_m
  stripe_price_id_yearly: price_basic_y
- name: Main
  stripe_key: main
  stripe_price_id_monthly: price_main_m
  stripe_price_id_yearly: price_main_y
- name: Premium
  stripe_key: premium
  stripe_price_id_monthly: price_premium_m
  stripe_price_id_yearly: price_premium_y
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        basic = Tier.objects.get(slug='basic')
        main = Tier.objects.get(slug='main')
        premium = Tier.objects.get(slug='premium')
        self.assertEqual(basic.stripe_price_id_monthly, 'price_basic_m')
        self.assertEqual(basic.stripe_price_id_yearly, 'price_basic_y')
        self.assertEqual(main.stripe_price_id_monthly, 'price_main_m')
        self.assertEqual(main.stripe_price_id_yearly, 'price_main_y')
        self.assertEqual(premium.stripe_price_id_monthly, 'price_premium_m')
        self.assertEqual(premium.stripe_price_id_yearly, 'price_premium_y')

    def test_idempotent_resync_keeps_same_values(self):
        Tier.objects.filter(slug='main').update(
            stripe_price_id_monthly='',
            stripe_price_id_yearly='',
        )
        yaml_blob = """
- name: Main
  stripe_key: main
  stripe_price_id_monthly: price_main_idempotent_m
  stripe_price_id_yearly: price_main_idempotent_y
"""
        self._write_tiers_yaml(yaml_blob)
        _sync_tiers_yaml(self.temp_dir.name)
        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='main')
        self.assertEqual(tier.stripe_price_id_monthly, 'price_main_idempotent_m')
        self.assertEqual(tier.stripe_price_id_yearly, 'price_main_idempotent_y')

    def test_unknown_stripe_key_logs_warning_and_other_entries_apply(self):
        Tier.objects.filter(slug='basic').update(
            stripe_price_id_monthly='',
            stripe_price_id_yearly='',
        )
        self._write_tiers_yaml(
            """
- name: Mystery
  stripe_key: not_a_real_slug
  stripe_price_id_monthly: price_mystery_m
  stripe_price_id_yearly: price_mystery_y
- name: Basic
  stripe_key: basic
  stripe_price_id_monthly: price_basic_after_unknown
  stripe_price_id_yearly: price_basic_after_unknown_y
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ) as captured:
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 2})
        basic = Tier.objects.get(slug='basic')
        self.assertEqual(
            basic.stripe_price_id_monthly, 'price_basic_after_unknown'
        )
        self.assertEqual(
            basic.stripe_price_id_yearly, 'price_basic_after_unknown_y'
        )
        joined = '\n'.join(captured.output)
        self.assertIn('not_a_real_slug', joined)
        self.assertIn('no matching payments.Tier row', joined)

    def test_entry_without_stripe_key_is_silently_skipped(self):
        # Free-tier-style entry: no stripe_key. Sync must not error and must
        # not touch any payments.Tier row.
        Tier.objects.filter(slug='free').update(
            stripe_price_id_monthly='',
            stripe_price_id_yearly='',
        )
        self._write_tiers_yaml(
            """
- name: Free
  price_monthly: 0
""",
        )

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 1})
        free = Tier.objects.get(slug='free')
        self.assertEqual(free.stripe_price_id_monthly, '')
        self.assertEqual(free.stripe_price_id_yearly, '')

    def test_site_config_blob_preserves_new_price_id_fields(self):
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  stripe_price_id_monthly: price_main_keep_m
  stripe_price_id_yearly: price_main_keep_y
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        config = SiteConfig.objects.get(key='tiers')
        self.assertEqual(
            config.data,
            [
                {
                    'name': 'Main',
                    'stripe_key': 'main',
                    'stripe_price_id_monthly': 'price_main_keep_m',
                    'stripe_price_id_yearly': 'price_main_keep_y',
                },
            ],
        )
