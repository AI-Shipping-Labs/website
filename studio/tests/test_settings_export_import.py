"""Tests for Studio settings export/import (issue #323).

Two buttons in the page header (`/studio/settings/`) drive the flow:

- ``GET /studio/settings/export/`` — JSON download of every known
  ``IntegrationSetting`` row + every ``SocialApp`` row for the three
  supported providers, in plaintext.
- ``POST /studio/settings/import/`` — upserts the entries, skipping
  unknown integration keys / providers with a warning.

Tests cover the round-trip, malformed-JSON / unknown-format-version
rejection, unknown-key skip-with-warning behaviour, and the staff gate.
"""

import json
import re

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from integrations.models import IntegrationSetting

User = get_user_model()


def _upload(name: str, body: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type='application/json')


class SettingsDashboardButtonsTest(TestCase):
    """Page header on /studio/settings/ shows Download + Upload."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_dashboard_shows_download_link(self):
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The download anchor points at the export endpoint.
        match = re.search(
            r'<a[^>]*data-testid="settings-download"[^>]*href="([^"]+)"',
            body,
        )
        # Fallback: order of attributes is not guaranteed by templates,
        # so allow href first.
        if match is None:
            match = re.search(
                r'<a[^>]*href="([^"]+)"[^>]*data-testid="settings-download"',
                body,
            )
        self.assertIsNotNone(match, 'Download link must be present in the page header')
        self.assertEqual(match.group(1), '/studio/settings/export/')
        self.assertIn('Download settings', body)

    def test_dashboard_shows_upload_form(self):
        response = self.client.get('/studio/settings/')
        body = response.content.decode()
        # An <input type="file" name="settings_file"> inside a form
        # POSTing to the import endpoint.
        self.assertIn('action="/studio/settings/import/"', body)
        self.assertIn('name="settings_file"', body)
        self.assertIn('Upload settings', body)


class SettingsExportTest(TestCase):
    """GET /studio/settings/export/ returns JSON in the expected shape."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_export_returns_json_file_attachment(self):
        response = self.client.get('/studio/settings/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        disposition = response['Content-Disposition']
        self.assertIn('attachment', disposition)
        # Filename pattern: aishippinglabs-settings-YYYYMMDD-HHMMSS.json
        self.assertRegex(
            disposition,
            r'filename="aishippinglabs-settings-\d{8}-\d{6}\.json"',
        )

    def test_export_contains_format_version_and_arrays(self):
        IntegrationSetting.objects.create(
            key='STRIPE_SECRET_KEY', value='sk_live_abc',
            is_secret=True, group='stripe',
        )
        SocialApp.objects.create(
            provider='google', name='Google',
            client_id='cid-123', secret='sec-456',
        )

        response = self.client.get('/studio/settings/export/')
        payload = json.loads(response.content.decode())

        self.assertEqual(payload['format_version'], 1)
        self.assertIn('integration_settings', payload)
        self.assertIn('auth_providers', payload)

        keys = {entry['key']: entry['value'] for entry in payload['integration_settings']}
        self.assertEqual(keys['STRIPE_SECRET_KEY'], 'sk_live_abc')

        providers = {p['provider']: p for p in payload['auth_providers']}
        self.assertEqual(providers['google']['client_id'], 'cid-123')
        # Plaintext: secret round-trips verbatim.
        self.assertEqual(providers['google']['secret'], 'sec-456')

    def test_export_excludes_unknown_integration_keys(self):
        # A row whose key is NOT in the registry must not leak into the
        # export — the schema guarantees we only ship known keys.
        IntegrationSetting.objects.create(
            key='LEGACY_DROPPED_KEY', value='nope',
            is_secret=False, group='unknown',
        )
        response = self.client.get('/studio/settings/export/')
        payload = json.loads(response.content.decode())
        keys = [entry['key'] for entry in payload['integration_settings']]
        self.assertNotIn('LEGACY_DROPPED_KEY', keys)


class SettingsImportTest(TestCase):
    """POST /studio/settings/import/ upserts and validates."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def _post_payload(self, payload: dict):
        body = json.dumps(payload).encode('utf-8')
        return self.client.post(
            '/studio/settings/import/',
            {'settings_file': _upload('settings.json', body)},
        )

    def test_round_trip_download_then_upload_into_empty_db(self):
        IntegrationSetting.objects.create(
            key='STRIPE_SECRET_KEY', value='sk_live_xyz',
            is_secret=True, group='stripe',
        )
        IntegrationSetting.objects.create(
            key='STRIPE_PUBLISHABLE_KEY', value='pk_live_xyz',
            is_secret=False, group='stripe',
        )
        SocialApp.objects.create(
            provider='google', name='Google',
            client_id='goog-id', secret='goog-secret',
        )

        # Download.
        export_response = self.client.get('/studio/settings/export/')
        exported = export_response.content

        # Wipe the DB to simulate a fresh environment.
        IntegrationSetting.objects.all().delete()
        SocialApp.objects.all().delete()

        # Upload the same bytes back.
        response = self.client.post(
            '/studio/settings/import/',
            {'settings_file': _upload('settings.json', exported)},
        )
        self.assertEqual(response.status_code, 302)

        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_SECRET_KEY').value,
            'sk_live_xyz',
        )
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_PUBLISHABLE_KEY').value,
            'pk_live_xyz',
        )
        google = SocialApp.objects.get(provider='google')
        self.assertEqual(google.client_id, 'goog-id')
        self.assertEqual(google.secret, 'goog-secret')

    def test_import_updates_existing_rows(self):
        IntegrationSetting.objects.create(
            key='STRIPE_SECRET_KEY', value='old-value',
            is_secret=True, group='stripe',
        )
        response = self._post_payload({
            'format_version': 1,
            'integration_settings': [
                {'key': 'STRIPE_SECRET_KEY', 'value': 'new-value'},
            ],
            'auth_providers': [],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_SECRET_KEY').value,
            'new-value',
        )

    def test_malformed_json_is_rejected_with_message(self):
        bad_body = b'{not valid json'
        response = self.client.post(
            '/studio/settings/import/',
            {'settings_file': _upload('settings.json', bad_body)},
        )
        self.assertEqual(response.status_code, 302)
        # No DB writes happened.
        self.assertEqual(IntegrationSetting.objects.count(), 0)
        self.assertEqual(SocialApp.objects.count(), 0)
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('valid JSON' in m for m in msgs))

    def test_unknown_format_version_is_rejected(self):
        # Existing row must be untouched.
        IntegrationSetting.objects.create(
            key='STRIPE_SECRET_KEY', value='preserved',
            is_secret=True, group='stripe',
        )
        response = self._post_payload({
            'format_version': 99,
            'integration_settings': [
                {'key': 'STRIPE_SECRET_KEY', 'value': 'should-not-apply'},
            ],
            'auth_providers': [],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_SECRET_KEY').value,
            'preserved',
        )
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('format_version' in m for m in msgs),
            f'Expected a format_version error in messages, got: {msgs!r}',
        )

    def test_unknown_integration_key_is_skipped_with_warning(self):
        response = self._post_payload({
            'format_version': 1,
            'integration_settings': [
                {'key': 'STRIPE_SECRET_KEY', 'value': 'sk_test'},
                {'key': 'TOTALLY_MADE_UP_KEY', 'value': 'whatever'},
            ],
            'auth_providers': [],
        })
        self.assertEqual(response.status_code, 302)
        # Known key applied.
        self.assertEqual(
            IntegrationSetting.objects.get(key='STRIPE_SECRET_KEY').value,
            'sk_test',
        )
        # Unknown key NOT created.
        self.assertFalse(
            IntegrationSetting.objects.filter(key='TOTALLY_MADE_UP_KEY').exists()
        )
        # Warning message names the skipped key.
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('TOTALLY_MADE_UP_KEY' in m for m in msgs),
            f'Expected skipped key in messages, got: {msgs!r}',
        )

    def test_unknown_auth_provider_is_skipped_with_warning(self):
        response = self._post_payload({
            'format_version': 1,
            'integration_settings': [],
            'auth_providers': [
                {'provider': 'twitter', 'name': 'Twitter', 'client_id': 'a', 'secret': 'b'},
            ],
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SocialApp.objects.filter(provider='twitter').exists())
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('twitter' in m for m in msgs))


class SettingsExportImportAccessControlTest(TestCase):
    """Both endpoints require staff."""

    @classmethod
    def setUpTestData(cls):
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_non_staff_cannot_export(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/settings/export/')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_export(self):
        response = self.client.get('/studio/settings/export/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_cannot_import(self):
        self.client.login(email='user@test.com', password='testpass')
        body = json.dumps({
            'format_version': 1,
            'integration_settings': [
                {'key': 'STRIPE_SECRET_KEY', 'value': 'sneaky'},
            ],
            'auth_providers': [],
        }).encode('utf-8')
        response = self.client.post(
            '/studio/settings/import/',
            {'settings_file': _upload('settings.json', body)},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            IntegrationSetting.objects.filter(key='STRIPE_SECRET_KEY').exists()
        )

    def test_anonymous_cannot_import(self):
        body = json.dumps({
            'format_version': 1,
            'integration_settings': [],
            'auth_providers': [],
        }).encode('utf-8')
        response = self.client.post(
            '/studio/settings/import/',
            {'settings_file': _upload('settings.json', body)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
