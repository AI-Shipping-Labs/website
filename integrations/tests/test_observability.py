"""Tests for the prod-only Logfire observability gate.

Covers the three-part AND gate and issue #1527's split startup contract:
app initialization uses settings/env/defaults without ORM or shared-cache
access, while the serving entrypoint may read Studio values after setup.
``logfire.configure`` is always patched, so CI emits no Logfire traffic.
"""

import builtins
import importlib
import os
from unittest.mock import patch

from django.apps import apps as django_apps
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from integrations.apps import IntegrationsConfig
from integrations.config import reset_local_config_cache
from integrations.models import IntegrationSetting
from integrations.services import observability
from integrations.services.observability import (
    init_logfire,
    logfire_is_enabled,
)

FAKE_TOKEN = 'pylf_fake_test_token'


class ResetsObservabilityState:

    def setUp(self):
        super().setUp()
        reset_local_config_cache()
        observability._logfire_initialized = False
        self.addCleanup(reset_local_config_cache)
        self.addCleanup(setattr, observability, '_logfire_initialized', False)


class LogfireGateTest(ResetsObservabilityState, SimpleTestCase):

    def test_gate_closed_under_testing_regardless_of_token_and_flag(self):
        # The live test suite always runs with TESTING True, so even with a
        # token and the flag forced on the gate must stay closed.
        with override_settings(LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true'):
            self.assertTrue(settings.TESTING)
            self.assertFalse(logfire_is_enabled())

    @override_settings(TESTING=False, LOGFIRE_ENABLED='true', LOGFIRE_TOKEN='')
    def test_gate_closed_without_token_even_when_flag_on(self):
        self.assertFalse(logfire_is_enabled())

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='false')
    def test_gate_closed_when_flag_off_even_with_token(self):
        self.assertFalse(logfire_is_enabled())

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true')
    def test_gate_open_only_when_not_testing_token_present_flag_on(self):
        self.assertTrue(logfire_is_enabled())


class LogfireInitTest(ResetsObservabilityState, SimpleTestCase):

    def test_configure_not_called_under_testing(self):
        # Default test-suite state: TESTING is True -> gate closed.
        with override_settings(LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true'):
            real_import = builtins.__import__
            with patch('builtins.__import__', wraps=real_import) as import_:
                self.assertFalse(init_logfire())

        imported_modules = [call.args[0] for call in import_.call_args_list]
        self.assertNotIn('logfire', imported_modules)

    @override_settings(TESTING=False, LOGFIRE_ENABLED='true', LOGFIRE_TOKEN='')
    def test_configure_not_called_without_token(self):
        with patch('logfire.configure') as mock_configure:
            self.assertFalse(init_logfire())
            mock_configure.assert_not_called()

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='false')
    def test_configure_not_called_when_disabled(self):
        with patch('logfire.configure') as mock_configure:
            self.assertFalse(init_logfire())
            mock_configure.assert_not_called()

    @override_settings(
        TESTING=False,
        LOGFIRE_TOKEN=FAKE_TOKEN,
        LOGFIRE_ENABLED='true',
        LOGFIRE_ENVIRONMENT='staging',
    )
    def test_configure_called_once_with_token_and_environment_when_gate_open(self):
        import logfire

        with patch.dict(
            os.environ,
            {
                'LOGFIRE_TOKEN': 'environment-token-must-not-win',
                'LOGFIRE_ENABLED': 'false',
                'LOGFIRE_ENVIRONMENT': 'environment-must-not-win',
            },
        ), patch('logfire.configure') as mock_configure, \
                patch.object(logfire, 'instrument_django'), \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'):
            self.assertTrue(init_logfire())
            mock_configure.assert_called_once_with(
                token=FAKE_TOKEN, environment='staging',
            )

    @override_settings(
        TESTING=False,
        LOGFIRE_TOKEN=None,
        LOGFIRE_ENABLED=None,
        LOGFIRE_ENVIRONMENT=None,
    )
    def test_boot_config_falls_back_from_settings_to_environment(self):
        import logfire

        with patch.dict(
            os.environ,
            {
                'LOGFIRE_TOKEN': FAKE_TOKEN,
                'LOGFIRE_ENABLED': 'yes',
                'LOGFIRE_ENVIRONMENT': 'env-stage',
            },
        ), patch('logfire.configure') as configure, \
                patch.object(logfire, 'instrument_django'), \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'):
            self.assertTrue(init_logfire())

        configure.assert_called_once_with(
            token=FAKE_TOKEN,
            environment='env-stage',
        )

    @override_settings(
        TESTING=False,
        LOGFIRE_TOKEN=FAKE_TOKEN,
        LOGFIRE_ENABLED=True,
        LOGFIRE_ENVIRONMENT=None,
    )
    def test_boot_config_uses_registered_environment_default(self):
        import logfire

        with patch.dict(os.environ, {}, clear=True), \
                patch('logfire.configure') as configure, \
                patch.object(logfire, 'instrument_django'), \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'):
            self.assertTrue(init_logfire())

        configure.assert_called_once_with(
            token=FAKE_TOKEN,
            environment='production',
        )

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true')
    def test_successful_initialization_is_idempotent(self):
        import logfire

        with patch('logfire.configure') as configure, \
                patch.object(logfire, 'instrument_django'), \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'):
            self.assertTrue(init_logfire())
            self.assertFalse(init_logfire(use_runtime_config=True))

        configure.assert_called_once_with(
            token=FAKE_TOKEN,
            environment='production',
        )

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true')
    def test_instrumentors_enabled_when_gate_open(self):
        import logfire

        with patch('logfire.configure'), \
                patch.object(logfire, 'instrument_django') as m_django, \
                patch.object(logfire, 'instrument_httpx') as m_httpx, \
                patch.object(logfire, 'instrument_requests') as m_requests, \
                patch.object(logfire, 'instrument_anthropic') as m_anthropic:
            self.assertTrue(init_logfire())
            m_django.assert_called_once()
            m_httpx.assert_called_once()
            m_requests.assert_called_once()
            m_anthropic.assert_called_once()

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true')
    def test_boot_does_not_crash_when_configure_raises(self):
        # A malformed token / misconfiguration surfaces as an exception from
        # configure(); init_logfire must catch it and return False, not raise.
        with patch('logfire.configure', side_effect=RuntimeError('bad token')):
            self.assertFalse(init_logfire())

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true')
    def test_boot_does_not_crash_when_an_instrumentor_missing(self):
        # An optional instrumentor absent from the installed Logfire must not
        # disable the rest or crash boot.
        import logfire

        with patch('logfire.configure'), \
                patch.object(logfire, 'instrument_django') as m_django, \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'):
            # Simulate the anthropic instrumentor not existing in this version.
            had_anthropic = hasattr(logfire, 'instrument_anthropic')
            saved = getattr(logfire, 'instrument_anthropic', None)
            if had_anthropic:
                delattr(logfire, 'instrument_anthropic')
            try:
                self.assertTrue(init_logfire())
                m_django.assert_called_once()
            finally:
                if had_anthropic:
                    logfire.instrument_anthropic = saved

    @override_settings(TESTING=False, LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true')
    def test_boot_logs_and_continues_when_an_instrumentor_fails(self):
        import logfire

        with patch('logfire.configure'), \
                patch.object(
                    logfire,
                    'instrument_django',
                    side_effect=RuntimeError('instrumentor failed'),
                ), patch.object(logfire, 'instrument_httpx') as httpx, \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'), \
                self.assertLogs(
                    'integrations.services.observability',
                    level='WARNING',
                ):
            self.assertTrue(init_logfire())

        self.assertEqual(httpx.call_count, 1)


class LogfireAppStartupTest(ResetsObservabilityState, SimpleTestCase):

    @override_settings(
        TESTING=False,
        LOGFIRE_TOKEN='',
        LOGFIRE_ENABLED='',
        LOGFIRE_ENVIRONMENT='production',
    )
    def test_ready_does_not_touch_runtime_config_orm_or_shared_cache(self):
        config = IntegrationsConfig(
            'integrations',
            importlib.import_module('integrations'),
        )

        with patch.dict(os.environ, {}, clear=True), \
                patch.object(django_apps, 'ready', False), \
                patch.object(observability, 'get_config') as get_config, \
                patch.object(IntegrationSetting, 'objects') as objects, \
                patch(
                    'integrations.shared_cache.get_shared_cache',
                ) as get_shared_cache, patch(
                    'integrations.shared_cache.set_shared_cache',
                ) as set_shared_cache:
            config.ready()

        get_config.assert_not_called()
        self.assertEqual(objects.mock_calls, [])
        get_shared_cache.assert_not_called()
        set_shared_cache.assert_not_called()


class LogfireRuntimeConfigTest(ResetsObservabilityState, TestCase):

    @override_settings(
        TESTING=False,
        LOGFIRE_TOKEN='',
        LOGFIRE_ENABLED='',
        LOGFIRE_ENVIRONMENT='production',
    )
    def test_db_only_values_apply_in_post_setup_runtime_pass(self):
        IntegrationSetting.objects.bulk_create([
            IntegrationSetting(
                key='LOGFIRE_TOKEN',
                value=FAKE_TOKEN,
                is_secret=True,
                group='observability',
            ),
            IntegrationSetting(
                key='LOGFIRE_ENABLED',
                value='true',
                group='observability',
            ),
            IntegrationSetting(
                key='LOGFIRE_ENVIRONMENT',
                value='studio-stage',
                group='observability',
            ),
        ])
        import logfire

        with patch.dict(os.environ, {}, clear=True), \
                patch('logfire.configure') as configure, \
                patch.object(logfire, 'instrument_django'), \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'):
            self.assertFalse(init_logfire())
            self.assertTrue(init_logfire(use_runtime_config=True))

        configure.assert_called_once_with(
            token=FAKE_TOKEN,
            environment='studio-stage',
        )
