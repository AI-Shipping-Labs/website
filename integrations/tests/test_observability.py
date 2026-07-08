"""Tests for the prod-only Logfire observability gate (issue #813).

Covers the three-part AND gate (off under TESTING, off with no token, off
with the flag false, on only when all three hold) and the single gated
initialization contract: ``logfire.configure`` is never called under the
test suite / no token / disabled, and is called exactly once when the gate
is forced open. ``logfire.configure`` is always patched, so no Logfire
network traffic is emitted in CI.
"""

from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.services.observability import (
    init_logfire,
    logfire_is_enabled,
)

FAKE_TOKEN = 'pylf_fake_test_token'


class LogfireGateTest(TestCase):

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

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


class LogfireInitTest(TestCase):

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_configure_not_called_under_testing(self):
        # Default test-suite state: TESTING is True -> gate closed.
        with override_settings(LOGFIRE_TOKEN=FAKE_TOKEN, LOGFIRE_ENABLED='true'):
            with patch('logfire.configure') as mock_configure:
                self.assertFalse(init_logfire())
                mock_configure.assert_not_called()

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

        with patch('logfire.configure') as mock_configure, \
                patch.object(logfire, 'instrument_django'), \
                patch.object(logfire, 'instrument_httpx'), \
                patch.object(logfire, 'instrument_requests'), \
                patch.object(logfire, 'instrument_anthropic'):
            self.assertTrue(init_logfire())
            mock_configure.assert_called_once_with(
                token=FAKE_TOKEN, environment='staging',
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
