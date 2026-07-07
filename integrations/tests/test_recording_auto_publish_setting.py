"""Tests for RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD (issue #1134, Phase B).

The recording should be watchable right away, so the auto-publish gate
defaults ON. Unlike ``is_enabled`` (which hardcodes a 'false' fallback), the
helper resolves via ``get_config('RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD', 'true')``
so the DB -> settings -> env -> default chain lands on True when unset, and an
operator can flip it off from Studio to keep the review-first flow.
"""

import os
from unittest import mock

from django.test import TestCase, override_settings

from integrations.config import (
    clear_config_cache,
    recording_auto_publish_on_s3_upload_enabled,
)
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name


def _auto_publish_def():
    group = get_group_by_name('s3_recordings')
    if not group:
        return None
    for key_def in group['keys']:
        if key_def['key'] == 'RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD':
            return key_def
    return None


class RecordingAutoPublishRegistryTest(TestCase):
    def test_registered_in_s3_recordings_group_as_boolean_default_true(self):
        key_def = _auto_publish_def()
        self.assertIsNotNone(
            key_def,
            'RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD must be registered in '
            'the s3_recordings group',
        )
        self.assertTrue(key_def.get('is_boolean'))
        self.assertEqual(key_def.get('default'), 'true')
        self.assertFalse(key_def.get('is_secret'))

    def test_has_description_and_docs_url(self):
        key_def = _auto_publish_def()
        self.assertIsNotNone(key_def)
        self.assertTrue(key_def.get('description'))
        self.assertEqual(
            key_def.get('docs_url'),
            '_docs/integrations/s3_recordings.md#recording_auto_publish_on_s3_upload',
        )


class RecordingAutoPublishResolutionTest(TestCase):
    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()
        IntegrationSetting.objects.filter(
            key='RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD',
        ).delete()

    def test_defaults_to_enabled_when_unset_everywhere(self):
        self.assertTrue(recording_auto_publish_on_s3_upload_enabled())

    @override_settings(RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD=False)
    def test_resolves_false_from_django_settings(self):
        clear_config_cache()
        self.assertFalse(recording_auto_publish_on_s3_upload_enabled())

    def test_explicit_false_db_override_disables(self):
        IntegrationSetting.objects.update_or_create(
            key='RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD',
            defaults={
                'value': 'false',
                'is_secret': False,
                'group': 's3_recordings',
            },
        )
        clear_config_cache()
        self.assertFalse(recording_auto_publish_on_s3_upload_enabled())

    def test_true_db_override_enables(self):
        IntegrationSetting.objects.update_or_create(
            key='RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD',
            defaults={
                'value': 'true',
                'is_secret': False,
                'group': 's3_recordings',
            },
        )
        clear_config_cache()
        self.assertTrue(recording_auto_publish_on_s3_upload_enabled())

    def test_explicit_false_env_var_disables(self):
        with mock.patch.dict(
            os.environ,
            {'RECORDING_AUTO_PUBLISH_ON_S3_UPLOAD': 'false'},
        ):
            clear_config_cache()
            self.assertFalse(recording_auto_publish_on_s3_upload_enabled())
