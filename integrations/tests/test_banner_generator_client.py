"""Tests for the banner-generator client (issue #788).

Covers ``is_enabled``, ``render_to_s3``, and the token-safety invariant
in :mod:`integrations.services.banner_generator`. All HTTP calls are
mocked at ``integrations.services.banner_generator.requests.post``.
"""

import os
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.banner_generator import (
    BannerGeneratorError,
    is_enabled,
    render_to_s3,
)

PATCH_TARGET = 'integrations.services.banner_generator.requests.post'


class _BannerGeneratorCacheCleanupMixin:
    """Clear the in-process config cache before and after each test.

    The settings cache is module-level and survives Django's per-test
    DB rollback. Without this teardown a setting written by one test
    leaks into the next when tests run in parallel and a worker happens
    to reuse the process for an unrelated test class.
    """

    def setUp(self):
        super().setUp()
        env_patch = patch.dict(os.environ, {
            'BANNER_GENERATOR_FUNCTION_URL': '',
            'BANNER_GENERATOR_AUTH_TOKEN': '',
            'AWS_S3_CONTENT_BUCKET': '',
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        clear_config_cache()
        self.addCleanup(clear_config_cache)


def _set_setting(key, value):
    IntegrationSetting.objects.update_or_create(
        key=key,
        defaults={'value': value, 'is_secret': False, 'group': 'banner_generator', 'description': ''},
    )


def _configure_banner_generator(token='sekrit-token-xyz', bucket='content-bucket'):
    """Seed IntegrationSettings so ``is_enabled()`` returns True."""
    _set_setting('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/render')
    _set_setting('BANNER_GENERATOR_AUTH_TOKEN', token)
    _set_setting('AWS_S3_CONTENT_BUCKET', bucket)
    clear_config_cache()


class IsEnabledTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """Drives is_enabled() via IntegrationSetting upserts to exercise get_config."""

    def test_returns_true_when_both_url_and_token_set(self):
        _configure_banner_generator()
        self.assertTrue(is_enabled())

    def test_returns_false_when_url_missing(self):
        _set_setting('BANNER_GENERATOR_AUTH_TOKEN', 'sekrit-token-xyz')
        clear_config_cache()
        self.assertFalse(is_enabled())

    def test_returns_false_when_token_missing(self):
        _set_setting('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/render')
        clear_config_cache()
        self.assertFalse(is_enabled())

    def test_returns_false_when_both_missing(self):
        self.assertFalse(is_enabled())


class RenderToS3RequestShapeTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """Asserts the JSON body, headers, and timeout passed to requests.post."""

    def setUp(self):
        super().setUp()
        _configure_banner_generator()

    @patch(PATCH_TARGET)
    def test_request_payload_includes_all_fields(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'ok': True, 's3': {'bucket': 'content-bucket', 'key': 'banners/article/42.jpg'}},
        )
        render_to_s3(
            template='asl-content-card', size='og', fmt='jpeg',
            data={
                'kind': 'Article', 'kicker': 'Guides',
                'title': 'Hello', 'subtitle': 'Sub',
                'meta_primary': 'Blog', 'meta_secondary': 'a / b',
                'footer': 'aishippinglabs.com/blog',
            },
            s3_key='banners/article/42.jpg',
        )
        mock_post.assert_called_once()
        kwargs = mock_post.call_args.kwargs
        # JSON body
        body = kwargs['json']
        self.assertEqual(body['template'], 'asl-content-card')
        self.assertEqual(body['format'], 'jpeg')
        self.assertEqual(body['size'], 'og')
        self.assertEqual(body['data']['kind'], 'Article')
        self.assertEqual(body['data']['title'], 'Hello')
        self.assertEqual(body['s3']['bucket'], 'content-bucket')
        self.assertEqual(body['s3']['key'], 'banners/article/42.jpg')
        self.assertEqual(body['s3']['content_type'], 'image/jpeg')
        # Headers
        self.assertEqual(
            kwargs['headers']['Authorization'],
            'Bearer sekrit-token-xyz',
        )
        self.assertEqual(kwargs['headers']['Content-Type'], 'application/json')
        # Timeout resolves from get_config; default is 90s (issue #900).
        self.assertEqual(kwargs['timeout'], 90)


class RenderToS3TimeoutConfigTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """The render timeout resolves from BANNER_GENERATOR_TIMEOUT_SECONDS."""

    def setUp(self):
        super().setUp()
        _configure_banner_generator()

    def _render(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {'ok': True},
        )
        render_to_s3(
            template='asl-content-card', size='og', fmt='jpeg',
            data={'title': 'x'}, s3_key='banners/article/1.jpg',
        )
        return mock_post.call_args.kwargs['timeout']

    @patch(PATCH_TARGET)
    def test_default_timeout_is_90(self, mock_post):
        self.assertEqual(self._render(mock_post), 90)

    @patch(PATCH_TARGET)
    def test_db_override_changes_timeout(self, mock_post):
        _set_setting('BANNER_GENERATOR_TIMEOUT_SECONDS', '120')
        clear_config_cache()
        self.assertEqual(self._render(mock_post), 120)

    @patch(PATCH_TARGET)
    def test_non_integer_override_falls_back_to_default(self, mock_post):
        _set_setting('BANNER_GENERATOR_TIMEOUT_SECONDS', 'not-a-number')
        clear_config_cache()
        self.assertEqual(self._render(mock_post), 90)

    @patch(PATCH_TARGET)
    def test_non_positive_override_falls_back_to_default(self, mock_post):
        _set_setting('BANNER_GENERATOR_TIMEOUT_SECONDS', '0')
        clear_config_cache()
        self.assertEqual(self._render(mock_post), 90)

    @patch(PATCH_TARGET)
    def test_explicit_timeout_arg_overrides_config(self, mock_post):
        _set_setting('BANNER_GENERATOR_TIMEOUT_SECONDS', '120')
        clear_config_cache()
        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {'ok': True},
        )
        render_to_s3(
            template='asl-content-card', size='og', fmt='jpeg',
            data={'title': 'x'}, s3_key='banners/article/1.jpg',
            timeout=5,
        )
        self.assertEqual(mock_post.call_args.kwargs['timeout'], 5)


class RenderToS3ErrorTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """Asserts BannerGeneratorError on every failure mode + no token leak."""

    TOKEN = 'super-secret-bearer-token-do-not-leak'

    def setUp(self):
        super().setUp()
        _configure_banner_generator(token=self.TOKEN)

    def _make_payload_kwargs(self):
        return {
            'template': 'asl-content-card', 'size': 'og', 'fmt': 'jpeg',
            'data': {'title': 'x'}, 's3_key': 'banners/article/1.jpg',
        }

    @patch(PATCH_TARGET)
    def test_raises_on_http_500(self, mock_post):
        mock_post.return_value = MagicMock(status_code=500, json=lambda: {})
        with self.assertRaises(BannerGeneratorError) as cm:
            render_to_s3(**self._make_payload_kwargs())
        self.assertNotIn(self.TOKEN, str(cm.exception))
        self.assertNotIn(self.TOKEN, repr(cm.exception.args))
        self.assertEqual(cm.exception.status_code, 500)

    @patch(PATCH_TARGET)
    def test_raises_on_request_exception(self, mock_post):
        mock_post.side_effect = requests.ConnectionError('boom')
        with self.assertRaises(BannerGeneratorError) as cm:
            render_to_s3(**self._make_payload_kwargs())
        self.assertNotIn(self.TOKEN, str(cm.exception))
        self.assertNotIn(self.TOKEN, repr(cm.exception.args))

    @patch(PATCH_TARGET)
    def test_raises_when_ok_false_in_response(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'ok': False, 'error': 'template missing'},
        )
        with self.assertRaises(BannerGeneratorError) as cm:
            render_to_s3(**self._make_payload_kwargs())
        self.assertIn('template missing', str(cm.exception))
        self.assertNotIn(self.TOKEN, str(cm.exception))

    @patch(PATCH_TARGET)
    def test_raises_on_non_json_response(self, mock_post):
        response = MagicMock(status_code=200)
        response.json.side_effect = ValueError('not json')
        mock_post.return_value = response
        with self.assertRaises(BannerGeneratorError) as cm:
            render_to_s3(**self._make_payload_kwargs())
        self.assertNotIn(self.TOKEN, str(cm.exception))

    def test_raises_when_not_configured(self):
        IntegrationSetting.objects.filter(
            key='BANNER_GENERATOR_FUNCTION_URL',
        ).delete()
        clear_config_cache()
        with self.assertRaises(BannerGeneratorError) as cm:
            render_to_s3(**self._make_payload_kwargs())
        self.assertNotIn(self.TOKEN, str(cm.exception))


class TokenSafetyInLogsTest(_BannerGeneratorCacheCleanupMixin, TestCase):
    """Failure modes must not log the bearer token at any level."""

    TOKEN = 'sekrit-do-not-leak'

    def setUp(self):
        super().setUp()
        _configure_banner_generator(token=self.TOKEN)

    @patch(PATCH_TARGET)
    def test_render_failure_logs_no_token(self, mock_post):
        from integrations.services.banner_generator.tasks import (
            render_banner_for_content,
        )
        mock_post.return_value = MagicMock(status_code=500, json=lambda: {})
        # Create a minimal article so the task hits the Lambda path.
        from datetime import date

        from content.models import Article
        article = Article.objects.create(
            title='Hello', slug='hello', date=date(2026, 1, 1),
        )
        with self.assertLogs(
            'integrations.services.banner_generator.tasks',
            level='WARNING',
        ) as log_cm:
            render_banner_for_content('article', article.pk)
        joined = '\n'.join(log_cm.output)
        self.assertNotIn(self.TOKEN, joined)
