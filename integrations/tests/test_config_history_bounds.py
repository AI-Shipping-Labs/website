"""Bounded SettingChange reads during a cold integration-config load."""

from datetime import timedelta
from unittest.mock import patch

from community_base.config.models import SettingChange
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from integrations import config as config_module
from integrations.config import get_config, reset_local_config_cache
from integrations.models import IntegrationSetting


class ConfigHistoryBoundsTest(TestCase):
    TOMBSTONE_KEY = "PERF_HISTORY_TOMBSTONE"
    REVIVAL_KEY = "PERF_HISTORY_REVIVAL"
    HISTORY_PER_KEY = 100

    @classmethod
    def setUpTestData(cls):
        IntegrationSetting.objects.bulk_create(
            [
                IntegrationSetting(
                    key=cls.TOMBSTONE_KEY,
                    value="stale-tombstoned-donor",
                    group="test",
                ),
                IntegrationSetting(
                    key=cls.REVIVAL_KEY,
                    value="live-revived-donor",
                    group="test",
                ),
            ]
        )
        history = SettingChange.objects.bulk_create(
            [
                SettingChange(
                    setting_key=key,
                    old_value=None,
                    new_value=f"history-{index}",
                    actor_ref="test:config-history",
                )
                for key in (cls.TOMBSTONE_KEY, cls.REVIVAL_KEY)
                for index in range(cls.HISTORY_PER_KEY)
            ]
        )
        older = timezone.now() - timedelta(days=1)
        SettingChange.objects.filter(pk__in=[change.pk for change in history]).update(
            created_at=older
        )

        # Equal timestamps resolve by descending pk. For the first key the
        # later row is a tombstone; for the second it is a non-null revival.
        tied = [
            SettingChange.objects.create(
                setting_key=cls.TOMBSTONE_KEY,
                old_value=None,
                new_value="superseded-value",
                actor_ref="test:config-history",
            ),
            SettingChange.objects.create(
                setting_key=cls.TOMBSTONE_KEY,
                old_value="superseded-value",
                new_value=None,
                actor_ref="test:config-history",
            ),
            SettingChange.objects.create(
                setting_key=cls.REVIVAL_KEY,
                old_value="older-value",
                new_value=None,
                actor_ref="test:config-history",
            ),
            SettingChange.objects.create(
                setting_key=cls.REVIVAL_KEY,
                old_value=None,
                new_value="latest-package-value",
                actor_ref="test:config-history",
            ),
        ]
        tie_time = timezone.now()
        SettingChange.objects.filter(pk__in=[change.pk for change in tied]).update(
            created_at=tie_time
        )

    def setUp(self):
        reset_local_config_cache()

    def tearDown(self):
        reset_local_config_cache()

    def test_cold_cache_fetches_one_latest_change_per_donor_key(self):
        donor_keys = {self.TOMBSTONE_KEY, self.REVIVAL_KEY}
        statements = []

        def capture_statement(execute, sql, params, many, context):
            statements.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(capture_statement), self.assertNumQueries(1):
            latest = config_module._latest_donor_changes(donor_keys)

        self.assertEqual(
            latest,
            [
                (self.REVIVAL_KEY, "latest-package-value"),
                (self.TOMBSTONE_KEY, None),
            ],
        )
        self.assertEqual(len(latest), len(donor_keys))
        self.assertEqual(len(statements), 1)
        sql, params = statements[0]
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            raw_rows = cursor.fetchmany(3)
        self.assertEqual(
            len(raw_rows),
            2,
            "the database returned history rows that Python later discarded",
        )
        self.assertEqual(
            SettingChange.objects.filter(setting_key__in=donor_keys).count(),
            2 * self.HISTORY_PER_KEY + 4,
        )

        fetched_by_cold_load = []
        original = config_module._latest_donor_changes

        def capture_latest(keys):
            rows = original(keys)
            fetched_by_cold_load.extend(rows)
            return rows

        with (
            patch.object(config_module, "_latest_donor_changes", side_effect=capture_latest),
            patch.object(config_module, "_read_stamp", return_value=None),
        ):
            self.assertEqual(
                get_config(self.TOMBSTONE_KEY, "fallback", use_settings=False),
                "fallback",
            )
            self.assertEqual(
                get_config(self.REVIVAL_KEY, "fallback", use_settings=False),
                "live-revived-donor",
            )

        self.assertEqual(fetched_by_cold_load, latest)
