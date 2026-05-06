"""Tests for Studio content sources export/import (issue #436).

Two buttons in the page header (`/studio/sync/`) drive the flow:

- ``GET /studio/sync/export/`` — JSON download of every ``ContentSource``
  row (operator-config fields only) in plaintext.
- ``POST /studio/sync/import/`` — upserts the entries on ``repo_name``
  without ever touching runtime-state fields.

Tests cover the round-trip, malformed-JSON / unknown-format-version
rejection, missing-repo_name skip-with-warning, runtime-state preservation,
and the staff gate.
"""

import json
import re

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from integrations.models import ContentSource
from studio.services.content_sources_io import (
    FORMAT_VERSION,
    ImportResult,
    apply_import,
    build_export,
)
from studio.services.content_sources_io import (
    ImportError as ContentSourcesImportError,
)

User = get_user_model()


def _upload(name: str, body: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, body, content_type='application/json')


class ContentSourcesBuildExportTest(TestCase):
    """``build_export()`` returns a deterministic, narrow snapshot."""

    def test_format_version_is_one(self):
        self.assertEqual(FORMAT_VERSION, 1)
        payload = build_export()
        self.assertEqual(payload['format_version'], 1)

    def test_top_level_keys_are_exactly_three(self):
        payload = build_export()
        self.assertEqual(
            set(payload.keys()),
            {'format_version', 'exported_at', 'content_sources'},
        )

    def test_exported_at_is_iso8601_utc(self):
        payload = build_export()
        # Format: YYYY-MM-DDTHH:MM:SSZ — strict pattern, no offset.
        self.assertRegex(
            payload['exported_at'],
            r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$',
        )

    def test_entry_keys_are_exactly_four(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='shh',
            is_private=True,
            max_files=500,
        )
        payload = build_export()
        self.assertEqual(len(payload['content_sources']), 1)
        entry = payload['content_sources'][0]
        self.assertEqual(
            set(entry.keys()),
            {'repo_name', 'webhook_secret', 'is_private', 'max_files'},
        )

    def test_excludes_runtime_state_fields(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='shh',
            last_synced_commit='deadbeef' * 5,
            last_sync_status='success',
            last_sync_log='lots of detail',
            sync_requested=True,
            last_synced_at=timezone.now(),
            last_webhook_at=timezone.now(),
        )
        payload = build_export()
        entry = payload['content_sources'][0]
        forbidden_keys = {
            'id', 'last_synced_at', 'last_sync_status', 'last_sync_log',
            'last_synced_commit', 'sync_locked_at', 'sync_requested',
            'last_webhook_at', 'created_at', 'updated_at',
        }
        leaked = forbidden_keys & set(entry.keys())
        self.assertFalse(
            leaked,
            f'Runtime-state fields leaked into export: {sorted(leaked)!r}',
        )

    def test_rows_ordered_by_repo_name(self):
        ContentSource.objects.create(repo_name='zzz/last')
        ContentSource.objects.create(repo_name='aaa/first')
        ContentSource.objects.create(repo_name='mmm/middle')
        payload = build_export()
        names = [row['repo_name'] for row in payload['content_sources']]
        self.assertEqual(names, ['aaa/first', 'mmm/middle', 'zzz/last'])

    def test_secret_round_trips_in_plaintext(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='very-secret-value',
        )
        payload = build_export()
        self.assertEqual(
            payload['content_sources'][0]['webhook_secret'],
            'very-secret-value',
        )


class ContentSourcesApplyImportTest(TestCase):
    """``apply_import()`` upserts and validates without touching runtime state."""

    def test_creates_new_rows(self):
        result = apply_import({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'webhook_secret': 's1', 'max_files': 100},
                {'repo_name': 'org/b', 'webhook_secret': 's2', 'is_private': True},
            ],
        })
        self.assertEqual(result.created, 2)
        self.assertEqual(result.updated, 0)
        a = ContentSource.objects.get(repo_name='org/a')
        self.assertEqual(a.webhook_secret, 's1')
        self.assertEqual(a.max_files, 100)
        b = ContentSource.objects.get(repo_name='org/b')
        self.assertEqual(b.webhook_secret, 's2')
        self.assertTrue(b.is_private)

    def test_updates_existing_rows(self):
        ContentSource.objects.create(
            repo_name='org/a', webhook_secret='old', max_files=50,
        )
        result = apply_import({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'webhook_secret': 'new', 'max_files': 200},
            ],
        })
        self.assertEqual(result.created, 0)
        self.assertEqual(result.updated, 1)
        a = ContentSource.objects.get(repo_name='org/a')
        self.assertEqual(a.webhook_secret, 'new')
        self.assertEqual(a.max_files, 200)

    def test_runtime_state_preserved_on_update(self):
        """Issue #436: the import never writes to last_synced_at / status / commit."""
        original_synced_at = timezone.now()
        ContentSource.objects.create(
            repo_name='org/a',
            webhook_secret='s',
            last_synced_at=original_synced_at,
            last_sync_status='success',
            last_synced_commit='deadbeef' * 5,
        )
        apply_import({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'webhook_secret': 'rotated'},
            ],
        })
        row = ContentSource.objects.get(repo_name='org/a')
        # Operator-config field updated...
        self.assertEqual(row.webhook_secret, 'rotated')
        # ...runtime-state untouched.
        self.assertEqual(row.last_synced_at, original_synced_at)
        self.assertEqual(row.last_sync_status, 'success')
        self.assertEqual(row.last_synced_commit, 'deadbeef' * 5)

    def test_raises_on_non_dict_payload(self):
        with self.assertRaises(ContentSourcesImportError) as ctx:
            apply_import(['not', 'a', 'dict'])
        self.assertIn('JSON object', str(ctx.exception))

    def test_raises_on_wrong_format_version(self):
        with self.assertRaises(ContentSourcesImportError) as ctx:
            apply_import({'format_version': 99, 'content_sources': []})
        self.assertIn('99', str(ctx.exception))
        self.assertIn('format_version', str(ctx.exception))

    def test_raises_on_missing_format_version(self):
        with self.assertRaises(ContentSourcesImportError):
            apply_import({'content_sources': []})

    def test_raises_when_content_sources_is_not_a_list(self):
        with self.assertRaises(ContentSourcesImportError) as ctx:
            apply_import({'format_version': 1, 'content_sources': 'oops'})
        self.assertIn('content_sources', str(ctx.exception))

    def test_skips_entries_with_missing_repo_name(self):
        result = apply_import({
            'format_version': 1,
            'content_sources': [
                {'webhook_secret': 'orphan'},
                {'repo_name': 'org/ok', 'webhook_secret': 's'},
                {'repo_name': '', 'webhook_secret': 'empty'},
                {'repo_name': 123, 'webhook_secret': 'wrong-type'},
            ],
        })
        self.assertEqual(result.created, 1)
        self.assertEqual(len(result.skipped_repos), 3)
        # Placeholders are 1-based indices, not echoed payload text.
        self.assertEqual(
            result.skipped_repos,
            ['<entry #1>', '<entry #3>', '<entry #4>'],
        )
        # The good row landed.
        self.assertTrue(ContentSource.objects.filter(repo_name='org/ok').exists())

    def test_silently_ignores_non_dict_entries(self):
        result = apply_import({
            'format_version': 1,
            'content_sources': [None, 'string', 42, {'repo_name': 'org/a'}],
        })
        self.assertEqual(result.created, 1)
        self.assertEqual(result.skipped_repos, [])

    def test_unknown_keys_in_entry_are_ignored(self):
        # Forward-compat: a future field like ``branch`` must not crash.
        result = apply_import({
            'format_version': 1,
            'content_sources': [
                {
                    'repo_name': 'org/a',
                    'webhook_secret': 's',
                    'branch': 'main',
                    'flux_capacitor': True,
                },
            ],
        })
        self.assertEqual(result.created, 1)
        self.assertTrue(ContentSource.objects.filter(repo_name='org/a').exists())

    def test_webhook_secret_absent_preserves_existing(self):
        ContentSource.objects.create(repo_name='org/a', webhook_secret='kept')
        apply_import({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'is_private': True},
            ],
        })
        row = ContentSource.objects.get(repo_name='org/a')
        self.assertEqual(row.webhook_secret, 'kept')
        self.assertTrue(row.is_private)

    def test_webhook_secret_empty_string_round_trips(self):
        ContentSource.objects.create(repo_name='org/a', webhook_secret='had')
        apply_import({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'webhook_secret': ''},
            ],
        })
        row = ContentSource.objects.get(repo_name='org/a')
        self.assertEqual(row.webhook_secret, '')

    def test_max_files_defaults_to_1000_when_absent(self):
        apply_import({
            'format_version': 1,
            'content_sources': [{'repo_name': 'org/a'}],
        })
        self.assertEqual(
            ContentSource.objects.get(repo_name='org/a').max_files,
            1000,
        )

    def test_max_files_negative_clamped_to_zero(self):
        apply_import({
            'format_version': 1,
            'content_sources': [{'repo_name': 'org/a', 'max_files': -7}],
        })
        self.assertEqual(
            ContentSource.objects.get(repo_name='org/a').max_files, 0,
        )

    def test_max_files_non_coercible_falls_back_to_default(self):
        apply_import({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'max_files': 'not-a-number'},
            ],
        })
        self.assertEqual(
            ContentSource.objects.get(repo_name='org/a').max_files,
            1000,
        )


class ContentSourcesImportResultTest(TestCase):
    """The ``ImportResult`` dataclass has the documented public surface."""

    def test_default_fields(self):
        r = ImportResult()
        self.assertEqual(r.created, 0)
        self.assertEqual(r.updated, 0)
        self.assertEqual(r.skipped_repos, [])

    def test_skipped_repos_is_independent_per_instance(self):
        # field(default_factory=list) — guard against shared mutable default.
        a = ImportResult()
        b = ImportResult()
        a.skipped_repos.append('x')
        self.assertEqual(b.skipped_repos, [])


class ContentSourcesDashboardButtonsTest(TestCase):
    """Page header on /studio/sync/ shows Download + Upload + caption."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_dashboard_shows_download_link(self):
        response = self.client.get('/studio/sync/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The download anchor points at the export endpoint.
        match = re.search(
            r'<a[^>]*data-testid="content-sources-download"[^>]*href="([^"]+)"',
            body,
        )
        if match is None:
            match = re.search(
                r'<a[^>]*href="([^"]+)"[^>]*data-testid="content-sources-download"',
                body,
            )
        self.assertIsNotNone(
            match,
            'Download link must be present in the page header',
        )
        self.assertEqual(match.group(1), '/studio/sync/export/')
        self.assertIn('Download content sources', body)

    def test_dashboard_shows_upload_form(self):
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        self.assertIn('action="/studio/sync/import/"', body)
        self.assertIn('enctype="multipart/form-data"', body)
        self.assertIn('name="content_sources_file"', body)
        self.assertIn('Upload content sources', body)

    def test_dashboard_shows_sensitivity_caption(self):
        response = self.client.get('/studio/sync/')
        body = response.content.decode()
        self.assertIn(
            'Includes webhook secrets — treat the file as sensitive.',
            body,
        )


class ContentSourcesExportViewTest(TestCase):
    """``GET /studio/sync/export/`` returns JSON with the right headers."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_returns_json_attachment_with_dated_filename(self):
        response = self.client.get('/studio/sync/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        disposition = response['Content-Disposition']
        self.assertIn('attachment', disposition)
        self.assertRegex(
            disposition,
            r'filename="aishippinglabs-content-sources-\d{8}-\d{6}\.json"',
        )

    def test_body_is_valid_json_with_format_version_one(self):
        ContentSource.objects.create(
            repo_name='org/a', webhook_secret='s1', is_private=True,
            max_files=200,
        )
        response = self.client.get('/studio/sync/export/')
        payload = json.loads(response.content.decode())
        self.assertEqual(payload['format_version'], 1)
        self.assertEqual(len(payload['content_sources']), 1)
        entry = payload['content_sources'][0]
        self.assertEqual(entry['repo_name'], 'org/a')
        self.assertEqual(entry['webhook_secret'], 's1')


class ContentSourcesImportViewTest(TestCase):
    """``POST /studio/sync/import/`` upserts and surfaces flash messages."""

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
            '/studio/sync/import/',
            {'content_sources_file': _upload('content-sources.json', body)},
        )

    def test_round_trip_download_then_upload_into_empty_db(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='secret-1',
            is_private=True,
            max_files=1000,
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/courses',
            webhook_secret='secret-2',
            is_private=True,
            max_files=500,
        )

        export_response = self.client.get('/studio/sync/export/')
        exported = export_response.content

        ContentSource.objects.all().delete()

        response = self.client.post(
            '/studio/sync/import/',
            {'content_sources_file': _upload('cs.json', exported)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/studio/sync/')

        a = ContentSource.objects.get(repo_name='AI-Shipping-Labs/content')
        self.assertEqual(a.webhook_secret, 'secret-1')
        self.assertEqual(a.max_files, 1000)
        b = ContentSource.objects.get(repo_name='AI-Shipping-Labs/courses')
        self.assertEqual(b.webhook_secret, 'secret-2')
        self.assertEqual(b.max_files, 500)

    def test_success_flash_includes_counts_and_security_disclaimer(self):
        response = self._post_payload({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/a', 'webhook_secret': 's1'},
                {'repo_name': 'org/b', 'webhook_secret': 's2'},
            ],
        })
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('2 created, 0 updated' in m for m in msgs),
            f'Expected the success summary in messages, got: {msgs!r}',
        )
        self.assertTrue(
            any('webhook secrets' in m for m in msgs),
            f'Expected the sensitivity disclaimer in messages, got: {msgs!r}',
        )

    def test_no_file_uploaded_flashes_error(self):
        response = self.client.post('/studio/sync/import/', {})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/studio/sync/')
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(any('No file uploaded' in m for m in msgs))

    def test_non_utf8_upload_flashes_error(self):
        bad = bytes([0xff, 0xfe, 0xfd])
        response = self.client.post(
            '/studio/sync/import/',
            {'content_sources_file': _upload('cs.json', bad)},
        )
        self.assertEqual(response.status_code, 302)
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('UTF-8' in m for m in msgs),
            f'Expected UTF-8 error in messages, got: {msgs!r}',
        )

    def test_malformed_json_flashes_error_and_no_writes(self):
        bad_body = b'{not valid json'
        response = self.client.post(
            '/studio/sync/import/',
            {'content_sources_file': _upload('cs.json', bad_body)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContentSource.objects.count(), 0)
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('valid JSON' in m for m in msgs),
            f'Expected JSON parse error in messages, got: {msgs!r}',
        )

    def test_unknown_format_version_blocked_and_no_writes(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            webhook_secret='preserved-secret',
        )
        response = self._post_payload({
            'format_version': 99,
            'content_sources': [
                {
                    'repo_name': 'AI-Shipping-Labs/content',
                    'webhook_secret': 'should-not-apply',
                },
            ],
        })
        self.assertEqual(response.status_code, 302)
        # Row preserved.
        self.assertEqual(
            ContentSource.objects.get(
                repo_name='AI-Shipping-Labs/content',
            ).webhook_secret,
            'preserved-secret',
        )
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('format_version' in m and '1' in m for m in msgs),
            f'Expected format_version error in messages, got: {msgs!r}',
        )

    def test_top_level_not_dict_flashes_error(self):
        body = json.dumps(['not', 'a', 'dict']).encode('utf-8')
        response = self.client.post(
            '/studio/sync/import/',
            {'content_sources_file': _upload('cs.json', body)},
        )
        self.assertEqual(response.status_code, 302)
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('JSON object' in m for m in msgs),
            f'Expected non-dict error in messages, got: {msgs!r}',
        )

    def test_content_sources_not_list_flashes_error(self):
        response = self._post_payload({
            'format_version': 1,
            'content_sources': 'oops',
        })
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('content_sources' in m for m in msgs),
            f'Expected content_sources type error in messages, got: {msgs!r}',
        )

    def test_skipped_repos_surfaces_warning(self):
        response = self._post_payload({
            'format_version': 1,
            'content_sources': [
                {'webhook_secret': 'no-name'},
                {'repo_name': 'org/ok', 'webhook_secret': 's'},
            ],
        })
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('Skipped' in m and '<entry #1>' in m for m in msgs),
            f'Expected skipped-repos warning, got: {msgs!r}',
        )

    def test_zero_recognised_entries_flashes_info(self):
        response = self._post_payload({
            'format_version': 1,
            'content_sources': [],
        })
        msgs = [str(m) for m in response.wsgi_request._messages]
        self.assertTrue(
            any('no recognised entries' in m for m in msgs),
            f'Expected info flash for empty payload, got: {msgs!r}',
        )


class ContentSourcesAccessControlTest(TestCase):
    """Both endpoints require staff."""

    @classmethod
    def setUpTestData(cls):
        cls.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass', is_staff=False,
        )

    def test_non_staff_cannot_export(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.get('/studio/sync/export/')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_export(self):
        response = self.client.get('/studio/sync/export/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_cannot_import(self):
        self.client.login(email='user@test.com', password='testpass')
        body = json.dumps({
            'format_version': 1,
            'content_sources': [
                {'repo_name': 'org/sneaky', 'webhook_secret': 's'},
            ],
        }).encode('utf-8')
        response = self.client.post(
            '/studio/sync/import/',
            {'content_sources_file': _upload('cs.json', body)},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            ContentSource.objects.filter(repo_name='org/sneaky').exists()
        )

    def test_anonymous_cannot_import(self):
        body = json.dumps({
            'format_version': 1, 'content_sources': [],
        }).encode('utf-8')
        response = self.client.post(
            '/studio/sync/import/',
            {'content_sources_file': _upload('cs.json', body)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class ContentSourcesUrlOrderingTest(TestCase):
    """``sync/export/`` and ``sync/import/`` must not be swallowed by
    the catch-all ``<uuid:source_id>/`` and ``<path:repo_name>/`` routes.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='admin@test.com', password='testpass')

    def test_export_path_resolves_to_export_view(self):
        # Hits the export view, not 404 from the uuid converter and not the
        # repo_name-trigger view. ``200`` + ``application/json`` is the
        # signature of the export view.
        response = self.client.get('/studio/sync/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')

    def test_import_path_routes_to_import_view(self):
        response = self.client.post('/studio/sync/import/', {})
        # No file → flash error + redirect to dashboard. If the
        # ``<path:repo_name>/`` route had captured ``import/`` we'd get
        # something else (404 or trigger view).
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/studio/sync/')
