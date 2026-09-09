"""Runtime Site-setting coverage for issue #1533."""

import json
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Token
from integrations.config import clear_config_cache
from integrations.models import ContentSource, IntegrationSetting, SyncLog
from integrations.services.github_sync.orchestration import _start_sync_log
from integrations.settings_registry import (
    SETTING_VALUE_TYPES,
    get_group_by_name,
)
from integrations.sync_config import (
    sync_queued_threshold_minutes,
    sync_running_threshold_minutes,
)
from studio.views.sync import _run_sync_watchdog
from studio.worker_health import expect_worker, get_worker_status

User = get_user_model()

RUNTIME_KEYS = (
    'SYNC_QUEUED_THRESHOLD_MINUTES',
    'SYNC_RUNNING_THRESHOLD_MINUTES',
    'EXPECT_WORKER',
)


class RuntimeSiteRegistryTest(SimpleTestCase):
    def test_registry_metadata_types_and_docs_are_complete(self):
        entries = {
            item['key']: item for item in get_group_by_name('site')['keys']
        }
        expected = {
            'SYNC_QUEUED_THRESHOLD_MINUTES': ('10', False, 'integer'),
            'SYNC_RUNNING_THRESHOLD_MINUTES': ('30', False, 'integer'),
            'EXPECT_WORKER': ('true', True, 'boolean'),
        }
        site_docs = (
            Path(settings.BASE_DIR) / '_docs' / 'integrations' / 'site.md'
        ).read_text(encoding='utf-8')

        for key, (default, is_boolean, value_type) in expected.items():
            with self.subTest(key=key):
                entry = entries[key]
                self.assertFalse(entry['is_secret'])
                self.assertTrue(entry['optional'])
                self.assertEqual(entry['default'], default)
                self.assertEqual(entry.get('is_boolean', False), is_boolean)
                self.assertEqual(SETTING_VALUE_TYPES[key], value_type)
                self.assertEqual(entry['value_type'], value_type)
                self.assertEqual(
                    entry['docs_url'],
                    f'_docs/integrations/site.md#{key.lower()}',
                )
                self.assertIn(f'## {key}\n', site_docs)


class RuntimeThresholdResolutionTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    @override_settings(
        SYNC_QUEUED_THRESHOLD_MINUTES='91',
        SYNC_RUNNING_THRESHOLD_MINUTES='92',
    )
    def test_db_overrides_take_effect_without_process_restart(self):
        IntegrationSetting.objects.create(
            key='SYNC_QUEUED_THRESHOLD_MINUTES', value='4', group='site',
        )
        IntegrationSetting.objects.create(
            key='SYNC_RUNNING_THRESHOLD_MINUTES', value='7', group='site',
        )
        clear_config_cache()

        self.assertEqual(sync_queued_threshold_minutes(), 4)
        self.assertEqual(sync_running_threshold_minutes(), 7)

    def test_invalid_db_values_fall_back_without_raising(self):
        cases = (
            ('SYNC_QUEUED_THRESHOLD_MINUTES', 'not-an-int', 10),
            ('SYNC_QUEUED_THRESHOLD_MINUTES', '0', 10),
            ('SYNC_QUEUED_THRESHOLD_MINUTES', '-2', 10),
            ('SYNC_RUNNING_THRESHOLD_MINUTES', 'not-an-int', 30),
            ('SYNC_RUNNING_THRESHOLD_MINUTES', '0', 30),
            ('SYNC_RUNNING_THRESHOLD_MINUTES', '-2', 30),
        )
        for key, raw_value, expected in cases:
            with self.subTest(key=key, raw_value=raw_value):
                IntegrationSetting.objects.update_or_create(
                    key=key,
                    defaults={'value': raw_value, 'group': 'site'},
                )
                clear_config_cache()
                resolved = (
                    sync_queued_threshold_minutes()
                    if key == 'SYNC_QUEUED_THRESHOLD_MINUTES'
                    else sync_running_threshold_minutes()
                )
                self.assertEqual(resolved, expected)


class RuntimeSyncCallerTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    @override_settings(
        SYNC_QUEUED_THRESHOLD_MINUTES='60',
        SYNC_RUNNING_THRESHOLD_MINUTES='60',
    )
    def test_watchdog_uses_db_thresholds_and_reports_resolved_minutes(self):
        queued_source = ContentSource.objects.create(
            repo_name='example/queued', last_sync_status='queued',
        )
        running_source = ContentSource.objects.create(
            repo_name='example/running', last_sync_status='running',
        )
        queued_log = SyncLog.objects.create(
            source=queued_source, status='queued',
        )
        running_log = SyncLog.objects.create(
            source=running_source, status='running',
        )
        SyncLog.objects.filter(pk=queued_log.pk).update(
            started_at=timezone.now() - timedelta(minutes=6),
        )
        SyncLog.objects.filter(pk=running_log.pk).update(
            started_at=timezone.now() - timedelta(minutes=9),
        )
        IntegrationSetting.objects.create(
            key='SYNC_QUEUED_THRESHOLD_MINUTES', value='5', group='site',
        )
        IntegrationSetting.objects.create(
            key='SYNC_RUNNING_THRESHOLD_MINUTES', value='8', group='site',
        )
        clear_config_cache()

        _run_sync_watchdog()

        queued_log.refresh_from_db()
        running_log.refresh_from_db()
        self.assertEqual(queued_log.status, 'failed')
        self.assertEqual(running_log.status, 'failed')
        self.assertEqual(
            queued_log.errors[-1]['error'],
            'Worker did not pick up task within 5 minutes',
        )
        self.assertEqual(
            running_log.errors[-1]['error'],
            'Worker did not report completion within 8 minutes',
        )

    @override_settings(SYNC_QUEUED_THRESHOLD_MINUTES='2')
    def test_worker_claims_log_inside_db_configured_queued_window(self):
        source = ContentSource.objects.create(repo_name='example/reused-log')
        queued_log = SyncLog.objects.create(source=source, status='queued')
        SyncLog.objects.filter(pk=queued_log.pk).update(
            started_at=timezone.now() - timedelta(minutes=7),
        )
        IntegrationSetting.objects.create(
            key='SYNC_QUEUED_THRESHOLD_MINUTES', value='10', group='site',
        )
        clear_config_cache()

        claimed_log = _start_sync_log(source, batch_id=uuid.uuid4())

        self.assertEqual(claimed_log.pk, queued_log.pk)
        self.assertEqual(claimed_log.status, 'running')


class ExpectWorkerRuntimeTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    @override_settings(EXPECT_WORKER='true')
    def test_only_case_insensitive_false_disables(self):
        cases = (
            ('false', False),
            ('FALSE', False),
            ('true', True),
            ('1', True),
            ('', True),
            ('anything-else', True),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                IntegrationSetting.objects.update_or_create(
                    key='EXPECT_WORKER',
                    defaults={'value': value, 'group': 'site'},
                )
                clear_config_cache()
                self.assertEqual(expect_worker(), expected)

    @override_settings(EXPECT_WORKER='true')
    def test_db_override_immediately_suppresses_worker_missing_status(self):
        IntegrationSetting.objects.create(
            key='EXPECT_WORKER', value='false', group='site',
        )
        clear_config_cache()

        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            status = get_worker_status()
        self.assertFalse(status['alive'])
        self.assertFalse(status['expect_worker'])


class RuntimeSettingsStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='runtime-site-studio@test.com',
            password='testpass',
            is_staff=True,
        )

    def setUp(self):
        clear_config_cache()
        self.client.login(email=self.staff.email, password='testpass')

    def tearDown(self):
        clear_config_cache()

    @override_settings(
        SYNC_QUEUED_THRESHOLD_MINUTES='41',
        SYNC_RUNNING_THRESHOLD_MINUTES='42',
        EXPECT_WORKER='true',
    )
    def test_save_applies_values_and_clear_restores_settings_fallback(self):
        response = self.client.post('/studio/settings/site/save/', {
            'SYNC_QUEUED_THRESHOLD_MINUTES': '4',
            'SYNC_RUNNING_THRESHOLD_MINUTES': '8',
        })
        self.assertRedirects(
            response, '/studio/settings/#site', fetch_redirect_response=False,
        )
        self.assertEqual(sync_queued_threshold_minutes(), 4)
        self.assertEqual(sync_running_threshold_minutes(), 8)
        self.assertFalse(expect_worker())

        response = self.client.post('/studio/settings/site/save/', {
            'SYNC_QUEUED_THRESHOLD_MINUTES': '',
            'SYNC_RUNNING_THRESHOLD_MINUTES': '',
            'clear_override': 'EXPECT_WORKER',
        })
        self.assertRedirects(
            response, '/studio/settings/#site', fetch_redirect_response=False,
        )
        self.assertFalse(
            IntegrationSetting.objects.filter(key__in=RUNTIME_KEYS).exists(),
        )
        self.assertEqual(sync_queued_threshold_minutes(), 41)
        self.assertEqual(sync_running_threshold_minutes(), 42)
        self.assertTrue(expect_worker())

    def test_invalid_integer_rejects_every_site_group_write(self):
        IntegrationSetting.objects.create(
            key='SYNC_RUNNING_THRESHOLD_MINUTES', value='12', group='site',
        )
        before = dict(
            IntegrationSetting.objects.filter(group='site').values_list(
                'key', 'value',
            )
        )

        response = self.client.post(
            '/studio/settings/site/save/',
            {
                'SYNC_QUEUED_THRESHOLD_MINUTES': 'not-an-integer',
                'SYNC_RUNNING_THRESHOLD_MINUTES': '6',
            },
            follow=True,
        )

        self.assertContains(
            response,
            'SYNC_QUEUED_THRESHOLD_MINUTES must be a valid integer. '
            'No settings were saved.',
        )
        after = dict(
            IntegrationSetting.objects.filter(group='site').values_list(
                'key', 'value',
            )
        )
        self.assertEqual(after, before)


class RuntimeSettingsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='runtime-site-api@test.com', is_staff=True,
        )
        cls.member = User.objects.create_user(email='runtime-member@test.com')
        cls.staff_token = Token.objects.create(user=cls.staff, name='runtime')
        cls.member_token = Token(
            key='runtime-site-non-staff-token',
            user=cls.member,
            name='runtime',
        )
        Token.objects.bulk_create([cls.member_token])

    def setUp(self):
        clear_config_cache()
        self.auth = {
            'HTTP_AUTHORIZATION': f'Token {self.staff_token.key}',
        }

    def tearDown(self):
        clear_config_cache()

    def test_get_lists_metadata_sources_without_values(self):
        IntegrationSetting.objects.create(
            key='SYNC_QUEUED_THRESHOLD_MINUTES',
            value='do-not-echo-queued',
            group='site',
        )
        clear_config_cache()

        response = self.client.get('/api/integrations/settings', **self.auth)
        entries = {
            item['key']: item for item in response.json()['settings']
        }

        for key in RUNTIME_KEYS:
            with self.subTest(key=key):
                self.assertEqual(entries[key]['group'], 'site')
                self.assertEqual(
                    entries[key]['is_boolean'], key == 'EXPECT_WORKER',
                )
                self.assertIn(
                    entries[key]['source'],
                    {'db', 'env', 'django_settings', 'default'},
                )
                self.assertNotIn('value', entries[key])
        self.assertNotContains(response, 'do-not-echo-queued')

    @override_settings(
        SYNC_QUEUED_THRESHOLD_MINUTES='51',
        SYNC_RUNNING_THRESHOLD_MINUTES='52',
        EXPECT_WORKER='true',
    )
    def test_post_sets_and_clears_all_three_runtime_keys(self):
        response = self.client.post(
            '/api/integrations/settings',
            data=json.dumps({'updates': [
                {'key': 'SYNC_QUEUED_THRESHOLD_MINUTES', 'value': '6'},
                {'key': 'SYNC_RUNNING_THRESHOLD_MINUTES', 'value': '9'},
                {'key': 'EXPECT_WORKER', 'value': False},
            ]}),
            content_type='application/json',
            **self.auth,
        )
        self.assertEqual(response.json(), {'status': 'ok', 'updated': 3})
        self.assertEqual(sync_queued_threshold_minutes(), 6)
        self.assertEqual(sync_running_threshold_minutes(), 9)
        self.assertFalse(expect_worker())

        response = self.client.post(
            '/api/integrations/settings',
            data=json.dumps({'updates': [
                {'key': key, 'value': ''} for key in RUNTIME_KEYS
            ]}),
            content_type='application/json',
            **self.auth,
        )
        self.assertEqual(response.json(), {'status': 'ok', 'updated': 3})
        self.assertFalse(
            IntegrationSetting.objects.filter(key__in=RUNTIME_KEYS).exists(),
        )
        self.assertEqual(sync_queued_threshold_minutes(), 51)
        self.assertEqual(sync_running_threshold_minutes(), 52)
        self.assertTrue(expect_worker())

    def test_non_staff_token_cannot_update_runtime_keys(self):
        response = self.client.post(
            '/api/integrations/settings',
            data=json.dumps({'updates': [
                {'key': 'EXPECT_WORKER', 'value': False},
            ]}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {self.member_token.key}',
        )

        self.assertEqual(response.status_code, 401)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='EXPECT_WORKER').exists(),
        )
