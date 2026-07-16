"""Canonical config-source regression tests for email sender selection."""

from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings

from email_app.services.email_classification import (
    DEFAULT_TRANSACTIONAL_FROM_EMAIL,
    LEGACY_FROM_KEY,
    TRANSACTIONAL_FROM_KEY,
    _has_runtime_value,
    get_sender_for_kind,
)


class RuntimeValueSourceTest(SimpleTestCase):
    @patch('email_app.services.email_classification.resolve_source')
    def test_unset_source_is_not_configured(self, resolve_source):
        resolve_source.return_value = None

        self.assertFalse(_has_runtime_value('UNSET_SENDER'))
        resolve_source.assert_called_once_with('UNSET_SENDER')

    @patch('email_app.services.email_classification.resolve_source')
    def test_db_and_environment_sources_are_configured(self, resolve_source):
        for source in ('db', 'env'):
            with self.subTest(source=source):
                resolve_source.return_value = source
                self.assertTrue(_has_runtime_value('CONFIGURED_SENDER'))

    @override_settings(TEST_SENDER='fallback@example.com')
    @patch('email_app.services.email_classification.resolve_source')
    def test_django_setting_equal_to_default_is_not_configured(
        self,
        resolve_source,
    ):
        resolve_source.return_value = 'django_settings'

        self.assertFalse(
            _has_runtime_value('TEST_SENDER', 'fallback@example.com'),
        )

    @override_settings(TEST_SENDER='configured@example.com')
    @patch('email_app.services.email_classification.resolve_source')
    def test_non_default_django_setting_is_configured(self, resolve_source):
        resolve_source.return_value = 'django_settings'

        self.assertTrue(
            _has_runtime_value('TEST_SENDER', 'fallback@example.com'),
        )

    @override_settings(TEST_SENDER='')
    @patch('email_app.services.email_classification.resolve_source')
    def test_empty_django_setting_is_not_configured(self, resolve_source):
        resolve_source.return_value = 'django_settings'

        self.assertFalse(
            _has_runtime_value('TEST_SENDER', 'fallback@example.com'),
        )


class SenderFallbackSourceTest(SimpleTestCase):
    @override_settings(
        SES_TRANSACTIONAL_FROM_EMAIL=DEFAULT_TRANSACTIONAL_FROM_EMAIL,
    )
    @patch('email_app.services.email_classification.get_config')
    @patch('email_app.services.email_classification.resolve_source')
    def test_default_django_setting_preserves_legacy_fallback(
        self,
        resolve_source,
        get_config,
    ):
        resolve_source.side_effect = lambda key: {
            TRANSACTIONAL_FROM_KEY: 'django_settings',
            LEGACY_FROM_KEY: 'env',
        }.get(key)
        get_config.return_value = 'legacy@example.com'

        self.assertEqual(
            get_sender_for_kind('transactional'),
            'legacy@example.com',
        )
        self.assertEqual(
            resolve_source.call_args_list,
            [call(TRANSACTIONAL_FROM_KEY), call(LEGACY_FROM_KEY)],
        )
        get_config.assert_called_once_with(
            LEGACY_FROM_KEY,
            DEFAULT_TRANSACTIONAL_FROM_EMAIL,
        )

    @override_settings(SES_TRANSACTIONAL_FROM_EMAIL='configured@example.com')
    @patch('email_app.services.email_classification.get_config')
    @patch('email_app.services.email_classification.resolve_source')
    def test_non_default_django_setting_keeps_primary_precedence(
        self,
        resolve_source,
        get_config,
    ):
        resolve_source.return_value = 'django_settings'
        get_config.return_value = 'configured@example.com'

        self.assertEqual(
            get_sender_for_kind('transactional'),
            'configured@example.com',
        )
        resolve_source.assert_called_once_with(TRANSACTIONAL_FROM_KEY)
        get_config.assert_called_once_with(
            TRANSACTIONAL_FROM_KEY,
            DEFAULT_TRANSACTIONAL_FROM_EMAIL,
        )
