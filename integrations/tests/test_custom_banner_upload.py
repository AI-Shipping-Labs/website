"""Unit tests for the custom-banner upload service (issue #931).

Covers validation (type, size, empty), the S3 upload happy path (boto3
mocked), the unique-key shape, the precedence resolver, and the narrow
safe-delete (only ``custom-banners/<type>/`` keys under the CDN base are
ever deleted).
"""

from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.banner_generator.custom_upload import (
    CustomBannerUploadError,
    is_upload_enabled,
    safe_delete_custom_banner,
    upload_custom_banner,
)
from integrations.services.banner_generator.resolve import (
    banner_source,
    effective_banner_url,
)

CDN_BASE = 'https://cdn.example.com'
BUCKET = 'content-bucket'
S3_CLIENT_PATH = (
    'integrations.services.banner_generator.custom_upload._s3_client'
)


def _configure(cdn=CDN_BASE, bucket=BUCKET):
    """Set / clear the CDN + bucket DB overrides.

    The base settings are forced empty at the class level via
    @override_settings, so DB rows (which win over settings) are the only
    source of a configured value here. Passing ``None`` clears the row so
    get_config falls through to the empty setting -> "unconfigured".
    """
    for key, value in (
        ('CONTENT_CDN_BASE', cdn),
        ('AWS_S3_CONTENT_BUCKET', bucket),
        ('AWS_S3_CONTENT_REGION', 'eu-west-1'),
    ):
        if value is None:
            IntegrationSetting.objects.filter(key=key).delete()
            continue
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                'value': value, 'is_secret': False,
                'group': 'banner_generator', 'description': '',
            },
        )
    clear_config_cache()


def _png(name='banner.png', size=1024):
    return SimpleUploadedFile(name, b'x' * size, content_type='image/png')


# Force the base settings empty so DB rows (which win over settings in
# get_config) are the only source of a configured CDN/bucket in these tests.
empty_base_settings = override_settings(
    CONTENT_CDN_BASE='', AWS_S3_CONTENT_BUCKET='',
)


class _ConfigCleanupMixin:
    def setUp(self):
        super().setUp()
        clear_config_cache()
        self.addCleanup(clear_config_cache)


@empty_base_settings
class IsUploadEnabledTest(_ConfigCleanupMixin, TestCase):
    def test_enabled_when_cdn_and_bucket_set(self):
        _configure()
        self.assertTrue(is_upload_enabled())

    def test_disabled_when_cdn_missing(self):
        _configure(cdn=None)
        self.assertFalse(is_upload_enabled())

    def test_disabled_when_bucket_missing(self):
        _configure(bucket=None)
        self.assertFalse(is_upload_enabled())


@empty_base_settings
class UploadValidationTest(_ConfigCleanupMixin, TestCase):
    def setUp(self):
        super().setUp()
        _configure()

    @patch(S3_CLIENT_PATH)
    def test_rejects_non_image(self, mock_client):
        bad = SimpleUploadedFile(
            'doc.pdf', b'%PDF-1.4', content_type='application/pdf',
        )
        with self.assertRaises(CustomBannerUploadError) as ctx:
            upload_custom_banner('article', 1, bad)
        self.assertIn('Unsupported file type', ctx.exception.message)
        mock_client.assert_not_called()

    @patch(S3_CLIENT_PATH)
    def test_rejects_oversized(self, mock_client):
        big = SimpleUploadedFile(
            'big.png', b'x' * (6 * 1024 * 1024), content_type='image/png',
        )
        with self.assertRaises(CustomBannerUploadError) as ctx:
            upload_custom_banner('article', 1, big)
        self.assertIn('too large', ctx.exception.message)
        self.assertIn('5 MB', ctx.exception.message)
        mock_client.assert_not_called()

    @patch(S3_CLIENT_PATH)
    def test_rejects_empty(self, mock_client):
        empty = SimpleUploadedFile('empty.png', b'', content_type='image/png')
        with self.assertRaises(CustomBannerUploadError):
            upload_custom_banner('article', 1, empty)
        mock_client.assert_not_called()

    def test_rejects_when_not_configured(self):
        _configure(cdn=None)
        with self.assertRaises(CustomBannerUploadError) as ctx:
            upload_custom_banner('article', 1, _png())
        self.assertIn('not configured', ctx.exception.message)


@empty_base_settings
class UploadHappyPathTest(_ConfigCleanupMixin, TestCase):
    def setUp(self):
        super().setUp()
        _configure()

    @patch(S3_CLIENT_PATH)
    def test_uploads_and_returns_cdn_url(self, mock_client):
        s3 = MagicMock()
        mock_client.return_value = s3

        url = upload_custom_banner('workshop', 42, _png())

        # The CDN URL is under the custom-banners/<type>/ prefix and the
        # configured CDN base, with the .png extension preserved.
        self.assertTrue(
            url.startswith(f'{CDN_BASE}/custom-banners/workshop/42-'),
            url,
        )
        self.assertTrue(url.endswith('.png'), url)

        # The S3 object was uploaded to the configured bucket with the
        # image content type and a public cache header.
        s3.upload_fileobj.assert_called_once()
        call_args = s3.upload_fileobj.call_args.args
        self.assertEqual(call_args[1], BUCKET)
        self.assertTrue(call_args[2].startswith('custom-banners/workshop/42-'))
        extra = s3.upload_fileobj.call_args.kwargs['ExtraArgs']
        self.assertEqual(extra['ContentType'], 'image/png')
        self.assertIn('max-age', extra['CacheControl'])

    @patch(S3_CLIENT_PATH)
    def test_jpeg_extension_mapped(self, mock_client):
        mock_client.return_value = MagicMock()
        jpg = SimpleUploadedFile(
            'b.jpg', b'x' * 512, content_type='image/jpeg',
        )
        url = upload_custom_banner('article', 5, jpg)
        self.assertTrue(url.endswith('.jpg'), url)


@empty_base_settings
class SafeDeleteTest(_ConfigCleanupMixin, TestCase):
    def setUp(self):
        super().setUp()
        _configure()

    @patch(S3_CLIENT_PATH)
    def test_deletes_own_custom_banner_key(self, mock_client):
        s3 = MagicMock()
        mock_client.return_value = s3
        url = f'{CDN_BASE}/custom-banners/article/1-abc.png'
        self.assertTrue(safe_delete_custom_banner('article', url))
        s3.delete_object.assert_called_once_with(
            Bucket=BUCKET, Key='custom-banners/article/1-abc.png',
        )

    @patch(S3_CLIENT_PATH)
    def test_refuses_to_delete_frontmatter_cover(self, mock_client):
        # A non custom-banners/ URL must never be deleted.
        url = f'{CDN_BASE}/content-repo/images/cover.png'
        self.assertFalse(safe_delete_custom_banner('article', url))
        mock_client.assert_not_called()

    @patch(S3_CLIENT_PATH)
    def test_refuses_to_delete_generated_banner(self, mock_client):
        # ``banners/`` is the generated-banner prefix, not custom uploads.
        url = f'{CDN_BASE}/banners/article/1-x.jpg'
        self.assertFalse(safe_delete_custom_banner('article', url))
        mock_client.assert_not_called()

    @patch(S3_CLIENT_PATH)
    def test_refuses_to_delete_url_off_cdn_base(self, mock_client):
        url = 'https://evil.example.com/custom-banners/article/1-x.png'
        self.assertFalse(safe_delete_custom_banner('article', url))
        mock_client.assert_not_called()

    @patch(S3_CLIENT_PATH)
    def test_refuses_cross_type_prefix(self, mock_client):
        # A course URL must not be deletable under the 'article' content type.
        url = f'{CDN_BASE}/custom-banners/course/1-x.png'
        self.assertFalse(safe_delete_custom_banner('article', url))
        mock_client.assert_not_called()


class ResolveHelperTest(TestCase):
    """Pure precedence resolver — no DB, no config."""

    class _Rec:
        def __init__(self, cover='', custom='', auto=''):
            self.cover_image_url = cover
            self.custom_banner_url = custom
            self.auto_banner_url = auto

    def test_cover_wins(self):
        rec = self._Rec(cover='c', custom='u', auto='a')
        self.assertEqual(effective_banner_url(rec), 'c')
        self.assertEqual(banner_source(rec), 'Frontmatter cover')

    def test_custom_beats_auto(self):
        rec = self._Rec(cover='', custom='u', auto='a')
        self.assertEqual(effective_banner_url(rec), 'u')
        self.assertEqual(banner_source(rec), 'Custom upload')

    def test_auto_is_last(self):
        rec = self._Rec(cover='', custom='', auto='a')
        self.assertEqual(effective_banner_url(rec), 'a')
        self.assertEqual(banner_source(rec), 'Generated')

    def test_empty_when_none(self):
        rec = self._Rec()
        self.assertEqual(effective_banner_url(rec), '')
        self.assertEqual(banner_source(rec), '')

    def test_missing_cover_attr_is_safe(self):
        # EventSeries has no cover_image_url; getattr default keeps it safe.
        class _SeriesLike:
            custom_banner_url = 'u'
            auto_banner_url = 'a'

        self.assertEqual(effective_banner_url(_SeriesLike()), 'u')
        self.assertEqual(banner_source(_SeriesLike()), 'Custom upload')
