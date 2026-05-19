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


class TiersYamlAllFieldSyncTest(TestCase):
    """Cover write-through to the full set of yaml-managed Tier columns.

    Beyond the two stripe_price_id_* fields delivered by #682, the dispatcher
    must also write ``name``, ``level``, ``price_eur_month`` (yaml
    ``price_monthly``), ``price_eur_year`` (yaml ``price_annual``), and
    ``description`` whenever the yaml value is present and non-empty.
    """

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

    def test_yaml_name_writes_through_to_tier_name(self):
        Tier.objects.filter(slug='main').update(name='Main')
        self._write_tiers_yaml(
            """
- name: Main Plus
  stripe_key: main
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(Tier.objects.get(slug='main').name, 'Main Plus')

    def test_yaml_level_writes_through_to_tier_level(self):
        # Seed migration gives main level=20. Pick a value that doesn't collide
        # with any other seeded level (0/10/20/30).
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  level: 25
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(Tier.objects.get(slug='main').level, 25)

    def test_yaml_prices_write_through_to_tier_price_eur_columns(self):
        Tier.objects.filter(slug='main').update(
            price_eur_month=50, price_eur_year=500
        )
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  price_monthly: 55
  price_annual: 550
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='main')
        self.assertEqual(tier.price_eur_month, 55)
        self.assertEqual(tier.price_eur_year, 550)

    def test_yaml_description_writes_through_to_tier_description(self):
        Tier.objects.filter(slug='main').update(description='Original copy')
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  description: "New copy"
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(
            Tier.objects.get(slug='main').description, 'New copy'
        )

    def test_missing_yaml_keys_leave_all_db_columns_unchanged(self):
        Tier.objects.filter(slug='main').update(
            name='AdminName',
            level=22,
            price_eur_month=51,
            price_eur_year=510,
            description='AdminDesc',
        )
        # yaml only carries stripe_key plus a single field; everything else
        # must stay verbatim.
        self._write_tiers_yaml(
            """
- stripe_key: main
  stripe_price_id_monthly: price_main_only_m
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='main')
        self.assertEqual(tier.name, 'AdminName')
        self.assertEqual(tier.level, 22)
        self.assertEqual(tier.price_eur_month, 51)
        self.assertEqual(tier.price_eur_year, 510)
        self.assertEqual(tier.description, 'AdminDesc')
        self.assertEqual(tier.stripe_price_id_monthly, 'price_main_only_m')

    def test_empty_strings_and_nulls_leave_db_columns_unchanged(self):
        Tier.objects.filter(slug='main').update(
            name='KeepMe',
            description='KeepDesc',
            price_eur_month=50,
            price_eur_year=500,
        )
        self._write_tiers_yaml(
            """
- stripe_key: main
  name: ''
  description: ''
  price_monthly: null
  price_annual: null
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)

        tier = Tier.objects.get(slug='main')
        self.assertEqual(tier.name, 'KeepMe')
        self.assertEqual(tier.description, 'KeepDesc')
        self.assertEqual(tier.price_eur_month, 50)
        self.assertEqual(tier.price_eur_year, 500)

    def test_idempotent_resync_on_all_fields(self):
        self._write_tiers_yaml(
            """
- stripe_key: main
  name: Idem Main
  level: 21
  price_monthly: 60
  price_annual: 600
  description: Idem desc
  stripe_price_id_monthly: price_idem_m
  stripe_price_id_yearly: price_idem_y
""",
        )

        _sync_tiers_yaml(self.temp_dir.name)
        first = Tier.objects.get(slug='main')
        first_snapshot = {
            'name': first.name,
            'level': first.level,
            'price_eur_month': first.price_eur_month,
            'price_eur_year': first.price_eur_year,
            'description': first.description,
            'stripe_price_id_monthly': first.stripe_price_id_monthly,
            'stripe_price_id_yearly': first.stripe_price_id_yearly,
        }

        _sync_tiers_yaml(self.temp_dir.name)
        second = Tier.objects.get(slug='main')
        second_snapshot = {
            'name': second.name,
            'level': second.level,
            'price_eur_month': second.price_eur_month,
            'price_eur_year': second.price_eur_year,
            'description': second.description,
            'stripe_price_id_monthly': second.stripe_price_id_monthly,
            'stripe_price_id_yearly': second.stripe_price_id_yearly,
        }

        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(second_snapshot['name'], 'Idem Main')
        self.assertEqual(second_snapshot['level'], 21)
        self.assertEqual(second_snapshot['price_eur_month'], 60)
        self.assertEqual(second_snapshot['price_eur_year'], 600)
        self.assertEqual(second_snapshot['description'], 'Idem desc')


class TiersYamlValidationTest(TestCase):
    """Cover the pre-flight validation pass that rejects malformed yaml.

    On validation failure the dispatcher must:
      1. Log a WARNING with a specific reason.
      2. Leave every payments.Tier row untouched.
      3. Still write SiteConfig['tiers'] so the pricing page keeps rendering.
    """

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

    def _snapshot_tier(self, slug):
        t = Tier.objects.get(slug=slug)
        return {
            'name': t.name,
            'level': t.level,
            'price_eur_month': t.price_eur_month,
            'price_eur_year': t.price_eur_year,
            'description': t.description,
            'stripe_price_id_monthly': t.stripe_price_id_monthly,
            'stripe_price_id_yearly': t.stripe_price_id_yearly,
        }

    def test_duplicate_stripe_key_rejects_all_tier_writes(self):
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main One
  stripe_key: main
  level: 25
- name: Main Two
  stripe_key: main
  level: 26
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ) as captured:
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 2})
        self.assertEqual(self._snapshot_tier('main'), before)
        self.assertTrue(SiteConfig.objects.filter(key='tiers').exists())
        joined = '\n'.join(captured.output)
        self.assertIn('duplicate stripe_key', joined)

    def test_duplicate_level_rejects_all_tier_writes(self):
        before_main = self._snapshot_tier('main')
        before_basic = self._snapshot_tier('basic')
        self._write_tiers_yaml(
            """
- name: Basic
  stripe_key: basic
  level: 25
- name: Main
  stripe_key: main
  level: 25
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ) as captured:
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 2})
        self.assertEqual(self._snapshot_tier('main'), before_main)
        self.assertEqual(self._snapshot_tier('basic'), before_basic)
        joined = '\n'.join(captured.output)
        self.assertIn('duplicate level', joined)

    def test_negative_level_rejects_all_tier_writes(self):
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  level: -1
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ) as captured:
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 1})
        self.assertEqual(self._snapshot_tier('main'), before)
        joined = '\n'.join(captured.output)
        self.assertIn('level', joined)

    def test_non_int_level_rejects_all_tier_writes(self):
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  level: "ten"
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ):
            _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(self._snapshot_tier('main'), before)

    def test_zero_price_rejects_all_tier_writes(self):
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  price_monthly: 0
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ) as captured:
            result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 1})
        self.assertEqual(self._snapshot_tier('main'), before)
        joined = '\n'.join(captured.output)
        self.assertIn('price_monthly', joined)

    def test_negative_price_rejects_all_tier_writes(self):
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  price_monthly: -5
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ):
            _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(self._snapshot_tier('main'), before)

    def test_wrong_prefix_stripe_price_id_rejects_all_tier_writes(self):
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main
  stripe_key: main
  stripe_price_id_monthly: prod_xxx
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ) as captured:
            _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(self._snapshot_tier('main'), before)
        joined = '\n'.join(captured.output)
        self.assertIn('price_', joined)

    def test_validation_failure_still_writes_site_config(self):
        # Validation rejects the Tier-row pass, but SiteConfig['tiers'] must
        # still be written so /pricing keeps rendering.
        before = self._snapshot_tier('main')
        self._write_tiers_yaml(
            """
- name: Main A
  stripe_key: main
- name: Main B
  stripe_key: main
""",
        )

        with self.assertLogs(
            'integrations.services.github', level=logging.WARNING
        ):
            _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(self._snapshot_tier('main'), before)
        config = SiteConfig.objects.get(key='tiers')
        self.assertEqual(len(config.data), 2)
        self.assertEqual(config.data[0]['name'], 'Main A')
        self.assertEqual(config.data[1]['name'], 'Main B')

    def test_valid_payload_with_all_fields_passes_validation(self):
        # Sanity check the happy path: every field present, valid types,
        # unique stripe_keys and levels -> all rows updated.
        self._write_tiers_yaml(
            """
- name: Basic Plus
  stripe_key: basic
  level: 11
  price_monthly: 21
  price_annual: 210
  description: Basic plus copy
  stripe_price_id_monthly: price_basic_m_valid
  stripe_price_id_yearly: price_basic_y_valid
- name: Main Plus
  stripe_key: main
  level: 22
  price_monthly: 55
  price_annual: 550
  description: Main plus copy
  stripe_price_id_monthly: price_main_m_valid
  stripe_price_id_yearly: price_main_y_valid
""",
        )

        result = _sync_tiers_yaml(self.temp_dir.name)

        self.assertEqual(result, {'synced': True, 'count': 2})
        basic = Tier.objects.get(slug='basic')
        main = Tier.objects.get(slug='main')
        self.assertEqual(basic.name, 'Basic Plus')
        self.assertEqual(basic.level, 11)
        self.assertEqual(basic.price_eur_month, 21)
        self.assertEqual(basic.price_eur_year, 210)
        self.assertEqual(basic.description, 'Basic plus copy')
        self.assertEqual(basic.stripe_price_id_monthly, 'price_basic_m_valid')
        self.assertEqual(basic.stripe_price_id_yearly, 'price_basic_y_valid')
        self.assertEqual(main.name, 'Main Plus')
        self.assertEqual(main.level, 22)
        self.assertEqual(main.price_eur_month, 55)
        self.assertEqual(main.price_eur_year, 550)
        self.assertEqual(main.description, 'Main plus copy')
        self.assertEqual(main.stripe_price_id_monthly, 'price_main_m_valid')
        self.assertEqual(main.stripe_price_id_yearly, 'price_main_y_valid')
