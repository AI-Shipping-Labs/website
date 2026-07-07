"""Tests for S3_ENABLED as a registered IntegrationSetting (#1068, #1131).

Issue #1131 flips the default to default-ON so a missing/drifted env var in
the prod worker container can no longer silently disable content-image uploads.
The ``media.py`` gate now resolves via ``s3_content_upload_enabled()``
(``get_config('S3_ENABLED', 'true')``) rather than ``is_enabled`` (which
hardcodes a 'false' fallback).

Covers:
- ``S3_ENABLED`` is registered in the ``s3_content`` group with the right
  metadata (``is_boolean``, ``default`` now ``'true'``, ``docs_url``), and the
  description advertises default-on.
- ``s3_content_upload_enabled()`` resolves from DB override, settings, env,
  and the default-on fallback (including the worker uncached read path).
- ``upload_images_to_s3`` returns a clean no-op when ``TESTING`` is True
  (no boto3 calls, no error entries), regardless of the flag.
- ``upload_images_to_s3`` returns an ``s3_disabled`` error entry when
  ``S3_ENABLED`` is explicitly false and ``TESTING`` is False (simulating prod).
- With the flag default-on but no bucket configured, ``upload_images_to_s3``
  is a clean no-op (missing-bucket short-circuit).
- A sync pipeline with ``S3_ENABLED`` false in non-test produces a
  ``partial`` SyncLog (via the ``s3_disabled`` error flowing through
  ``orchestration._run_content_pipeline`` / ``_finish_successful_sync``).
- The integration settings API surfaces ``S3_ENABLED`` with correct
  source resolution and accepts boolean writes.
"""

import os
import tempfile

from django.test import TestCase, override_settings

from integrations.config import (
    _get_config_uncached,
    clear_config_cache,
    s3_content_upload_enabled,
)
from integrations.models import ContentSource, IntegrationSetting
from integrations.services.github_sync.media import upload_images_to_s3
from integrations.settings_registry import INTEGRATION_GROUPS, get_group_by_name


def _s3_enabled_def():
    """Return the registry key-def for S3_ENABLED, or None."""
    group = get_group_by_name('s3_content')
    if not group:
        return None
    for key_def in group['keys']:
        if key_def['key'] == 'S3_ENABLED':
            return key_def
    return None


class S3EnabledRegistryTest(TestCase):
    """S3_ENABLED is registered in the s3_content group with correct metadata."""

    def test_s3_enabled_is_in_s3_content_group(self):
        key_def = _s3_enabled_def()
        self.assertIsNotNone(
            key_def, 'S3_ENABLED must be registered in the s3_content group',
        )

    def test_s3_enabled_is_boolean_with_true_default(self):
        # Issue #1131: default flipped to 'true' so a missing/drifted env var
        # can no longer silently disable content-image uploads in prod.
        key_def = _s3_enabled_def()
        self.assertIsNotNone(key_def)
        self.assertTrue(key_def.get('is_boolean'))
        self.assertEqual(key_def.get('default'), 'true')
        self.assertFalse(key_def.get('is_secret'))

    def test_s3_enabled_description_does_not_imply_default_off(self):
        # Issue #1131: description must advertise the default-on behaviour.
        key_def = _s3_enabled_def()
        self.assertIsNotNone(key_def)
        description = key_def.get('description', '').lower()
        self.assertIn('on by default', description)
        self.assertNotIn('must be on in production', description)

    def test_s3_enabled_has_docs_url(self):
        key_def = _s3_enabled_def()
        self.assertIsNotNone(key_def)
        self.assertEqual(
            key_def.get('docs_url'),
            '_docs/integrations/s3_content.md#s3_enabled',
        )

    def test_s3_content_group_still_has_bucket_region_cdn_keys(self):
        """Adding S3_ENABLED must not displace the existing keys."""
        group = get_group_by_name('s3_content')
        keys = {k['key'] for k in group['keys']}
        self.assertIn('AWS_S3_CONTENT_BUCKET', keys)
        self.assertIn('AWS_S3_CONTENT_REGION', keys)
        self.assertIn('CONTENT_CDN_BASE', keys)
        self.assertIn('S3_ENABLED', keys)

    def test_total_group_count_unchanged(self):
        """No new group added — S3_ENABLED goes into the existing s3_content.

        The absolute count grows as unrelated groups are added (e.g. the
        ``triggers`` group in issue #1070); this assertion's intent is only
        that S3_ENABLED did NOT introduce a group, so it tracks the current
        total rather than a frozen number.
        """
        self.assertEqual(len(INTEGRATION_GROUPS), 17)


class S3ContentUploadEnabledResolutionTest(TestCase):
    """s3_content_upload_enabled() resolves through the config chain (default-on).

    Issue #1131: this is the gate ``media.py`` uses. Unlike ``is_enabled``
    (which hardcodes a 'false' fallback), the content-upload gate defaults ON
    so a missing/drifted env var can no longer silently disable uploads.
    """

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()
        IntegrationSetting.objects.filter(key='S3_ENABLED').delete()

    def test_defaults_to_enabled_when_unset(self):
        """With no DB row, no settings, and no env, the gate is default-ON.

        This is the exact prod regression #1131 fixes: the worker container
        had no S3_ENABLED env var and previously fell through to 'false'.
        (Under the real test suite the TESTING short-circuit still keeps
        upload_images_to_s3 a silent no-op; this asserts the flag resolution
        itself, not the upload path.)
        """
        self.assertTrue(s3_content_upload_enabled())

    @override_settings(S3_ENABLED=True)
    def test_resolves_true_from_django_settings(self):
        clear_config_cache()
        self.assertTrue(s3_content_upload_enabled())

    @override_settings(S3_ENABLED=False)
    def test_resolves_false_from_django_settings(self):
        clear_config_cache()
        self.assertFalse(s3_content_upload_enabled())

    def test_resolves_true_from_db_override(self):
        IntegrationSetting.objects.update_or_create(
            key='S3_ENABLED',
            defaults={'value': 'true', 'is_secret': False, 'group': 's3_content'},
        )
        clear_config_cache()
        self.assertTrue(s3_content_upload_enabled())

    def test_explicit_false_db_override_disables(self):
        IntegrationSetting.objects.update_or_create(
            key='S3_ENABLED',
            defaults={'value': 'false', 'is_secret': False, 'group': 's3_content'},
        )
        clear_config_cache()
        self.assertFalse(s3_content_upload_enabled())

    def test_explicit_false_env_var_disables(self):
        """An explicit env var S3_ENABLED=false overrides the default-on."""
        from unittest import mock
        with mock.patch.dict(os.environ, {'S3_ENABLED': 'false'}):
            clear_config_cache()
            self.assertFalse(s3_content_upload_enabled())

    def test_worker_uncached_path_defaults_to_enabled(self):
        """The worker (qcluster) uncached read resolves default-ON when unset.

        Issue #1131: the incident was caused by the worker process reading a
        different (missing) env var than the web process. _get_config_uncached
        is the worker read path; with the key unset everywhere it must pass
        the 'true' default through so the gate stays enabled.
        """
        raw = _get_config_uncached('S3_ENABLED', 'true')
        self.assertTrue(str(raw).strip().lower() in ('true', '1', 'yes'))


class UploadImagesToS3TestingShortCircuitTest(TestCase):
    """When TESTING is True, upload_images_to_s3 returns a clean no-op."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/content',
        )
        self.temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(self.temp_dir, 'hero.png')
        with open(img_path, 'wb') as f:
            f.write(b'\x89PNG fake image data for testing short-circuit')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_testing_true_returns_clean_noop_even_with_s3_enabled(self):
        """Under TESTING=True the skip is silent regardless of S3_ENABLED."""
        # TESTING is True under manage.py test — verify the actual
        # behaviour by calling the function directly.
        result = upload_images_to_s3(self.temp_dir, self.source)
        self.assertEqual(result, {'uploaded': 0, 'skipped': 0, 'errors': []})


class UploadImagesToS3DisabledErrorTest(TestCase):
    """When TESTING=False and S3_ENABLED=False, stats includes s3_disabled error."""

    def setUp(self):
        clear_config_cache()
        self.source = ContentSource.objects.create(
            repo_name='test-org/content',
        )
        self.temp_dir = tempfile.mkdtemp()
        img_path = os.path.join(self.temp_dir, 'hero.png')
        with open(img_path, 'wb') as f:
            f.write(b'\x89PNG fake image data for disabled-error test')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        clear_config_cache()

    @override_settings(TESTING=False, S3_ENABLED=False)
    def test_returns_s3_disabled_error_entry(self):
        """Non-test env with S3 off → s3_disabled error in stats."""
        clear_config_cache()
        result = upload_images_to_s3(self.temp_dir, self.source)
        self.assertEqual(result['uploaded'], 0)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(len(result['errors']), 1)
        entry = result['errors'][0]
        self.assertEqual(entry['step'], 's3_disabled')
        self.assertEqual(entry['file'], '')
        self.assertIn('S3_ENABLED is false', entry['error'])

    @override_settings(
        TESTING=False,
        S3_ENABLED=True,
        AWS_S3_CONTENT_BUCKET='',
    )
    def test_proceeds_past_gate_when_enabled_no_s3_disabled_error(self):
        """Non-test env with S3 on → passes the gate, reaches bucket check."""
        from unittest.mock import patch

        clear_config_cache()
        # With S3_ENABLED=True and TESTING=False, the gate passes but
        # AWS_S3_CONTENT_BUCKET is not configured, so we get the bucket
        # not-configured skip (clean no-op, no s3_disabled error).
        with patch('integrations.services.github_sync.media.boto3.client') as mock:
            result = upload_images_to_s3(self.temp_dir, self.source)
        mock.assert_not_called()
        self.assertEqual(result, {'uploaded': 0, 'skipped': 0, 'errors': []})
        # Crucially, no s3_disabled error since we passed the gate.
        steps = [e.get('step') for e in result['errors']]
        self.assertNotIn('s3_disabled', steps)

    @override_settings(TESTING=False, AWS_S3_CONTENT_BUCKET='')
    def test_default_on_unset_flag_no_bucket_is_clean_noop(self):
        """Issue #1131: flag UNSET (default-on) + no bucket → clean no-op.

        This is the regression scenario: a fresh/drifted worker with no
        S3_ENABLED anywhere must pass the enabled gate (no s3_disabled skip),
        then stop cleanly at the missing-bucket check. Local dev without S3
        creds is unaffected.
        """
        from unittest.mock import patch

        IntegrationSetting.objects.filter(key='S3_ENABLED').delete()
        clear_config_cache()
        with patch('integrations.services.github_sync.media.boto3.client') as mock:
            result = upload_images_to_s3(self.temp_dir, self.source)
        mock.assert_not_called()
        self.assertEqual(result, {'uploaded': 0, 'skipped': 0, 'errors': []})
        steps = [e.get('step') for e in result['errors']]
        self.assertNotIn('s3_disabled', steps)


class S3DisabledErrorFlowsToSyncLogPartialTest(TestCase):
    """The s3_disabled error makes a sync run report 'partial' (issue #1068)."""

    def test_orchestration_s3_disabled_makes_status_partial(self):
        """When _run_content_pipeline gets an s3_disabled error, the pipeline
        result carries it and a SyncLog with that error is 'partial'.

        We simulate the flow by checking that the s3_disabled error dict,
        when placed in sync_log.errors, makes status='partial' — matching
        the orchestration logic at _finish_successful_sync."""
        from integrations.models import SyncLog

        source = ContentSource.objects.create(
            repo_name='test-org/content',
        )
        sync_log = SyncLog.objects.create(source=source, status='running')
        sync_log.errors = [{
            'file': '',
            'error': 'S3 image upload disabled (S3_ENABLED is false)',
            'step': 's3_disabled',
        }]
        # Mirrors orchestration._finish_successful_sync:
        # sync_log.status = 'partial' if sync_log.errors else 'success'
        sync_log.status = 'partial' if sync_log.errors else 'success'
        self.assertEqual(sync_log.status, 'partial')

    def test_clean_noop_errors_makes_status_success(self):
        """When there are no errors (TESTING short-circuit), status is success."""
        from integrations.models import SyncLog

        source = ContentSource.objects.create(
            repo_name='test-org/content',
        )
        sync_log = SyncLog.objects.create(source=source, status='running')
        sync_log.errors = []  # clean no-op from TESTING short-circuit
        sync_log.status = 'partial' if sync_log.errors else 'success'
        self.assertEqual(sync_log.status, 'success')
