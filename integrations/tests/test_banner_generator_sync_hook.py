"""Integration test: sync_content_source enqueues banner-generator (issue #788).

Uses :class:`integrations.tests.sync_fixtures.SyncTestRepo` to drive a
real ``sync_content_source`` call against a temp content repo and asserts
on the ``async_task`` call count via a mock on the dispatch module.
"""

import os
from unittest.mock import patch

from django.test import TestCase

from content.models import Article
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.tests.sync_fixtures import (
    SyncTestRepo,
    make_content_source,
    sync_repo,
)

DISPATCH_PATCH = (
    'integrations.services.banner_generator.dispatch.async_task'
)


class _BannerGeneratorCacheCleanupMixin:
    """Clear the in-process config cache before and after each test."""

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


def _configure_banner_generator():
    for key, value in (
        ('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/'),
        ('BANNER_GENERATOR_AUTH_TOKEN', 'token-abc'),
        ('AWS_S3_CONTENT_BUCKET', 'content-bucket'),
        ('CONTENT_CDN_BASE', 'https://cdn.example.com'),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={'value': value, 'is_secret': False, 'group': 'banner_generator', 'description': ''},
        )
    clear_config_cache()


class ArticleSyncEnqueuesBannerTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        _configure_banner_generator()
        self.source = make_content_source('test-org/content')

    def _write_article(self, repo, **frontmatter_overrides):
        fm = {
            'title': 'Hello World',
            'slug': 'hello-world',
            'date': '2026-01-01',
            'tags': ['guides'],
            **frontmatter_overrides,
        }
        repo.write_markdown(
            'articles/hello.md',
            frontmatter=fm,
            body='Sample body.',
        )

    @patch(DISPATCH_PATCH)
    def test_new_article_without_cover_enqueues_render(self, mock_async):
        repo = SyncTestRepo(self)
        self._write_article(repo)
        sync_repo(self.source, repo)
        article = Article.objects.get(slug='hello-world')
        # At least one call for the article we created.
        article_calls = [
            call for call in mock_async.call_args_list
            if len(call.args) >= 3 and call.args[1] == 'article' and call.args[2] == article.pk
        ]
        self.assertEqual(len(article_calls), 1)

    @patch(DISPATCH_PATCH)
    def test_unchanged_resync_does_not_re_enqueue(self, mock_async):
        repo = SyncTestRepo(self)
        self._write_article(repo)
        sync_repo(self.source, repo)
        mock_async.reset_mock()
        # Second sync with no changes — nothing to enqueue.
        sync_repo(self.source, repo)
        article = Article.objects.get(slug='hello-world')
        article_calls = [
            call for call in mock_async.call_args_list
            if len(call.args) >= 3 and call.args[1] == 'article' and call.args[2] == article.pk
        ]
        self.assertEqual(len(article_calls), 0)

    @patch(DISPATCH_PATCH)
    def test_cover_image_set_in_frontmatter_does_not_enqueue(self, mock_async):
        repo = SyncTestRepo(self)
        # Use an absolute URL so the cover-image rewriter stores it as-is.
        self._write_article(
            repo, cover_image='https://cdn.example.com/foo.png',
        )
        sync_repo(self.source, repo)
        article = Article.objects.get(slug='hello-world')
        article_calls = [
            call for call in mock_async.call_args_list
            if len(call.args) >= 3 and call.args[1] == 'article' and call.args[2] == article.pk
        ]
        self.assertEqual(len(article_calls), 0)

    @patch(DISPATCH_PATCH)
    def test_no_op_when_banner_generator_not_configured(self, mock_async):
        IntegrationSetting.objects.filter(
            key__startswith='BANNER_GENERATOR_',
        ).delete()
        clear_config_cache()
        repo = SyncTestRepo(self)
        self._write_article(repo)
        sync_repo(self.source, repo)
        article = Article.objects.get(slug='hello-world')
        article_calls = [
            call for call in mock_async.call_args_list
            if len(call.args) >= 3 and call.args[1] == 'article' and call.args[2] == article.pk
        ]
        self.assertEqual(len(article_calls), 0)


class CustomBannerSurvivesResyncTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):
    """Issue #931: a Studio custom upload is never clobbered by a re-sync.

    The sync dispatchers write ``cover_image_url`` (from frontmatter) and
    never write ``custom_banner_url``. This is the critical guarantee that
    makes the custom upload a sync-safe override — assert it through a real
    ``sync_content_source`` run rather than by reading the dispatcher code.
    """

    def setUp(self):
        super().setUp()
        _configure_banner_generator()
        self.source = make_content_source('test-org/content')

    def _write_article(self, repo, **frontmatter_overrides):
        fm = {
            'title': 'Sync Safe',
            'slug': 'sync-safe',
            'date': '2026-01-01',
            'tags': ['guides'],
            **frontmatter_overrides,
        }
        repo.write_markdown(
            'articles/sync-safe.md', frontmatter=fm, body='Body.',
        )

    @patch(DISPATCH_PATCH)
    def test_custom_banner_url_unchanged_after_resync(self, _mock_async):
        custom = 'https://cdn.example.com/custom-banners/article/9-abc.png'
        repo = SyncTestRepo(self)
        # Frontmatter has NO cover_image, so cover_image_url stays empty.
        self._write_article(repo)
        sync_repo(self.source, repo)

        article = Article.objects.get(slug='sync-safe')
        # Simulate an operator's custom upload landing on the record.
        Article.objects.filter(pk=article.pk).update(custom_banner_url=custom)

        # Re-sync the same (unchanged) content.
        sync_repo(self.source, repo)

        article.refresh_from_db()
        self.assertEqual(article.custom_banner_url, custom)
        # And it remains empty for cover_image (the sync-owned field) so the
        # custom upload is still the effective banner.
        self.assertEqual(article.cover_image_url, '')

    @patch(DISPATCH_PATCH)
    def test_custom_banner_survives_even_when_frontmatter_changes(
        self, _mock_async,
    ):
        custom = 'https://cdn.example.com/custom-banners/article/9-def.png'
        repo = SyncTestRepo(self)
        self._write_article(repo)
        sync_repo(self.source, repo)
        article = Article.objects.get(slug='sync-safe')
        Article.objects.filter(pk=article.pk).update(custom_banner_url=custom)

        # Change the title (forces a real re-save of the article row).
        self._write_article(repo, title='Sync Safe Renamed')
        sync_repo(self.source, repo)

        article.refresh_from_db()
        self.assertEqual(article.title, 'Sync Safe Renamed')
        self.assertEqual(article.custom_banner_url, custom)
