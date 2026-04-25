"""Tests for the SECRET_KEY / DEBUG environment-variable contract.

Production must hard-fail at startup if SECRET_KEY is missing, empty, or
equal to the in-tree dev fallback. Development must boot with a known
insecure default and a RuntimeWarning so the gap is visible in logs.

These tests exercise the pure helpers in ``website.settings`` directly so
we can sweep all four corners of the matrix (debug x set/unset) without
re-importing the whole settings module.
"""

import warnings

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from website import settings as website_settings
from website.settings import (
    DEV_FALLBACK_SECRET_KEY,
    _bool_env,
    _resolve_secret_key,
)


class ResolveSecretKeyProductionTest(SimpleTestCase):
    """``DEBUG=False`` must reject every unsafe SECRET_KEY shape."""

    def test_unset_secret_key_raises_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            _resolve_secret_key(debug=False, env={})

        message = str(ctx.exception)
        self.assertIn('SECRET_KEY', message)
        self.assertIn('ai-shipping-labs/django-secret-key', message)

    def test_empty_secret_key_raises_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            _resolve_secret_key(debug=False, env={'SECRET_KEY': ''})

        self.assertIn('SECRET_KEY', str(ctx.exception))

    def test_whitespace_only_secret_key_raises_improperly_configured(self):
        with self.assertRaises(ImproperlyConfigured):
            _resolve_secret_key(debug=False, env={'SECRET_KEY': '   '})

    def test_dev_fallback_value_is_rejected_in_production(self):
        with self.assertRaises(ImproperlyConfigured) as ctx:
            _resolve_secret_key(
                debug=False,
                env={'SECRET_KEY': DEV_FALLBACK_SECRET_KEY},
            )

        self.assertIn('SECRET_KEY', str(ctx.exception))

    def test_real_secret_key_is_returned_silently(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = _resolve_secret_key(
                debug=False,
                env={'SECRET_KEY': 'production-grade-secret'},
            )

        self.assertEqual(result, 'production-grade-secret')
        runtime_warnings = [
            w for w in caught if issubclass(w.category, RuntimeWarning)
        ]
        self.assertEqual(runtime_warnings, [])


class ResolveSecretKeyDevelopmentTest(SimpleTestCase):
    """``DEBUG=True`` must boot, but warn when the fallback is used."""

    def test_unset_secret_key_returns_fallback_with_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = _resolve_secret_key(debug=True, env={})

        self.assertEqual(result, DEV_FALLBACK_SECRET_KEY)
        runtime_warnings = [
            w for w in caught if issubclass(w.category, RuntimeWarning)
        ]
        self.assertEqual(len(runtime_warnings), 1)
        message = str(runtime_warnings[0].message)
        self.assertIn('SECRET_KEY', message)
        self.assertIn('insecure', message.lower())

    def test_empty_secret_key_returns_fallback_with_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = _resolve_secret_key(
                debug=True,
                env={'SECRET_KEY': ''},
            )

        self.assertEqual(result, DEV_FALLBACK_SECRET_KEY)
        runtime_warnings = [
            w for w in caught if issubclass(w.category, RuntimeWarning)
        ]
        self.assertEqual(len(runtime_warnings), 1)

    def test_explicit_dev_fallback_value_warns(self):
        # Even in dev mode, if the env literally contains the fallback
        # string, treat it as "unset" and warn so devs notice it leaked.
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = _resolve_secret_key(
                debug=True,
                env={'SECRET_KEY': DEV_FALLBACK_SECRET_KEY},
            )

        self.assertEqual(result, DEV_FALLBACK_SECRET_KEY)
        runtime_warnings = [
            w for w in caught if issubclass(w.category, RuntimeWarning)
        ]
        self.assertEqual(len(runtime_warnings), 1)

    def test_custom_secret_key_used_without_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            result = _resolve_secret_key(
                debug=True,
                env={'SECRET_KEY': 'my-local-key'},
            )

        self.assertEqual(result, 'my-local-key')
        runtime_warnings = [
            w for w in caught if issubclass(w.category, RuntimeWarning)
        ]
        self.assertEqual(runtime_warnings, [])


class BoolEnvTest(SimpleTestCase):
    def test_missing_env_uses_default(self):
        self.assertTrue(_bool_env('DEBUG', default=True, env={}))
        self.assertFalse(_bool_env('DEBUG', default=False, env={}))

    def test_truthy_values(self):
        for raw in ['1', 'true', 'True', 'TRUE', 'yes', 'YES']:
            with self.subTest(raw=raw):
                self.assertTrue(
                    _bool_env('DEBUG', default=False, env={'DEBUG': raw}),
                )

    def test_falsy_values(self):
        # Anything that is not in the truthy set is falsy, including the
        # empty string. The default is irrelevant when the var is set.
        for raw in ['0', 'false', 'False', 'no', 'NO', '', 'maybe']:
            with self.subTest(raw=raw):
                self.assertFalse(
                    _bool_env('DEBUG', default=True, env={'DEBUG': raw}),
                )

    def test_whitespace_values_are_normalised(self):
        self.assertTrue(
            _bool_env('DEBUG', default=False, env={'DEBUG': '  true  '}),
        )
        self.assertFalse(
            _bool_env('DEBUG', default=True, env={'DEBUG': '  false  '}),
        )


class SettingsModuleSmokeTest(SimpleTestCase):
    """The settings module loaded successfully under the test runner.

    The test suite runs with ``DEBUG=True`` (default) and typically with
    no ``SECRET_KEY`` env var set, so the dev-fallback path is exercised
    on every CI run. If the contract regresses (e.g. someone tightens
    the dev path to also raise), this test fails before any other test
    even gets a chance to run.
    """

    def test_secret_key_is_a_non_empty_string(self):
        self.assertIsInstance(website_settings.SECRET_KEY, str)
        self.assertGreater(len(website_settings.SECRET_KEY), 0)

    def test_dev_fallback_constant_is_recognisable(self):
        # Sanity: the constant is the prefix-tagged "insecure" string so
        # `manage.py check --deploy` will continue to flag it.
        self.assertTrue(
            DEV_FALLBACK_SECRET_KEY.startswith('django-insecure-'),
        )
