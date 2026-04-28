"""Tests for per-field config source badges and DB-override semantics (issue #324).

Each integration field on ``/studio/settings/`` shows one of three source
badges (``DB override`` / ``env`` / ``not set``), an optional muted hint that
exposes the underlying env value when a DB override is shadowing it, and an
icon-sized "Clear override" button that deletes the DB row so the field falls
back to env.

These tests exercise the four source states, the secret-redaction rule for
the env hint, the DELETE-on-empty save semantics, and the integration with
``integrations.config.get_config`` so a cleared override actually changes
what runtime code reads.

We pin tests to ``STRIPE_PUBLISHABLE_KEY`` / ``STRIPE_SECRET_KEY`` because
they are real registry keys with realistic ``is_secret`` flags. We always
clear the corresponding ``os.environ`` entry in ``setUp`` so the dev shell's
``.env``-loaded value can't bleed into the test's expected source state.
"""

import os
import re

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting

User = get_user_model()

ENV_KEYS_UNDER_TEST = (
    'STRIPE_PUBLISHABLE_KEY',
    'STRIPE_SECRET_KEY',
)


def _clear_env(keys):
    """Pop the given keys from ``os.environ`` and remember their previous values
    so the test can restore them in ``addCleanup``."""
    saved = {k: os.environ.pop(k, None) for k in keys}
    return saved


def _restore_env(saved):
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)


class SettingsSourceBadgeTest(TestCase):
    """Per-field source badge renders one of three states."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        # Pop any pre-existing values so the test starts from a known state.
        saved = _clear_env(ENV_KEYS_UNDER_TEST)
        self.addCleanup(_restore_env, saved)

    def _field_html(self, body, key):
        """Return the HTML block for a single field, scoped by ``data-field-key``.

        The block runs from this field's opening ``<div data-field-key="..."``
        up to the next sibling field marker or to the form's submit button if
        this is the last field in the group. We don't try to match nested
        ``</div>`` because the field block contains several inner divs.
        """
        pattern = (
            r'<div data-field-key="' + re.escape(key) + r'">'
            r'(.*?)'
            r'(?=<div data-field-key=|<div class="mt-6 flex justify-end">)'
        )
        match = re.search(pattern, body, re.DOTALL)
        self.assertIsNotNone(match, f'Field block for {key} not found in dashboard HTML')
        return match.group(0)

    def test_env_only_shows_env_badge_no_clear_no_hint(self):
        """Field with only env value: blue env badge, no Clear button, no hint."""
        os.environ['STRIPE_PUBLISHABLE_KEY'] = 'pk_live_env_only'

        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        block = self._field_html(response.content.decode(), 'STRIPE_PUBLISHABLE_KEY')

        self.assertIn('data-source-badge="env"', block)
        self.assertIn('Source: env', block)
        self.assertNotIn('data-clear-override="STRIPE_PUBLISHABLE_KEY"', block)
        self.assertNotIn('data-env-hint=', block)

    def test_db_override_with_env_shows_db_badge_clear_and_hint(self):
        """Field with DB override + env: amber DB badge, Clear button, hint shows env value."""
        IntegrationSetting.objects.create(
            key='STRIPE_PUBLISHABLE_KEY', value='pk_live_db_override',
            is_secret=False, group='stripe',
        )
        os.environ['STRIPE_PUBLISHABLE_KEY'] = 'pk_live_env_value'

        response = self.client.get('/studio/settings/')
        block = self._field_html(response.content.decode(), 'STRIPE_PUBLISHABLE_KEY')

        self.assertIn('data-source-badge="db"', block)
        self.assertIn('Source: DB override', block)
        self.assertIn('data-clear-override="STRIPE_PUBLISHABLE_KEY"', block)
        # Hint is the present-env variant and contains the raw env value
        # (this field is not a secret).
        self.assertIn('data-env-hint="present"', block)
        self.assertIn('pk_live_env_value', block)
        self.assertIn('would apply if override cleared', block)

    def test_db_override_without_env_shows_no_env_hint(self):
        """DB override + no env: amber badge, Clear button, "no env value" hint."""
        IntegrationSetting.objects.create(
            key='STRIPE_PUBLISHABLE_KEY', value='pk_live_db_only',
            is_secret=False, group='stripe',
        )
        # setUp already popped STRIPE_PUBLISHABLE_KEY from os.environ.

        response = self.client.get('/studio/settings/')
        block = self._field_html(response.content.decode(), 'STRIPE_PUBLISHABLE_KEY')

        self.assertIn('data-source-badge="db"', block)
        self.assertIn('data-clear-override="STRIPE_PUBLISHABLE_KEY"', block)
        self.assertIn('data-env-hint="absent"', block)
        self.assertIn('No env value set', block)

    def test_not_set_shows_grey_badge_no_clear_no_hint(self):
        """Field with neither DB nor env: grey "not set" badge."""
        # setUp already popped STRIPE_PUBLISHABLE_KEY.

        response = self.client.get('/studio/settings/')
        block = self._field_html(response.content.decode(), 'STRIPE_PUBLISHABLE_KEY')

        self.assertIn('data-source-badge="none"', block)
        self.assertIn('Source: not set', block)
        self.assertNotIn('data-clear-override="STRIPE_PUBLISHABLE_KEY"', block)
        self.assertNotIn('data-env-hint=', block)

    def test_secret_field_redacts_env_value_in_hint(self):
        """Secret field with DB override + env: hint redacts to fixed-length stars."""
        IntegrationSetting.objects.create(
            key='STRIPE_SECRET_KEY', value='sk_live_db_override',
            is_secret=True, group='stripe',
        )
        # Use a recognisable env value so we can assert it does NOT leak.
        os.environ['STRIPE_SECRET_KEY'] = 'sk_live_env_supersecretvalue123'

        response = self.client.get('/studio/settings/')
        block = self._field_html(response.content.decode(), 'STRIPE_SECRET_KEY')

        self.assertIn('data-env-hint="present"', block)
        # 12 fixed stars — never the raw env value, never length-leaking.
        self.assertIn('************', block)
        self.assertNotIn('sk_live_env_supersecretvalue123', block)

    def test_post_empty_value_deletes_db_override(self):
        """POST with empty value for a DB-override key deletes the row."""
        IntegrationSetting.objects.create(
            key='STRIPE_PUBLISHABLE_KEY', value='pk_live_db_override',
            is_secret=False, group='stripe',
        )
        # Other Stripe keys are non-empty so they don't get incidentally cleared.
        post_data = {
            'STRIPE_SECRET_KEY': 'sk_keep',
            'STRIPE_WEBHOOK_SECRET': 'whsec_keep',
            'STRIPE_PUBLISHABLE_KEY': '',  # the one we are clearing
            'STRIPE_CHECKOUT_ENABLED': 'true',
            'STRIPE_CUSTOMER_PORTAL_URL': 'https://billing.example.com',
        }
        response = self.client.post('/studio/settings/stripe/save/', post_data)
        self.assertEqual(response.status_code, 302)
        # Row was deleted.
        self.assertFalse(
            IntegrationSetting.objects.filter(key='STRIPE_PUBLISHABLE_KEY').exists(),
        )
        # Sibling rows we set are still present.
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_SECRET_KEY').value,
            'sk_keep',
        )

    def test_clear_override_then_get_shows_env_badge(self):
        """After clearing a DB override, the next GET shows the env badge."""
        IntegrationSetting.objects.create(
            key='STRIPE_PUBLISHABLE_KEY', value='pk_live_db_override',
            is_secret=False, group='stripe',
        )
        os.environ['STRIPE_PUBLISHABLE_KEY'] = 'pk_live_env_value'

        # Clear via empty-value POST.
        self.client.post('/studio/settings/stripe/save/', {
            'STRIPE_SECRET_KEY': '',
            'STRIPE_WEBHOOK_SECRET': '',
            'STRIPE_PUBLISHABLE_KEY': '',
            'STRIPE_CUSTOMER_PORTAL_URL': '',
            # STRIPE_CHECKOUT_ENABLED unchecked → "false"
        })
        # Next GET shows env badge for the cleared key.
        response = self.client.get('/studio/settings/')
        block = self._field_html(response.content.decode(), 'STRIPE_PUBLISHABLE_KEY')
        self.assertIn('data-source-badge="env"', block)
        self.assertNotIn('data-clear-override="STRIPE_PUBLISHABLE_KEY"', block)

    def test_post_empty_for_unset_key_is_noop(self):
        """Saving empty for a key with no existing row doesn't error or create one."""
        post_data = {
            'STRIPE_SECRET_KEY': '',
            'STRIPE_WEBHOOK_SECRET': '',
            'STRIPE_PUBLISHABLE_KEY': '',
            'STRIPE_CUSTOMER_PORTAL_URL': '',
        }
        response = self.client.post('/studio/settings/stripe/save/', post_data)
        self.assertEqual(response.status_code, 302)
        # No row should exist for any of these keys.
        for key in ['STRIPE_SECRET_KEY', 'STRIPE_PUBLISHABLE_KEY']:
            self.assertFalse(IntegrationSetting.objects.filter(key=key).exists())


class SettingsTemplateCommentLeakageTest(TestCase):
    """Regression: multi-line ``{# #}`` blocks leak as visible text.

    Django's ``{# #}`` syntax is single-line only; multi-line uses leak into
    the rendered output. The settings page once shipped a multi-line
    ``{# Clear-override ... #}`` block in ``_integration_card.html`` that
    rendered 31 visible copies next to every field. This test fails fast if
    any comment fragments leak into the response body.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_no_django_comment_fragments_leak_into_rendered_settings(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn('{#', body)
        self.assertNotIn('{# Clear-override', body)


@override_settings(STRIPE_PUBLISHABLE_KEY='pk_env_underneath')
class SettingsClearOverrideRuntimeIntegrationTest(TestCase):
    """End-to-end: clearing a DB override actually changes what runtime reads.

    ``get_config`` checks DB cache → Django settings → ``os.environ``. We
    use ``@override_settings`` to pin the env-equivalent fallback so the
    assertion doesn't depend on the dev shell's ``.env`` values.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_clearing_db_override_makes_runtime_read_env(self):
        """get_config reads env value after the DB override row is deleted."""
        IntegrationSetting.objects.create(
            key='STRIPE_PUBLISHABLE_KEY', value='pk_db_winning',
            is_secret=False, group='stripe',
        )

        clear_config_cache()
        # Before clear: DB value wins.
        self.assertEqual(get_config('STRIPE_PUBLISHABLE_KEY'), 'pk_db_winning')

        # Submit empty-value save → row deleted, cache cleared by the view.
        self.client.post('/studio/settings/stripe/save/', {
            'STRIPE_SECRET_KEY': '',
            'STRIPE_WEBHOOK_SECRET': '',
            'STRIPE_PUBLISHABLE_KEY': '',
            'STRIPE_CUSTOMER_PORTAL_URL': '',
        })

        # After clear: runtime falls back to the Django-settings value
        # (which @override_settings has pinned to the env-equivalent).
        self.assertEqual(get_config('STRIPE_PUBLISHABLE_KEY'), 'pk_env_underneath')
