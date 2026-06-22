"""Tests for issue #797: validate cover_image references against known images.

Covers:
- Unit tests on ``rewrite_cover_image_url`` for the new
  ``known_images`` / ``errors`` kwargs (missing-from-set, present-in-set,
  legacy None default, absolute URL not validated).
- Unit tests on ``upload_images_to_s3`` to verify the new ``step`` key
  on per-file (``s3_upload``) and listing-pass (``s3_list``) errors.
- Integration tests on ``sync_content_source`` showing that a workshop
  yaml with a missing ``cover_image`` produces a ``partial`` SyncLog
  entry, that the happy path stays ``success``, and that a missing
  cover on one workshop does not block sibling workshops.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import ClientError
from django.test import TestCase, override_settings

from content.models import Workshop
from integrations.config import clear_config_cache
from integrations.models import ContentSource
from integrations.services.github_sync.media import (
    rewrite_cover_image_url,
    upload_images_to_s3,
)
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class RewriteCoverImageUrlValidationTest(TestCase):
    """Unit tests for the new ``known_images`` / ``errors`` kwargs."""

    @classmethod
    def setUpTestData(cls):
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/workshops-content',
            webhook_secret='secret',
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_relative_missing_from_known_images_returns_empty_and_records_error(self):
        """A relative cover_image not in known_images -> '' + error entry."""
        rel_path = (
            '2026-05-19-home-assignment-investment-coach-bot/workshop.yaml'
        )
        errors = []
        result = rewrite_cover_image_url(
            'images/cover.jpg', self.source, rel_path,
            known_images=set(), errors=errors,
        )

        self.assertEqual(result, '')
        self.assertEqual(len(errors), 1)
        entry = errors[0]
        self.assertEqual(entry['file'], rel_path)
        self.assertEqual(entry['step'], 'cover_image_missing')
        self.assertIn('images/cover.jpg', entry['error'])

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_relative_present_in_known_images_returns_cdn_url(self):
        """A relative cover_image in known_images -> CDN URL, no error."""
        rel_path = (
            '2026-05-19-home-assignment-investment-coach-bot/workshop.yaml'
        )
        known_images = {
            '2026-05-19-home-assignment-investment-coach-bot/images/cover.jpg',
        }
        errors = []
        result = rewrite_cover_image_url(
            'images/cover.jpg', self.source, rel_path,
            known_images=known_images, errors=errors,
        )

        self.assertEqual(
            result,
            (
                'https://cdn.example.com/workshops-content/'
                '2026-05-19-home-assignment-investment-coach-bot/images/cover.jpg'
            ),
        )
        self.assertEqual(errors, [])

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_legacy_call_without_known_images_returns_cdn_url(self):
        """Backward compatibility: known_images=None skips validation."""
        rel_path = (
            '2026-05-19-home-assignment-investment-coach-bot/workshop.yaml'
        )
        # No file actually exists at this path on disk — that's fine,
        # the helper does not consult the filesystem when known_images
        # is None. This preserves every legacy caller's behaviour.
        result = rewrite_cover_image_url(
            'images/cover.jpg', self.source, rel_path,
        )

        self.assertEqual(
            result,
            (
                'https://cdn.example.com/workshops-content/'
                '2026-05-19-home-assignment-investment-coach-bot/images/cover.jpg'
            ),
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_absolute_url_not_validated_against_known_images(self):
        """Absolute http(s):// URLs bypass the known_images check entirely."""
        rel_path = (
            '2026-05-19-home-assignment-investment-coach-bot/workshop.yaml'
        )
        errors = []
        result = rewrite_cover_image_url(
            'https://example.com/cover.jpg', self.source, rel_path,
            known_images=set(), errors=errors,
        )

        self.assertEqual(result, 'https://example.com/cover.jpg')
        self.assertEqual(errors, [])


class UploadImagesToS3StepTaggingTest(TestCase):
    """Unit tests for the new ``step`` tags on upload_images_to_s3 errors."""

    def setUp(self):
        clear_config_cache()
        self.source = ContentSource.objects.create(
            repo_name='test-org/content',
        )
        self.temp_dir = tempfile.mkdtemp()
        # Drop a small fake image so the upload pass has something to do.
        self.img_path = os.path.join(self.temp_dir, 'hero.png')
        with open(self.img_path, 'wb') as f:
            f.write(b'\x89PNG fake image data for step tagging test')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings(
        TESTING=False,
        S3_ENABLED=True,
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_per_file_upload_error_carries_step_s3_upload(self, mock_boto_client):
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{'Contents': []}]
        mock_s3.get_paginator.return_value = mock_paginator
        mock_s3.upload_file.side_effect = S3UploadFailedError('Access Denied')

        result = upload_images_to_s3(self.temp_dir, self.source)

        self.assertEqual(result['uploaded'], 0)
        self.assertEqual(len(result['errors']), 1)
        entry = result['errors'][0]
        self.assertEqual(entry['step'], 's3_upload')
        self.assertEqual(entry['file'], 'hero.png')
        self.assertIn('Access Denied', entry['error'])

    @override_settings(
        TESTING=False,
        S3_ENABLED=True,
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_listing_error_carries_step_s3_list_and_upload_continues(
        self, mock_boto_client,
    ):
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'listing denied'}},
            'ListObjectsV2',
        )
        mock_s3.get_paginator.return_value = mock_paginator

        result = upload_images_to_s3(self.temp_dir, self.source)

        # Listing failure does NOT abort the upload pass — the per-file
        # upload still runs with an empty existing_etags index.
        self.assertEqual(result['uploaded'], 1)
        # Exactly one listing-step entry.
        list_errors = [
            e for e in result['errors'] if e.get('step') == 's3_list'
        ]
        self.assertEqual(len(list_errors), 1)
        self.assertEqual(list_errors[0]['file'], '')
        mock_s3.upload_file.assert_called_once()


class WorkshopSyncCoverImageValidationTest(TestCase):
    """Integration: sync downgrades to ``partial`` when cover image is missing."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/workshops-content',
            is_private=False,
            prefix='cover-validation-',
        )

    def _write_workshop(self, folder, *, slug, cover_image='images/cover.jpg',
                        with_cover_file=True):
        """Write a minimal workshop.yaml + one page; optionally drop a cover."""
        import uuid

        self.repo.write_yaml(
            f'{folder}/workshop.yaml',
            {
                'content_id': str(uuid.uuid4()),
                'slug': slug,
                'title': f'Workshop {slug}',
                'date': '2026-05-19',
                'pages_required_level': 0,
                'cover_image': cover_image,
            },
        )
        # Tiny content page so the workshop has something to publish.
        self.repo.write_markdown(
            f'{folder}/01-intro.md',
            {'title': 'Intro'},
            'Body.\n',
            ensure_content_id=False,
        )
        if with_cover_file:
            # 1x1 PNG header — enough for the image walker to count it.
            self.repo.write_bytes(
                f'{folder}/images/cover.jpg',
                b'\x89PNG\r\n\x1a\nfake image bytes',
            )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_missing_cover_image_downgrades_status_to_partial(self):
        folder = '2026-05-19-home-assignment-investment-coach-bot'
        # cover_image references images/cover.jpg but no such file exists.
        self._write_workshop(
            folder,
            slug='investment-coach-bot',
            with_cover_file=False,
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.status, 'partial')
        cover_errors = [
            e for e in (sync_log.errors or [])
            if e.get('step') == 'cover_image_missing'
        ]
        self.assertEqual(len(cover_errors), 1)
        self.assertEqual(
            cover_errors[0]['file'], f'{folder}/workshop.yaml',
        )
        self.assertIn('images/cover.jpg', cover_errors[0]['error'])

        workshop = Workshop.objects.get(slug='investment-coach-bot')
        self.assertEqual(workshop.cover_image_url, '')

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_aggregate_batch_reports_cover_error_count(self):
        """The Studio dashboard's batch aggregator counts the cover error."""
        from studio.views.sync import _aggregate_batch

        folder = '2026-05-19-home-assignment-investment-coach-bot'
        self._write_workshop(
            folder, slug='investment-coach-bot', with_cover_file=False,
        )
        sync_log = sync_repo(self.source, self.repo)

        agg = _aggregate_batch([sync_log])
        self.assertGreaterEqual(agg['errors_count'], 1)

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_happy_path_stays_success_when_cover_file_present(self):
        folder = '2026-05-19-home-assignment-investment-coach-bot'
        self._write_workshop(
            folder, slug='investment-coach-bot', with_cover_file=True,
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(
            sync_log.status, 'success',
            f'Expected success, got {sync_log.status}: {sync_log.errors}',
        )
        self.assertEqual(sync_log.errors, [])
        workshop = Workshop.objects.get(slug='investment-coach-bot')
        self.assertEqual(
            workshop.cover_image_url,
            (
                'https://cdn.example.com/workshops-content/'
                f'{folder}/images/cover.jpg'
            ),
        )

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com')
    def test_missing_cover_on_one_workshop_does_not_block_siblings(self):
        good_folder = '2026-05-19-good-workshop'
        bad_folder = '2026-05-19-bad-workshop'
        self._write_workshop(
            good_folder, slug='good-workshop', with_cover_file=True,
        )
        self._write_workshop(
            bad_folder, slug='bad-workshop', with_cover_file=False,
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(sync_log.status, 'partial')
        cover_errors = [
            e for e in (sync_log.errors or [])
            if e.get('step') == 'cover_image_missing'
        ]
        self.assertEqual(len(cover_errors), 1)
        self.assertEqual(
            cover_errors[0]['file'], f'{bad_folder}/workshop.yaml',
        )

        good = Workshop.objects.get(slug='good-workshop')
        bad = Workshop.objects.get(slug='bad-workshop')
        self.assertEqual(
            good.cover_image_url,
            (
                'https://cdn.example.com/workshops-content/'
                f'{good_folder}/images/cover.jpg'
            ),
        )
        self.assertEqual(bad.cover_image_url, '')
