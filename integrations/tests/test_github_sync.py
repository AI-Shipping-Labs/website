"""Tests for GitHub Content Sync - issue #92.

Covers:
- ContentSource and SyncLog models
- Source tracking fields on content models
- GitHub webhook endpoint (signature validation, repo identification, sync triggering)
- Content sync logic (articles, courses, resources, projects)
- Soft-delete behavior for stale content
- Image URL rewriting
- GitHub App authentication
- Admin sync pages (dashboard, history, trigger, sync all)
- Seed content sources management command
- Direct admin edits flagged with source_repo = null
"""

import hashlib
import hmac
import json
import os
import tempfile
from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings, tag
from django.utils import timezone

from content.models import (
    Article,
    Course,
    CuratedLink,
    Download,
    Module,
    Project,
    Unit,
    Workshop,
)
from events.models import Event
from integrations.models import ContentSource, SyncLog
from integrations.services.content_sync_queue import ContentSyncQueueResult
from integrations.services.github import (
    GitHubSyncError,
    find_content_source,
    rewrite_image_urls,
    sync_content_source,
    validate_webhook_signature,
)
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo

User = get_user_model()

TEST_WEBHOOK_SECRET = 'test-github-webhook-secret'


def make_github_signature(body, secret=TEST_WEBHOOK_SECRET):
    """Create a valid GitHub webhook X-Hub-Signature-256 for testing."""
    if isinstance(body, str):
        body = body.encode('utf-8')
    sig = hmac.new(
        secret.encode('utf-8'),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f'sha256={sig}'


# ===========================================================================
# ContentSource Model Tests
# ===========================================================================


@tag('core')
class ContentSourceModelTest(TestCase):
    """Test ContentSource model fields and behavior."""

    def test_create_content_source(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            webhook_secret='secret123',
            is_private=False,
        )
        self.assertEqual(source.repo_name, 'AI-Shipping-Labs/blog')
        self.assertFalse(source.is_private)
        self.assertIsNone(source.last_synced_at)
        self.assertIsNone(source.last_sync_status)
        self.assertIsNone(source.last_sync_log)
        self.assertIsNotNone(source.id)
        self.assertIsNotNone(source.created_at)

    def test_content_source_short_name(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )
        self.assertEqual(source.short_name, 'blog')

    def test_repo_name_unique(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )
        with self.assertRaises(IntegrityError):
            ContentSource.objects.create(
                repo_name='AI-Shipping-Labs/blog',
            )

    def test_is_private_default_false(self):
        source = ContentSource.objects.create(
            repo_name='test/repo',
        )
        self.assertFalse(source.is_private)

    def test_private_source(self):
        source = ContentSource.objects.create(
            repo_name='test/private-repo',
            is_private=True,
        )
        self.assertTrue(source.is_private)


# ===========================================================================
# SyncLog Model Tests
# ===========================================================================


class SyncLogModelTest(TestCase):
    """Test SyncLog model fields and behavior."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def test_create_sync_log(self):
        log = SyncLog.objects.create(
            source=self.source,
            status='running',
        )
        self.assertEqual(log.source, self.source)
        self.assertEqual(log.status, 'running')
        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, 0)
        self.assertEqual(log.items_deleted, 0)
        self.assertEqual(log.errors, [])
        self.assertIsNone(log.finished_at)

    def test_sync_log_total_items(self):
        log = SyncLog.objects.create(
            source=self.source,
            items_created=5,
            items_updated=3,
            items_deleted=1,
        )
        self.assertEqual(log.total_items, 9)

    def test_sync_log_duration(self):
        log = SyncLog.objects.create(
            source=self.source,
            status='success',
        )
        # No finished_at yet
        self.assertIsNone(log.duration_seconds)

        # Set finished_at
        log.finished_at = log.started_at + timezone.timedelta(seconds=42)
        log.save()
        self.assertAlmostEqual(log.duration_seconds, 42.0, places=1)

    def test_sync_log_cascade_delete(self):
        SyncLog.objects.create(source=self.source, status='success')
        self.assertEqual(SyncLog.objects.count(), 1)
        self.source.delete()
        self.assertEqual(SyncLog.objects.count(), 0)

    def test_sync_log_ordering(self):
        SyncLog.objects.create(source=self.source, status='success')
        log2 = SyncLog.objects.create(source=self.source, status='failed')
        logs = list(SyncLog.objects.all())
        # Most recent first
        self.assertEqual(logs[0].pk, log2.pk)


# ===========================================================================
# Source Tracking Fields Tests
# ===========================================================================


class SourceTrackingFieldsTest(TestCase):
    """Test that all content models have source_repo, source_path, source_commit."""

    def test_article_source_fields(self):
        article = Article.objects.create(
            title='Test', slug='test', date=date.today(),
            source_repo='AI-Shipping-Labs/blog',
            source_path='test-article.md',
            source_commit='abc123',
        )
        self.assertEqual(article.source_repo, 'AI-Shipping-Labs/blog')
        self.assertEqual(article.source_path, 'test-article.md')
        self.assertEqual(article.source_commit, 'abc123')

    def test_article_source_fields_nullable(self):
        article = Article.objects.create(
            title='Test', slug='test-null', date=date.today(),
        )
        self.assertIsNone(article.source_repo)
        self.assertIsNone(article.source_path)
        self.assertIsNone(article.source_commit)

    def test_recording_source_fields(self):
        recording = Event.objects.create(
            title='Test', slug='test-rec', start_datetime=timezone.now(),
            source_repo='AI-Shipping-Labs/resources',
            source_path='recordings/test.yaml',
            source_commit='def456',
        )
        self.assertEqual(recording.source_repo, 'AI-Shipping-Labs/resources')

    def test_project_source_fields(self):
        project = Project.objects.create(
            title='Test', slug='test-proj', date=date.today(),
            source_repo='AI-Shipping-Labs/projects',
        )
        self.assertEqual(project.source_repo, 'AI-Shipping-Labs/projects')

    def test_curated_link_source_fields(self):
        link = CuratedLink.objects.create(
            item_id='test-link', title='Test', url='https://example.com',
            category='tools',
            source_repo='AI-Shipping-Labs/resources',
        )
        self.assertEqual(link.source_repo, 'AI-Shipping-Labs/resources')

    def test_download_source_fields(self):
        dl = Download.objects.create(
            title='Test', slug='test-dl',
            file_url='https://example.com/file.pdf',
            source_repo='AI-Shipping-Labs/resources',
        )
        self.assertEqual(dl.source_repo, 'AI-Shipping-Labs/resources')

    def test_course_source_fields(self):
        course = Course.objects.create(
            title='Test', slug='test-course',
            source_repo='AI-Shipping-Labs/courses',
        )
        self.assertEqual(course.source_repo, 'AI-Shipping-Labs/courses')

    def test_module_source_fields(self):
        course = Course.objects.create(title='Test', slug='test-c')
        module = Module.objects.create(
            course=course, title='Module 1', slug='module-1',
            source_repo='AI-Shipping-Labs/courses',
            source_path='test-course/module-01',
        )
        self.assertEqual(module.source_repo, 'AI-Shipping-Labs/courses')

    def test_unit_source_fields(self):
        course = Course.objects.create(title='Test', slug='test-cu')
        module = Module.objects.create(course=course, title='M1', slug='m1')
        unit = Unit.objects.create(
            module=module, title='Unit 1', slug='unit-1',
            source_repo='AI-Shipping-Labs/courses',
            source_path='test-course/module-01/unit-01.md',
        )
        self.assertEqual(unit.source_repo, 'AI-Shipping-Labs/courses')


# ===========================================================================
# Webhook Signature Validation Tests
# ===========================================================================


@tag('core')
class GitHubWebhookSignatureTest(TestCase):
    """Test GitHub webhook X-Hub-Signature-256 validation."""

    def test_valid_signature(self):
        body = b'{"action":"push"}'
        sig = make_github_signature(body)
        request = MagicMock()
        request.headers = {'X-Hub-Signature-256': sig}
        request.body = body
        self.assertTrue(validate_webhook_signature(request, TEST_WEBHOOK_SECRET))

    def test_invalid_signature(self):
        request = MagicMock()
        request.headers = {'X-Hub-Signature-256': 'sha256=invalidsig'}
        request.body = b'{"action":"push"}'
        self.assertFalse(validate_webhook_signature(request, TEST_WEBHOOK_SECRET))

    def test_missing_signature_header(self):
        request = MagicMock()
        request.headers = {}
        request.body = b'{}'
        self.assertFalse(validate_webhook_signature(request, TEST_WEBHOOK_SECRET))

    def test_empty_secret(self):
        request = MagicMock()
        request.headers = {'X-Hub-Signature-256': 'sha256=abc'}
        request.body = b'{}'
        self.assertFalse(validate_webhook_signature(request, ''))

    def test_tampered_body(self):
        body = b'{"action":"push"}'
        sig = make_github_signature(body)
        request = MagicMock()
        request.headers = {'X-Hub-Signature-256': sig}
        request.body = b'{"action":"tampered"}'
        self.assertFalse(validate_webhook_signature(request, TEST_WEBHOOK_SECRET))


# ===========================================================================
# Find Content Source Tests
# ===========================================================================


class FindContentSourceTest(TestCase):
    """Test finding content sources by repo name."""

    def test_find_existing_source(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        found = find_content_source('AI-Shipping-Labs/content')
        self.assertEqual(found.pk, source.pk)

    def test_find_returns_single_source_per_repo(self):
        # Issue #310: one ContentSource per repo (unique repo_name).
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )
        found = find_content_source('AI-Shipping-Labs/content')
        self.assertIsNotNone(found)
        self.assertEqual(found.pk, source.pk)

    def test_find_nonexistent_source(self):
        found = find_content_source('nonexistent/repo')
        self.assertIsNone(found)


# ===========================================================================
# Image URL Rewriting Tests
# ===========================================================================


@tag('core')
class ImageURLRewriteTest(TestCase):
    """Test markdown image URL rewriting."""

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com/content-images')
    def test_rewrite_relative_image(self):
        md = '![diagram](images/architecture.png)'
        result = rewrite_image_urls(md, 'AI-Shipping-Labs/blog', '')
        self.assertIn('https://cdn.example.com/content-images/blog/', result)
        self.assertIn('architecture.png', result)

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com/content-images')
    def test_preserve_absolute_url(self):
        md = '![photo](https://example.com/photo.png)'
        result = rewrite_image_urls(md, 'AI-Shipping-Labs/blog', '')
        self.assertEqual(result, md)

    @override_settings(CONTENT_CDN_BASE='https://cdn.example.com/content-images')
    def test_rewrite_with_base_path(self):
        md = '![img](screenshot.png)'
        result = rewrite_image_urls(md, 'AI-Shipping-Labs/blog', 'articles')
        self.assertIn('articles/screenshot.png', result)

    def test_no_images(self):
        md = 'Just some text without images.'
        result = rewrite_image_urls(md, 'test/repo', '')
        self.assertEqual(result, md)


# ===========================================================================
# GitHub Webhook Endpoint Tests
# ===========================================================================


@tag('core')
class GitHubWebhookEndpointTest(TestCase):
    """Test POST /api/webhooks/github endpoint."""

    def setUp(self):
        self.client = Client()
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            webhook_secret=TEST_WEBHOOK_SECRET,
        )

    def _post_webhook(self, payload_dict, event_type='push'):
        body = json.dumps(payload_dict)
        sig = make_github_signature(body.encode('utf-8'))
        return self.client.post(
            '/api/webhooks/github',
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=sig,
            HTTP_X_GITHUB_EVENT=event_type,
        )

    def test_valid_push_webhook_returns_200(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'AI-Shipping-Labs/blog'},
        }
        with patch('integrations.views.github_webhook.sync_content_source'):
            response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'ok')

    def test_invalid_signature_returns_400(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'AI-Shipping-Labs/blog'},
        }
        response = self.client.post(
            '/api/webhooks/github',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256='sha256=invalidsig',
            HTTP_X_GITHUB_EVENT='push',
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data['error'], 'Invalid webhook signature')

    def test_unknown_repo_returns_404(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'unknown-org/unknown-repo'},
        }
        response = self.client.post(
            '/api/webhooks/github',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_GITHUB_EVENT='push',
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            '/api/webhooks/github',
            data='not json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_repo_info_returns_400(self):
        body = json.dumps({'ref': 'refs/heads/main'})
        sig = make_github_signature(body.encode('utf-8'))
        response = self.client.post(
            '/api/webhooks/github',
            data=body,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=sig,
        )
        self.assertEqual(response.status_code, 400)

    def test_non_main_branch_push_not_synced(self):
        payload = {
            'ref': 'refs/heads/feature-branch',
            'repository': {'full_name': 'AI-Shipping-Labs/blog'},
        }
        with patch('integrations.views.github_webhook.sync_content_source') as mock_sync:
            response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)
        mock_sync.assert_not_called()

    def test_get_not_allowed(self):
        response = self.client.get('/api/webhooks/github')
        self.assertEqual(response.status_code, 405)

    def test_csrf_exempt(self):
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'AI-Shipping-Labs/blog'},
        }
        with patch('integrations.views.github_webhook.sync_content_source'):
            response = self._post_webhook(payload)
        self.assertEqual(response.status_code, 200)

    def test_webhook_logged(self):
        from integrations.models import WebhookLog
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'AI-Shipping-Labs/blog'},
        }
        with patch('integrations.views.github_webhook.sync_content_source'):
            self._post_webhook(payload)
        log = WebhookLog.objects.filter(service='github').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.event_type, 'push')

    def test_source_without_webhook_secret_skips_validation(self):
        """Sources without webhook_secret accept any request."""
        self.source.webhook_secret = ''
        self.source.save()
        payload = {
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'AI-Shipping-Labs/blog'},
        }
        with patch('integrations.views.github_webhook.sync_content_source'):
            response = self.client.post(
                '/api/webhooks/github',
                data=json.dumps(payload),
                content_type='application/json',
                HTTP_X_GITHUB_EVENT='push',
            )
        self.assertEqual(response.status_code, 200)


# ===========================================================================
# Content Sync Tests (Articles)
# ===========================================================================


@tag('core')
class SyncArticlesTest(TestCase):
    """Test syncing articles from a mock repo directory."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/blog',
            prefix='article-sync-',
        )
        self.temp_dir = str(self.repo.path)

    def _write_article(self, filename, frontmatter_dict, body):
        return self.repo.write_markdown(filename, frontmatter_dict, body)

    def test_sync_creates_article(self):
        self._write_article(
            'my-article.md',
            {
                'title': 'My Article',
                'slug': 'my-article',
                'description': 'A test article',
                'date': '2026-01-15',
                'author': 'Test Author',
            },
            'Article body content here.',
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 1)
        article = Article.objects.get(slug='my-article')
        self.assertEqual(article.title, 'My Article')
        self.assertEqual(article.source_repo, 'AI-Shipping-Labs/blog')
        self.assertEqual(article.source_path, 'my-article.md')
        self.assertTrue(article.published)

    def test_sync_updates_existing_article(self):
        Article.objects.create(
            title='Old Title', slug='my-article', date=date.today(),
            source_repo='AI-Shipping-Labs/blog',
        )
        self._write_article(
            'my-article.md',
            {'title': 'New Title', 'slug': 'my-article', 'date': '2026-01-15'},
            'Updated body.',
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertEqual(sync_log.items_updated, 1)
        article = Article.objects.get(slug='my-article')
        self.assertEqual(article.title, 'New Title')

    def test_sync_soft_deletes_stale_articles(self):
        Article.objects.create(
            title='Stale', slug='stale-article', date=date.today(),
            source_repo='AI-Shipping-Labs/blog',
            published=True,
        )
        # No files in repo = stale article should be soft-deleted
        sync_log = sync_repo(self.source, self.repo)
        self.assertEqual(sync_log.items_deleted, 1)
        article = Article.objects.get(slug='stale-article')
        self.assertFalse(article.published)
        self.assertEqual(article.status, 'draft')

    def test_article_round_trip_remove_then_restore(self):
        """write -> sync -> remove -> sync -> re-write -> sync.

        Asserts the model flag (``published``) AND the public ``/blog``
        listing visibility flip at each step. A model-flag-only test
        would miss a regression where the queryset on the listing view
        forgot to filter on ``published=True``.
        """
        stable_content_id = '99999999-9999-9999-9999-999999999999'
        unique_title = 'Round Trip Article ZZQQ-RT-1'

        def write_file():
            self._write_article(
                'roundtrip.md',
                {
                    'title': unique_title,
                    'slug': 'roundtrip-article',
                    'description': 'Round trip test',
                    'date': '2026-01-15',
                    'page_type': 'blog',
                    'content_id': stable_content_id,
                },
                'Round trip body content.',
            )

        def remove_file():
            os.remove(os.path.join(self.temp_dir, 'roundtrip.md'))

        # Step 1: write yaml -> sync -> assert published
        write_file()
        sync_repo(self.source, self.repo)
        article = Article.objects.get(slug='roundtrip-article')
        self.assertTrue(article.published)
        self.assertEqual(article.status, 'published')
        # Public listing must include the title.
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unique_title)

        # Step 2: remove yaml -> sync -> assert soft-deleted
        remove_file()
        sync_repo(self.source, self.repo)
        article.refresh_from_db()
        self.assertFalse(article.published)
        self.assertEqual(article.status, 'draft')
        # Public listing must hide the title now.
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, unique_title)

        # Step 3: re-add yaml -> sync -> assert restored
        write_file()
        sync_repo(self.source, self.repo)
        article.refresh_from_db()
        self.assertTrue(article.published)
        self.assertEqual(article.status, 'published')
        # Listing must include it again.
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unique_title)

    def test_sync_does_not_soft_delete_direct_admin_edits(self):
        """Articles with source_repo = null (direct admin edits) are not touched."""
        Article.objects.create(
            title='Admin Edit', slug='admin-article', date=date.today(),
            source_repo=None,
            published=True,
        )
        sync_repo(self.source, self.repo)
        article = Article.objects.get(slug='admin-article')
        self.assertTrue(article.published)

    def test_sync_skips_readme(self):
        """README.md files should be skipped."""
        self._write_article(
            'README.MD',
            {'title': 'Readme', 'slug': 'readme'},
            'This is the readme.',
        )
        sync_log = sync_repo(self.source, self.repo)
        self.assertEqual(sync_log.items_created, 0)
        self.assertFalse(Article.objects.filter(slug='readme').exists())

    def test_sync_multiple_articles(self):
        for i in range(3):
            self._write_article(
                f'article-{i}.md',
                {'title': f'Article {i}', 'slug': f'article-{i}', 'date': '2026-01-15'},
                f'Body of article {i}.',
            )
        sync_log = sync_repo(self.source, self.repo)
        self.assertEqual(sync_log.items_created, 3)
        self.assertEqual(Article.objects.filter(source_repo='AI-Shipping-Labs/blog').count(), 3)

    def test_sync_log_created(self):
        sync_log = sync_repo(self.source, self.repo)
        self.assertIsNotNone(sync_log)
        self.assertEqual(sync_log.source, self.source)
        self.assertIsNotNone(sync_log.finished_at)

    def test_sync_updates_source_status(self):
        sync_repo(self.source, self.repo)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_sync_status, 'success')
        self.assertIsNotNone(self.source.last_synced_at)
        self.assertIsNotNone(self.source.last_sync_log)

    def test_sync_with_errors_partial_status(self):
        """If some files have errors, status should be 'partial'."""
        # Write a valid article
        self._write_article(
            'good-article.md',
            {'title': 'Good', 'slug': 'good', 'date': '2026-01-15'},
            'Good content.',
        )
        # Write a file that will cause a parsing error (binary content)
        self.repo.write_bytes('bad-article.md', b'\x00\x01\x02---\ntitle: bad\n---\n\x80\x81')
        sync_log = sync_repo(self.source, self.repo)
        # The good article should still be created
        self.assertTrue(Article.objects.filter(slug='good').exists())
        # If errors occurred, status should be partial
        if sync_log.errors:
            self.assertEqual(sync_log.status, 'partial')

    def test_sync_does_not_overwrite_studio_content(self):
        """Repo sync does not overwrite Studio-created content (source_repo=NULL)."""
        Article.objects.create(
            title='Admin Version', slug='same-slug', date=date.today(),
            source_repo=None,  # direct admin/Studio edit
            published=True,
        )
        self._write_article(
            'same-slug.md',
            {'title': 'Repo Version', 'slug': 'same-slug', 'date': '2026-01-15'},
            'From repo.',
        )
        sync_log = sync_repo(self.source, self.repo)
        # Studio article should be untouched
        article = Article.objects.get(slug='same-slug')
        self.assertEqual(article.title, 'Admin Version')
        self.assertIsNone(article.source_repo)
        # Error should be logged for slug collision
        self.assertTrue(
            any('Slug collision' in str(e.get('error', '')) for e in sync_log.errors),
        )


# ===========================================================================
# Content Sync Tests (Projects)
# ===========================================================================


@tag('core')
class SyncProjectsTest(TestCase):
    """Test syncing projects from a mock repo directory."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/projects',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sync_creates_project(self):
        filepath = os.path.join(self.temp_dir, 'my-project.md')
        with open(filepath, 'w') as f:
            f.write('---\n')
            f.write('title: "My Project"\n')
            f.write('slug: "my-project"\n')
            f.write('description: "A test project"\n')
            f.write('difficulty: "beginner"\n')
            f.write('date: "2026-01-15"\n')
            f.write('content_id: "11111111-1111-1111-1111-111111111111"\n')
            f.write('---\n')
            f.write('Project content here.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)
        project = Project.objects.get(slug='my-project')
        self.assertEqual(project.title, 'My Project')
        self.assertEqual(project.source_repo, 'AI-Shipping-Labs/projects')

    def test_sync_soft_deletes_stale_projects(self):
        Project.objects.create(
            title='Stale', slug='stale-project', date=date.today(),
            source_repo='AI-Shipping-Labs/projects',
            published=True,
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_deleted, 1)
        project = Project.objects.get(slug='stale-project')
        self.assertFalse(project.published)


# ===========================================================================
# Content Sync Tests (Courses)
# ===========================================================================


@tag('core')
class SyncCoursesTest(TestCase):
    """Test syncing courses from a mock repo directory."""

    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/courses',
            prefix='course-sync-',
        )
        self.temp_dir = str(self.repo.path)

    def _create_course_structure(self):
        """Create a minimal course directory structure."""
        self.repo.write_yaml(
            'python-data-ai/course.yaml',
            {
                'title': 'Python for Data AI',
                'slug': 'python-data-ai',
                'description': 'Learn Python',
                'instructor_name': 'Test Instructor',
                'required_level': 0,
                'content_id': '22222222-2222-2222-2222-222222222222',
                'tags': ['python', 'data'],
            },
        )
        self.repo.write_yaml(
            'python-data-ai/module-01-setup/module.yaml',
            {'title': 'Getting Started', 'sort_order': 1},
        )
        self.repo.write_markdown(
            'python-data-ai/module-01-setup/unit-01-intro.md',
            {
                'title': 'Introduction',
                'sort_order': 1,
                'is_preview': True,
                'content_id': '33333333-3333-3333-3333-333333333333',
            },
            'Welcome to the course!\n',
        )
        return self.repo.path / 'python-data-ai'

    def test_sync_creates_course_with_modules_and_units(self):
        self._create_course_structure()
        sync_log = sync_repo(self.source, self.repo)

        self.assertIn(sync_log.status, ('success', 'partial'))
        # 1 course + 1 module + 1 unit = 3 created
        self.assertEqual(sync_log.items_created, 3)

        course = Course.objects.get(slug='python-data-ai')
        self.assertEqual(course.title, 'Python for Data AI')
        self.assertEqual(course.source_repo, 'AI-Shipping-Labs/courses')

        module = Module.objects.get(course=course, title='Getting Started')
        self.assertEqual(module.sort_order, 1)
        self.assertEqual(module.source_repo, 'AI-Shipping-Labs/courses')

        unit = Unit.objects.get(module=module, title='Introduction')
        self.assertEqual(unit.sort_order, 1)
        self.assertTrue(unit.is_preview)
        self.assertEqual(unit.source_repo, 'AI-Shipping-Labs/courses')

    def test_sync_soft_deletes_stale_course(self):
        Course.objects.create(
            title='Stale Course', slug='stale-course',
            source_repo='AI-Shipping-Labs/courses',
            status='published',
        )
        sync_repo(self.source, self.repo)
        course = Course.objects.get(slug='stale-course')
        self.assertEqual(course.status, 'draft')

    def test_course_round_trip_remove_then_restore(self):
        """write -> sync -> remove -> sync -> re-write -> sync for courses.

        Asserts ``Course.status`` and ``/courses`` listing visibility
        flip at each step.
        """
        import shutil as _shutil
        unique_title = 'Round Trip Course ZZQQ-RT-2'

        # Step 1: write course folder -> sync -> assert published
        course_dir = self._create_course_structure()
        # Override the title so the listing assertion is unambiguous
        # (the helper uses 'Python for Data AI', which is generic).
        self.repo.write_yaml(
            'python-data-ai/course.yaml',
            {
                'title': unique_title,
                'slug': 'python-data-ai',
                'description': 'Round trip course test',
                'instructor_name': 'Test Instructor',
                'required_level': 0,
                'content_id': '22222222-2222-2222-2222-222222222222',
            },
        )

        sync_repo(self.source, self.repo)
        course = Course.objects.get(slug='python-data-ai')
        self.assertEqual(course.status, 'published')
        # Public catalog must include the course title.
        response = self.client.get('/courses')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unique_title)

        # Step 2: remove the entire course folder -> sync -> assert draft
        _shutil.rmtree(course_dir)
        sync_repo(self.source, self.repo)
        course.refresh_from_db()
        self.assertEqual(course.status, 'draft')
        # Catalog must hide the soft-deleted course.
        response = self.client.get('/courses')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, unique_title)

        # Step 3: re-create the same folder (same slug + content_id) ->
        # sync -> assert restored to published.
        self._create_course_structure()
        self.repo.write_yaml(
            'python-data-ai/course.yaml',
            {
                'title': unique_title,
                'slug': 'python-data-ai',
                'description': 'Round trip course test',
                'instructor_name': 'Test Instructor',
                'required_level': 0,
                'content_id': '22222222-2222-2222-2222-222222222222',
            },
        )

        sync_repo(self.source, self.repo)
        course.refresh_from_db()
        self.assertEqual(course.status, 'published')
        response = self.client.get('/courses')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unique_title)

    def test_sync_ignores_legacy_is_free_key_in_yaml(self):
        """A leftover `is_free` key in course.yaml must not break sync.

        The field was removed in favor of deriving from `required_level`,
        but older content YAML may still contain the key. The parser must
        silently ignore it.
        """
        self.repo.write_yaml(
            'legacy-course/course.yaml',
            {
                'title': 'Legacy Course',
                'slug': 'legacy-course',
                'description': 'Has leftover is_free key.',
                'instructor_name': 'Test',
                'required_level': 0,
                # Deprecated key that must be silently ignored by sync.
                'is_free': True,
                'content_id': '44444444-4444-4444-4444-444444444444',
            },
        )
        self.repo.write_yaml(
            'legacy-course/module-01/module.yaml',
            {'title': 'Intro', 'sort_order': 1},
        )
        self.repo.write_markdown(
            'legacy-course/module-01/unit-01.md',
            {
                'title': 'Unit 1',
                'sort_order': 1,
                'content_id': '55555555-5555-5555-5555-555555555555',
            },
            'Body.\n',
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertIn(sync_log.status, ('success', 'partial'))
        course = Course.objects.get(slug='legacy-course')
        self.assertEqual(course.required_level, 0)
        # The property derives from required_level, not from the YAML key.
        self.assertTrue(course.is_free)


class SyncCoursePerLevelDetailTest(TestCase):
    """Issue #224 - course syncs record per-level items_detail entries.

    Previously the sync only appended one entry per course to
    ``items_detail``; modules and units were rolled into the bare counts.
    The dashboard couldn't tell which lessons were touched. The sync now
    appends one ``items_detail`` entry per course, per module, and per unit
    so the dashboard can render the per-level breakdown and an expandable
    list of changed pages.
    """

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _build_course_with_modules_and_units(
        self, n_modules=3, units_per_module=4,
    ):
        """Create a single-course repo with N modules x M units each.

        Returns the (n_modules, units_per_module) tuple so the test can
        derive expected counts.
        """
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Big Course"\n')
            f.write('slug: "big-course"\n')
            f.write('description: "Has many modules."\n')
            f.write('instructor_name: "Test"\n')
            f.write('required_level: 0\n')
            f.write(
                'content_id: "11111111-1111-1111-1111-111111111111"\n'
            )

        unit_counter = 0
        for m in range(1, n_modules + 1):
            mdir = os.path.join(self.temp_dir, f'module-{m:02d}')
            os.makedirs(mdir, exist_ok=True)
            with open(os.path.join(mdir, 'module.yaml'), 'w') as f:
                f.write(f'title: "Module {m}"\n')
                f.write(f'sort_order: {m}\n')
            for u in range(1, units_per_module + 1):
                unit_counter += 1
                # Stable content_id keyed by counter so re-syncing yields
                # the same UUID and we can verify update vs create.
                content_id = f'22222222-2222-2222-2222-{unit_counter:012d}'
                upath = os.path.join(mdir, f'unit-{u:02d}.md')
                with open(upath, 'w') as f:
                    f.write('---\n')
                    f.write(f'title: "Unit {m}.{u}"\n')
                    f.write(f'sort_order: {u}\n')
                    f.write(f'content_id: "{content_id}"\n')
                    f.write('---\n')
                    f.write(f'Body for unit {m}.{u}.\n')
        return n_modules, units_per_module

    def test_sync_records_per_level_entries_in_items_detail(self):
        """1 course + 3 modules + 12 units -> matching items_detail counts."""
        n_modules, units_per_module = (
            self._build_course_with_modules_and_units(
                n_modules=3, units_per_module=4,
            )
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertIn(sync_log.status, ('success', 'partial'))

        by_type = {}
        for item in sync_log.items_detail:
            by_type.setdefault(item['content_type'], []).append(item)

        self.assertEqual(len(by_type.get('course', [])), 1)
        self.assertEqual(len(by_type.get('module', [])), n_modules)
        self.assertEqual(
            len(by_type.get('unit', [])),
            n_modules * units_per_module,
        )

    def test_sync_lists_every_changed_unit_title(self):
        """All 12 unit titles appear in items_detail (acceptance criterion)."""
        self._build_course_with_modules_and_units(
            n_modules=3, units_per_module=4,
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        unit_titles = sorted(
            item['title']
            for item in sync_log.items_detail
            if item['content_type'] == 'unit'
        )
        expected = sorted(
            f'Unit {m}.{u}'
            for m in range(1, 4)
            for u in range(1, 5)
        )
        self.assertEqual(unit_titles, expected)

    def test_unit_items_include_studio_edit_ids(self):
        """Each unit item includes course_id, module_id, unit_id for URL building."""
        self._build_course_with_modules_and_units(
            n_modules=1, units_per_module=2,
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        unit_items = [
            i for i in sync_log.items_detail if i['content_type'] == 'unit'
        ]
        self.assertEqual(len(unit_items), 2)
        for item in unit_items:
            self.assertIn('course_id', item)
            self.assertIn('module_id', item)
            self.assertIn('unit_id', item)
            self.assertIsNotNone(item['unit_id'])

    def test_module_items_include_course_id(self):
        """Each module item includes course_id so it can link to the course edit page."""
        self._build_course_with_modules_and_units(
            n_modules=2, units_per_module=1,
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        module_items = [
            i for i in sync_log.items_detail if i['content_type'] == 'module'
        ]
        self.assertEqual(len(module_items), 2)
        for item in module_items:
            self.assertIn('course_id', item)
            self.assertIn('module_id', item)
            self.assertIsNotNone(item['module_id'])

    def test_resync_with_no_changes_produces_no_items_detail(self):
        """Issue #225: re-syncing identical content does not mark anything
        as 'updated' or include items in items_detail.

        The previous behavior wrote every row on every sync, inflating the
        'updated' counter and the per-item detail with rows that hadn't
        actually changed. The dashboard treats items_detail as an audit log
        of what changed, so spurious entries make it useless.
        """
        self._build_course_with_modules_and_units(
            n_modules=2, units_per_module=2,
        )
        sync_log_1 = sync_content_source(self.source, repo_dir=self.temp_dir)
        # First sync should create everything.
        self.assertEqual(sync_log_1.items_created, 1 + 2 + 4)  # course+modules+units

        sync_log_2 = sync_content_source(
            self.source, repo_dir=self.temp_dir,
        )

        self.assertEqual(sync_log_2.items_created, 0)
        self.assertEqual(sync_log_2.items_updated, 0)
        self.assertEqual(sync_log_2.items_unchanged, 1 + 2 + 4)
        self.assertEqual(sync_log_2.items_detail, [])


# ===========================================================================
# Content Sync Tests (Single-Course Repo)
# ===========================================================================


class SyncSingleCourseRepoTest(TestCase):
    """Test syncing a single-course repo where course.yaml lives at root.

    This covers issue #197 - support for repos that contain exactly one
    course at the root (e.g. AI-Shipping-Labs/python-course), as opposed
    to the existing multi-course layout used by the content monorepo's
    courses/ subtree.
    """

    def setUp(self):
        # ContentSource with content_path='' - root of repo is the course root.
        self.source, self.repo = make_sync_repo(
            self,
            repo_name='AI-Shipping-Labs/python-course',
            prefix='single-course-sync-',
        )
        self.temp_dir = str(self.repo.path)

    def _write_root_course_yaml(self, content_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                                slug='python-course'):
        self.repo.write_yaml(
            'course.yaml',
            {
                'title': 'Python Course',
                'slug': slug,
                'description': 'Learn Python from scratch.',
                'instructor_name': 'Alexey Grigorev',
                'required_level': 20,
                'content_id': content_id,
                'tags': ['python', 'fundamentals'],
            },
        )

    def _write_module(self, dirname, title, content_id, sort_order=None):
        module_data = {'title': title, 'content_id': content_id}
        if sort_order is not None:
            module_data['sort_order'] = sort_order
        self.repo.write_yaml(f'{dirname}/module.yaml', module_data)
        module_dir = self.repo.path / dirname
        return module_dir

    def _write_unit(self, module_dir, filename, title, content_id, body='Body text.\n'):
        rel_module = module_dir.relative_to(self.repo.path)
        self.repo.write_markdown(
            rel_module / filename,
            {'title': title, 'content_id': content_id},
            body,
        )

    def test_root_course_yaml_creates_single_course(self):
        """A course.yaml at the root is treated as one course; modules are children."""
        self._write_root_course_yaml()
        module_dir = self._write_module(
            '01-intro', 'Introduction',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            module_dir, '01-why-python.md', 'Why Python',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertIn(sync_log.status, ('success', 'partial'),
                      msg=f'Errors: {sync_log.errors}')
        # 1 course + 1 module + 1 unit = 3 created
        self.assertEqual(sync_log.items_created, 3)
        self.assertEqual(Course.objects.filter(
            source_repo='AI-Shipping-Labs/python-course',
        ).count(), 1)

        course = Course.objects.get(slug='python-course')
        self.assertEqual(course.title, 'Python Course')
        # Single-course mode: source_path is '.' (course root == repo content root)
        self.assertEqual(course.source_path, '.')
        self.assertEqual(course.required_level, 20)

        module = Module.objects.get(course=course)
        self.assertEqual(module.title, 'Introduction')
        # Module source_path is the module dir relative to content root.
        self.assertEqual(module.source_path, '01-intro')
        # sort_order derived from numeric "01-" prefix.
        self.assertEqual(module.sort_order, 1)

        unit = Unit.objects.get(module=module)
        self.assertEqual(unit.title, 'Why Python')

    def test_root_course_slug_change_updates_existing_course_by_content_id(self):
        """Re-syncing with the same content_id but a new slug updates the row.

        The course must remain the same logical object: no duplicate row,
        no stale draft of the old slug, and the renamed course stays
        published with its module/unit tree intact.
        """
        content_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        self._write_root_course_yaml(content_id=content_id, slug='python-course')
        module_dir = self._write_module(
            '01-intro', 'Introduction',
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
        )
        self._write_unit(
            module_dir, '01-why-python.md', 'Why Python',
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
        )

        first_log = sync_repo(self.source, self.repo)
        self.assertIn(first_log.status, ('success', 'partial'))
        original_course = Course.objects.get(slug='python-course')
        original_course_id = original_course.pk
        original_module_ids = list(
            Module.objects.filter(course=original_course).values_list('pk', flat=True)
        )
        original_unit_ids = list(
            Unit.objects.filter(module__course=original_course).values_list('pk', flat=True)
        )

        self.repo.write_yaml(
            'course.yaml',
            {
                'title': 'Python Course Workshop',
                'slug': 'python-course-workshop',
                'description': 'Learn Python from scratch.',
                'instructor_name': 'Alexey Grigorev',
                'required_level': 20,
                'content_id': content_id,
                'tags': ['python', 'fundamentals'],
            },
        )

        second_log = sync_repo(self.source, self.repo)

        self.assertIn(second_log.status, ('success', 'partial'))
        self.assertEqual(Course.objects.filter(
            source_repo='AI-Shipping-Labs/python-course',
        ).count(), 1)
        self.assertFalse(Course.objects.filter(slug='python-course').exists())

        course = Course.objects.get(slug='python-course-workshop')
        self.assertEqual(course.pk, original_course_id)
        self.assertEqual(course.title, 'Python Course Workshop')
        self.assertEqual(str(course.content_id), content_id)
        self.assertEqual(course.status, 'published')
        self.assertEqual(second_log.items_created, 0)
        self.assertEqual(second_log.items_updated, 1)
        self.assertEqual(
            list(Module.objects.filter(course=course).values_list('pk', flat=True)),
            original_module_ids,
        )
        self.assertEqual(
            list(Unit.objects.filter(module__course=course).values_list('pk', flat=True)),
            original_unit_ids,
        )

    def test_no_root_course_yaml_falls_back_to_multi_course_walk(self):
        """Without root course.yaml, each child dir with course.yaml is its own course (regression guard)."""
        # Two child course dirs, each with their own course.yaml + module + unit.
        for idx, slug in enumerate(['course-a', 'course-b'], start=1):
            self.repo.write_yaml(
                f'{slug}/course.yaml',
                {
                    'title': f'Course {slug}',
                    'slug': slug,
                    'content_id': f'1{idx:07d}-1111-1111-1111-111111111111',
                },
            )
            self.repo.write_yaml(f'{slug}/01-mod/module.yaml', {'title': 'Module 1'})
            self.repo.write_markdown(
                f'{slug}/01-mod/01-intro.md',
                {
                    'title': 'Intro',
                    'content_id': f'2{idx:07d}-2222-2222-2222-222222222222',
                },
                'Body.\n',
            )

        sync_log = sync_repo(self.source, self.repo)

        self.assertIn(sync_log.status, ('success', 'partial'),
                      msg=f'Errors: {sync_log.errors}')
        # 2 courses + 2 modules + 2 units = 6 created.
        self.assertEqual(sync_log.items_created, 6)
        slugs = set(Course.objects.filter(
            source_repo='AI-Shipping-Labs/python-course',
        ).values_list('slug', flat=True))
        self.assertEqual(slugs, {'course-a', 'course-b'})

    def test_root_course_yaml_wins_over_child_course_dirs(self):
        """If root course.yaml exists AND a child has course.yaml, only the root course is created."""
        self._write_root_course_yaml()
        # Child dir with its own course.yaml that should be IGNORED as a
        # standalone course; without a module.yaml, the child dir is just
        # skipped by _sync_course_modules.
        self.repo.write_yaml(
            'child-course/course.yaml',
            {
                'title': 'Child Course',
                'slug': 'child-course',
                'content_id': 'dddddddd-dddd-dddd-dddd-dddddddddddd',
            },
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertIn(sync_log.status, ('success', 'partial'),
                      msg=f'Errors: {sync_log.errors}')
        courses = list(Course.objects.filter(
            source_repo='AI-Shipping-Labs/python-course',
        ))
        self.assertEqual(len(courses), 1)
        self.assertEqual(courses[0].slug, 'python-course')
        # The child dir has no module.yaml so it produces no Module either.
        self.assertEqual(Module.objects.filter(course=courses[0]).count(), 0)

    def test_stale_cleanup_in_single_course_mode_demotes_other_courses(self):
        """When a root course.yaml is synced, other published courses for the same repo become drafts."""
        # Pre-existing course from same source_repo with a different slug.
        Course.objects.create(
            title='Old Python Course',
            slug='old-python-course',
            source_repo='AI-Shipping-Labs/python-course',
            status='published',
        )
        self._write_root_course_yaml()

        sync_log = sync_repo(self.source, self.repo)
        self.assertIn(sync_log.status, ('success', 'partial'),
                      msg=f'Errors: {sync_log.errors}')

        # New course present, old course demoted.
        new_course = Course.objects.get(slug='python-course')
        self.assertEqual(new_course.status, 'published')
        old_course = Course.objects.get(slug='old-python-course')
        self.assertEqual(old_course.status, 'draft')

    def test_root_course_yaml_missing_content_id_is_rejected(self):
        """Single-course mode still enforces content_id validation."""
        # Write a course.yaml WITHOUT content_id.
        self.repo.write_yaml(
            'course.yaml',
            {
                'title': 'Python Course',
                'slug': 'python-course',
            },
        )

        sync_log = sync_repo(self.source, self.repo)

        self.assertEqual(
            Course.objects.filter(slug='python-course').count(), 0,
        )
        # Sync should record an error mentioning content_id.
        self.assertTrue(any(
            'content_id' in err.get('error', '')
            for err in sync_log.errors
        ), msg=f'Expected content_id error in {sync_log.errors}')

    def test_empty_module_dir_with_only_module_yaml_does_not_error(self):
        """Modules with module.yaml but no unit .md files sync as empty modules without crashing."""
        self._write_root_course_yaml()
        # Module 1: has a unit.
        m1 = self._write_module(
            '01-intro', 'Intro',
            'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
        )
        self._write_unit(
            m1, '01-why-python.md', 'Why Python',
            'ffffffff-ffff-ffff-ffff-ffffffffffff',
        )
        # Module 2: just module.yaml, no units (scaffolding for future content).
        self._write_module(
            '06-data-processing', 'Data Processing',
            '11111111-2222-3333-4444-555555555555',
        )

        sync_log = sync_repo(self.source, self.repo)

        # No errors, both modules created.
        self.assertEqual(sync_log.errors, [])
        course = Course.objects.get(slug='python-course')
        modules = Module.objects.filter(course=course).order_by('sort_order')
        self.assertEqual([m.title for m in modules], ['Intro', 'Data Processing'])
        self.assertEqual([m.sort_order for m in modules], [1, 6])
        # The empty module has zero units.
        empty_module = modules.get(sort_order=6)
        self.assertEqual(Unit.objects.filter(module=empty_module).count(), 0)


# ===========================================================================
# Content Sync Tests (Resources)
# ===========================================================================


@tag('core')
class SyncResourcesTest(TestCase):
    """Test syncing resources (recordings, curated links, downloads)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/resources',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sync_recordings(self):
        # Recordings are now synced as events. With one ContentSource
        # per repo (issue #310), the existing self.source is reused.
        event_source = self.source
        rec_dir = os.path.join(self.temp_dir, 'recordings')
        os.makedirs(rec_dir)
        with open(os.path.join(rec_dir, 'my-workshop.yaml'), 'w') as f:
            f.write('title: "My Workshop"\n')
            f.write('slug: "my-workshop"\n')
            f.write('description: "A great workshop"\n')
            f.write('video_url: "https://youtube.com/watch?v=abc"\n')
            f.write('published_at: "2026-01-15"\n')
            f.write('content_id: "44444444-4444-4444-4444-444444444444"\n')
            f.write('tags:\n  - workshop\n')

        sync_log = sync_content_source(event_source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)
        recording = Event.objects.get(slug='my-workshop')
        self.assertEqual(recording.title, 'My Workshop')
        self.assertEqual(recording.source_repo, 'AI-Shipping-Labs/resources')

    def test_sync_curated_links_from_markdown_files(self):
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'awesome-tool.md'), 'w') as f:
            f.write('---\n')
            f.write('content_id: link-1\n')
            f.write('title: "Awesome Tool"\n')
            f.write('url: "https://example.com"\n')
            f.write('category: tools\n')
            f.write('tags: [ai, tools]\n')
            f.write('date: 2026-03-15\n')
            f.write('required_level: 0\n')
            f.write('sort_order: 0\n')
            f.write('---\n\n')
            f.write('A great tool for AI development.\n')
        with open(os.path.join(links_dir, 'cool-model.md'), 'w') as f:
            f.write('---\n')
            f.write('content_id: link-2\n')
            f.write('title: "Cool Model"\n')
            f.write('url: "https://example.com/model"\n')
            f.write('category: models\n')
            f.write('tags: [models]\n')
            f.write('date: 2026-03-15\n')
            f.write('required_level: 0\n')
            f.write('sort_order: 1\n')
            f.write('---\n\n')
            f.write('An impressive open-source model.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 2)

        link1 = CuratedLink.objects.get(item_id='link-1')
        self.assertEqual(link1.title, 'Awesome Tool')
        self.assertEqual(link1.description, 'A great tool for AI development.')
        self.assertEqual(link1.category, 'tools')
        self.assertEqual(link1.url, 'https://example.com')

        link2 = CuratedLink.objects.get(item_id='link-2')
        self.assertEqual(link2.title, 'Cool Model')
        self.assertEqual(link2.description, 'An impressive open-source model.')

    def test_sync_curated_links_from_yaml_manifest(self):
        links_dir = os.path.join(self.temp_dir, 'resources', 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'links.yaml'), 'w') as f:
            f.write('- content_id: yaml-link-1\n')
            f.write('  title: "YAML Tool"\n')
            f.write('  description: "Tool description"\n')
            f.write('  url: "https://example.com/tool"\n')
            f.write('  category: tools\n')
            f.write('  tags: [AI, Tools]\n')
            f.write('  source: "Example"\n')
            f.write('  sort_order: 3\n')
            f.write('  required_level: 10\n')
            f.write('  published: true\n')
            f.write('- item_id: yaml-link-2\n')
            f.write('  title: "YAML Course"\n')
            f.write('  url: "https://example.com/course"\n')
            f.write('  category: courses\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 2)
        link = CuratedLink.objects.get(item_id='yaml-link-1')
        self.assertEqual(link.title, 'YAML Tool')
        self.assertEqual(link.description, 'Tool description')
        self.assertEqual(link.url, 'https://example.com/tool')
        self.assertEqual(link.category, 'tools')
        self.assertEqual(link.tags, ['ai', 'tools'])
        self.assertEqual(link.source, 'Example')
        self.assertEqual(link.sort_order, 3)
        self.assertEqual(link.required_level, 10)
        self.assertTrue(link.published)
        self.assertEqual(link.source_repo, 'AI-Shipping-Labs/resources')
        self.assertEqual(link.source_path, 'resources/curated-links/links.yaml')
        self.assertEqual(link.source_commit, 'test-commit-sha')

        defaulted = CuratedLink.objects.get(item_id='yaml-link-2')
        self.assertEqual(defaulted.description, '')
        self.assertEqual(defaulted.tags, [])
        self.assertEqual(defaulted.sort_order, 0)
        self.assertEqual(defaulted.required_level, 0)
        self.assertTrue(defaulted.published)

    def test_sync_curated_links_yaml_manifest_updates_existing_row(self):
        links_dir = os.path.join(self.temp_dir, 'resources', 'curated-links')
        os.makedirs(links_dir)
        manifest_path = os.path.join(links_dir, 'links.yaml')
        with open(manifest_path, 'w') as f:
            f.write('- content_id: yaml-link\n')
            f.write('  title: "Original"\n')
            f.write('  url: "https://example.com/original"\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        with open(manifest_path, 'w') as f:
            f.write('- content_id: yaml-link\n')
            f.write('  title: "Updated"\n')
            f.write('  url: "https://example.com/updated"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.items_updated, 1)
        self.assertEqual(CuratedLink.objects.filter(item_id='yaml-link').count(), 1)
        link = CuratedLink.objects.get(item_id='yaml-link')
        self.assertEqual(link.title, 'Updated')
        self.assertEqual(link.url, 'https://example.com/updated')

    def test_sync_curated_links_yaml_manifest_invalid_entry_is_partial(self):
        links_dir = os.path.join(self.temp_dir, 'resources', 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'links.yaml'), 'w') as f:
            f.write('- content_id: bad-link\n')
            f.write('  title: "Missing URL"\n')
            f.write('- content_id: good-link\n')
            f.write('  title: "Good Link"\n')
            f.write('  url: "https://example.com/good"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.status, 'partial')
        self.assertEqual(CuratedLink.objects.filter(item_id='good-link').count(), 1)
        self.assertFalse(CuratedLink.objects.filter(item_id='bad-link').exists())
        self.assertTrue(any(
            'resources/curated-links/links.yaml[0]' in error.get('file', '')
            and 'bad-link' in error.get('error', '')
            and 'url' in error.get('error', '')
            for error in sync_log.errors
        ), sync_log.errors)

    def test_sync_curated_links_yaml_failed_item_id_protected_from_stale_cleanup(self):
        CuratedLink.objects.create(
            item_id='keep-link', title='Keep',
            url='https://example.com/old', category='tools',
            source_repo='AI-Shipping-Labs/resources', published=True,
        )
        links_dir = os.path.join(self.temp_dir, 'resources', 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'links.yaml'), 'w') as f:
            f.write('- content_id: keep-link\n')
            f.write('  title: "Missing URL"\n')
            f.write('- content_id: new-link\n')
            f.write('  title: "New Link"\n')
            f.write('  url: "https://example.com/new"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.status, 'partial')
        stale_protected = CuratedLink.objects.get(item_id='keep-link')
        self.assertTrue(stale_protected.published)
        self.assertEqual(stale_protected.title, 'Keep')
        self.assertTrue(CuratedLink.objects.filter(item_id='new-link').exists())

    def test_bad_curated_links_yaml_entry_does_not_block_workshop_sync(self):
        links_dir = os.path.join(self.temp_dir, 'resources', 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'links.yaml'), 'w') as f:
            f.write('- content_id: bad-link\n')
            f.write('  title: "Missing URL"\n')

        workshop_dir = os.path.join(self.temp_dir, 'workshops', '2026-04-01-demo')
        os.makedirs(workshop_dir)
        with open(os.path.join(workshop_dir, 'workshop.yaml'), 'w') as f:
            f.write('content_id: "66666666-6666-4666-8666-666666666666"\n')
            f.write('slug: "demo-workshop"\n')
            f.write('title: "Demo Workshop"\n')
            f.write('date: "2026-04-01"\n')
            f.write('pages_required_level: 0\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.status, 'partial')
        self.assertTrue(Workshop.objects.filter(slug='demo-workshop').exists())
        self.assertTrue(any(
            'resources/curated-links/links.yaml[0]' in error.get('file', '')
            for error in sync_log.errors
        ), sync_log.errors)

    def test_sync_curated_links_body_as_description(self):
        """Body text of the markdown file becomes the link description."""
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'test-link.md'), 'w') as f:
            f.write('---\n')
            f.write('content_id: body-desc\n')
            f.write('title: "Body Test"\n')
            f.write('url: "https://example.com"\n')
            f.write('category: tools\n')
            f.write('---\n\n')
            f.write('This description comes from the body.\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        link = CuratedLink.objects.get(item_id='body-desc')
        self.assertEqual(link.description, 'This description comes from the body.')

    def test_sync_curated_links_empty_body_no_description(self):
        """Empty body results in empty description."""
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'no-desc.md'), 'w') as f:
            f.write('---\n')
            f.write('content_id: no-desc\n')
            f.write('title: "No Desc"\n')
            f.write('url: "https://example.com"\n')
            f.write('category: tools\n')
            f.write('---\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        link = CuratedLink.objects.get(item_id='no-desc')
        self.assertEqual(link.description, '')

    def test_sync_curated_links_soft_deletes_stale(self):
        """Links removed from the repo are soft-deleted."""
        CuratedLink.objects.create(
            item_id='stale-link', title='Stale',
            url='https://example.com', category='tools',
            source_repo='AI-Shipping-Labs/resources', published=True,
        )
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'new-link.md'), 'w') as f:
            f.write('---\n')
            f.write('content_id: new-link\n')
            f.write('title: "New"\n')
            f.write('url: "https://example.com"\n')
            f.write('category: tools\n')
            f.write('---\n\n')
            f.write('New link.\n')

        sync_content_source(self.source, repo_dir=self.temp_dir)
        stale = CuratedLink.objects.get(item_id='stale-link')
        self.assertFalse(stale.published)
        new = CuratedLink.objects.get(item_id='new-link')
        self.assertTrue(new.published)

    def test_sync_curated_links_skips_non_md_files(self):
        """Non-.md files in the curated-links directory are ignored."""
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'readme.txt'), 'w') as f:
            f.write('This is not a link file.\n')
        with open(os.path.join(links_dir, 'valid-link.md'), 'w') as f:
            f.write('---\n')
            f.write('content_id: valid-link\n')
            f.write('title: "Valid"\n')
            f.write('url: "https://example.com"\n')
            f.write('category: tools\n')
            f.write('---\n\n')
            f.write('A valid link.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(CuratedLink.objects.filter(item_id='valid-link').count(), 1)
        self.assertEqual(sync_log.items_created, 1)

    def test_sync_curated_links_missing_content_id_skipped(self):
        """Files without content_id are skipped with an error."""
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'bad-link.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "No ID"\n')
            f.write('url: "https://example.com"\n')
            f.write('category: tools\n')
            f.write('---\n\n')
            f.write('Missing content_id.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(CuratedLink.objects.count(), 0)
        self.assertGreater(len(sync_log.errors), 0)

    def test_sync_downloads(self):
        dl_dir = os.path.join(self.temp_dir, 'downloads')
        os.makedirs(dl_dir)
        with open(os.path.join(dl_dir, 'cheatsheet.yaml'), 'w') as f:
            f.write('title: "Cheat Sheet"\n')
            f.write('slug: "cheatsheet"\n')
            f.write('file_url: "https://example.com/file.pdf"\n')
            f.write('file_type: "pdf"\n')
            f.write('file_size_bytes: 1024\n')
            f.write('content_id: "55555555-5555-5555-5555-555555555555"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)
        dl = Download.objects.get(slug='cheatsheet')
        self.assertEqual(dl.title, 'Cheat Sheet')
        self.assertEqual(dl.source_repo, 'AI-Shipping-Labs/resources')

    def test_sync_soft_deletes_stale_recordings(self):
        # Recordings are synced as events. Issue #310: one source per
        # repo, so the existing ``self.source`` is reused.
        event_source = self.source
        Event.objects.create(
            title='Stale', slug='stale-rec', start_datetime=timezone.now(),
            source_repo='AI-Shipping-Labs/resources',
            published=True,
        )
        # Create an empty recordings directory so the sync runs the events path
        os.makedirs(os.path.join(self.temp_dir, 'recordings'))
        sync_content_source(event_source, repo_dir=self.temp_dir)
        recording = Event.objects.get(slug='stale-rec')
        self.assertFalse(recording.published)

    def test_event_round_trip_remove_then_restore(self):
        """write -> sync -> remove -> sync -> re-write -> sync for events.

        Issue #310: with one source per repo, recordings and events
        flow through the same ``ContentSource``.
        """
        event_source = self.source
        rec_dir = os.path.join(self.temp_dir, 'recordings')
        os.makedirs(rec_dir, exist_ok=True)
        rec_file = os.path.join(rec_dir, 'roundtrip-event.yaml')
        unique_title = 'Round Trip Event ZZQQ-RT-3'

        def write_file():
            with open(rec_file, 'w') as f:
                f.write(f'title: "{unique_title}"\n')
                f.write('slug: "roundtrip-event"\n')
                f.write('description: "Round trip event test"\n')
                f.write('video_url: "https://youtube.com/watch?v=rt-3"\n')
                f.write('published_at: "2026-01-15"\n')
                f.write(
                    'content_id: "88888888-8888-8888-8888-888888888888"\n'
                )

        # Step 1: write yaml -> sync -> assert published
        write_file()
        sync_content_source(event_source, repo_dir=self.temp_dir)
        event = Event.objects.get(slug='roundtrip-event')
        self.assertTrue(event.published)
        # Past-events surface lists completed events with a recording url
        # and ``published=True``. The synced event has a video_url and is
        # status='completed' by default — confirm it appears on the list.
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unique_title)

        # Step 2: remove yaml -> sync -> assert published=False
        os.remove(rec_file)
        sync_content_source(event_source, repo_dir=self.temp_dir)
        event.refresh_from_db()
        self.assertFalse(event.published)
        # Past-events surface filters on ``published=True``, so the
        # soft-deleted event must disappear.
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, unique_title)

        # Step 3: re-write yaml -> sync -> content updates, but the
        # existing row's operational visibility flag is preserved.
        write_file()
        sync_content_source(event_source, repo_dir=self.temp_dir)
        event.refresh_from_db()
        self.assertFalse(event.published)
        response = self.client.get('/events?filter=past')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, unique_title)


# ===========================================================================
# Sync Failure Tests
# ===========================================================================


class SyncFailureTest(TestCase):
    """Test sync failure handling.

    Issue #310 dropped per-type ContentSource rows, so the historical
    "invalid content_type" failure path no longer exists. Sync failures
    now come from clone errors, missing repo dirs, the max_files guard,
    or per-file parser errors. The latter is exercised throughout
    :mod:`integrations.tests.test_malformed_yaml`. The max_files guard
    is exercised here.
    """

    def test_max_files_guard_fails_sync(self):
        source = ContentSource.objects.create(repo_name='test/fail')
        source.max_files = 0
        source.save()

        temp_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(temp_dir, 'a.md'), 'w') as f:
                f.write('---\ntitle: x\n---\n')
            with self.assertLogs('integrations.services.github', level='ERROR') as logs:
                sync_content_source(source, repo_dir=temp_dir)
            self.assertIn('Sync failed for test/fail', logs.output[0])
            source.refresh_from_db()
            self.assertEqual(source.last_sync_status, 'failed')
            self.assertIn('failed', source.last_sync_log.lower())
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# GitHub App Authentication Tests
# ===========================================================================


class GitHubAppAuthTest(TestCase):
    """Test GitHub App token generation."""

    @override_settings(
        GITHUB_APP_ID='',
        GITHUB_APP_PRIVATE_KEY='',
        GITHUB_APP_INSTALLATION_ID='',
    )
    def test_missing_credentials_raises_error(self):
        from integrations.services.github import generate_github_app_token
        with self.assertRaises(GitHubSyncError) as ctx:
            generate_github_app_token()
        self.assertIn('not configured', str(ctx.exception))

    @override_settings(
        GITHUB_APP_ID='12345',
        GITHUB_APP_PRIVATE_KEY='fake-key',
        GITHUB_APP_INSTALLATION_ID='67890',
    )
    @patch('integrations.services.github_sync.client.jwt.encode')
    @patch('integrations.services.github_sync.client.requests.post')
    def test_successful_token_generation(self, mock_post, mock_jwt):
        from integrations.services.github import generate_github_app_token

        mock_jwt.return_value = 'fake-jwt-token'
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {'token': 'ghs_test_token_123'}
        mock_post.return_value = mock_response

        token = generate_github_app_token()
        self.assertEqual(token, 'ghs_test_token_123')

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertIn('67890', call_args[0][0])

    @override_settings(
        GITHUB_APP_ID='12345',
        GITHUB_APP_PRIVATE_KEY='fake-key',
        GITHUB_APP_INSTALLATION_ID='67890',
    )
    @patch('integrations.services.github_sync.client.jwt.encode')
    @patch('integrations.services.github_sync.client.requests.post')
    def test_failed_token_generation(self, mock_post, mock_jwt):
        from integrations.services.github import generate_github_app_token

        mock_jwt.return_value = 'fake-jwt-token'
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = 'Bad credentials'
        mock_post.return_value = mock_response

        with self.assertRaises(GitHubSyncError) as ctx:
            generate_github_app_token()
        self.assertIn('401', str(ctx.exception))


# ===========================================================================
# Admin Sync Page Tests
# ===========================================================================


class AdminSyncDashboardTest(TestCase):
    """Test admin sync dashboard view."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def test_dashboard_accessible_to_staff(self):
        response = self.client.get('/admin/sync/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI-Shipping-Labs/blog')

    def test_dashboard_requires_staff(self):
        self.client.logout()
        response = self.client.get('/admin/sync/')
        # Should redirect to login
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_dashboard_shows_all_sources(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/courses',
        )
        response = self.client.get('/admin/sync/')
        self.assertContains(response, 'AI-Shipping-Labs/blog')
        self.assertContains(response, 'AI-Shipping-Labs/courses')

    def test_dashboard_shows_sync_status(self):
        self.source.last_sync_status = 'success'
        self.source.last_synced_at = timezone.now()
        self.source.save()
        response = self.client.get('/admin/sync/')
        self.assertContains(response, 'success')

    def test_dashboard_shows_sync_now_button(self):
        response = self.client.get('/admin/sync/')
        self.assertContains(response, 'Sync Now')

    def test_dashboard_shows_sync_all_button(self):
        response = self.client.get('/admin/sync/')
        self.assertContains(response, 'Sync All')


class AdminSyncHistoryTest(TestCase):
    """Test admin sync history view."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def test_history_page_accessible(self):
        SyncLog.objects.create(
            source=self.source, status='success',
            items_created=5, items_updated=2,
        )
        response = self.client.get(f'/admin/sync/{self.source.pk}/history/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'success')

    def test_history_shows_error_details(self):
        SyncLog.objects.create(
            source=self.source, status='partial',
            errors=[{'file': 'bad.md', 'error': 'Parse error'}],
        )
        response = self.client.get(f'/admin/sync/{self.source.pk}/history/')
        self.assertContains(response, 'bad.md')
        self.assertContains(response, 'Parse error')


class AdminSyncTriggerTest(TestCase):
    """Test admin sync trigger action."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )

    def test_trigger_sync_redirects(self):
        with patch('django_q.tasks.async_task'):
            response = self.client.post(
                f'/admin/sync/{self.source.pk}/trigger/',
            )
        self.assertEqual(response.status_code, 302)

    def test_trigger_sync_uses_shared_enqueue_service_for_json(self):
        with patch(
            'integrations.views.admin_sync.enqueue_content_sync',
        ) as mock_enqueue:
            mock_enqueue.return_value = ContentSyncQueueResult(
                ok=True,
                queued=True,
                ran_inline=False,
                source=self.source,
                message='custom queued message',
            )
            response = self.client.post(
                f'/admin/sync/{self.source.pk}/trigger/',
                HTTP_ACCEPT='application/json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'custom queued message')
        mock_enqueue.assert_called_once_with(self.source)

    def test_trigger_sync_json_error_when_inline_fallback_sync_raises(self):
        with (
            patch(
                'integrations.services.content_sync_queue._enqueue_async_task',
                side_effect=ImportError('django-q unavailable'),
            ),
            patch(
                'integrations.services.content_sync_queue.sync_content_source',
                side_effect=Exception('inline sync error'),
            ),
            self.assertLogs('integrations.views.admin_sync', level='ERROR') as logs,
        ):
            response = self.client.post(
                f'/admin/sync/{self.source.pk}/trigger/',
                HTTP_ACCEPT='application/json',
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()['status'], 'error')
        self.assertEqual(
            response.json()['message'],
            'Sync failed for AI-Shipping-Labs/blog: inline sync error',
        )
        self.assertIn(
            'Error triggering sync for AI-Shipping-Labs/blog',
            logs.output[0],
        )

    def test_trigger_sync_requires_post(self):
        response = self.client.get(f'/admin/sync/{self.source.pk}/trigger/')
        self.assertEqual(response.status_code, 405)

    def test_trigger_sync_requires_staff(self):
        self.client.logout()
        response = self.client.post(f'/admin/sync/{self.source.pk}/trigger/')
        # Should redirect to login (302) but not to the sync dashboard
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)


class AdminSyncAllTest(TestCase):
    """Test admin sync all action."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_sync_all_redirects(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )
        with patch('django_q.tasks.async_task'):
            response = self.client.post('/admin/sync/all/')
        self.assertEqual(response.status_code, 302)

    def test_sync_all_uses_shared_enqueue_service(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
        )
        with patch(
            'integrations.views.admin_sync.enqueue_content_syncs',
        ) as mock_enqueue:
            mock_enqueue.return_value = [
                ContentSyncQueueResult(
                    ok=True,
                    queued=True,
                    ran_inline=False,
                    source=source,
                ),
            ]
            response = self.client.post(
                '/admin/sync/all/',
                HTTP_ACCEPT='application/json',
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message'], 'Sync triggered for 1 sources')
        mock_enqueue.assert_called_once_with([source])

    def test_sync_all_requires_post(self):
        response = self.client.get('/admin/sync/all/')
        self.assertEqual(response.status_code, 405)


# ===========================================================================
# Seed Content Sources Management Command Tests
# ===========================================================================


class SeedContentSourcesCommandTest(TestCase):
    """Test the seed_content_sources management command."""

    def test_seeds_default_sources(self):
        from io import StringIO

        from django.core.management import call_command

        from integrations.management.commands.seed_content_sources import (
            DEFAULT_SOURCES,
        )
        call_command('seed_content_sources', stdout=StringIO())
        for source_data in DEFAULT_SOURCES:
            self.assertTrue(
                ContentSource.objects.filter(
                    repo_name=source_data['repo_name'],
                ).exists(),
                f"Missing seeded source: {source_data['repo_name']}",
            )

    def test_seed_is_idempotent(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        count_after_first = ContentSource.objects.count()
        call_command('seed_content_sources', stdout=StringIO())
        count_after_second = ContentSource.objects.count()
        self.assertEqual(count_after_first, count_after_second)

    def test_seed_creates_expected_repos(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        repos = set(ContentSource.objects.values_list('repo_name', flat=True))
        expected = {
            'AI-Shipping-Labs/content',
            'AI-Shipping-Labs/python-course',
            'AI-Shipping-Labs/workshops-content',
        }
        self.assertEqual(repos, expected)

    def test_content_sources_are_private(self):
        """All seeded content sources are marked private (require GitHub App auth)."""
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        for source in ContentSource.objects.all():
            self.assertTrue(
                source.is_private,
                f"Expected {source.repo_name} to be marked private",
            )

    def test_seeds_exactly_three_rows(self):
        """Issue #310: one ContentSource per canonical repo (3 total)."""
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        self.assertEqual(ContentSource.objects.count(), 3)


# ===========================================================================
# Django Admin Registration Tests
# ===========================================================================


class AdminRegistrationTest(TestCase):
    """Test that models are registered in Django admin."""

    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_content_source_admin_accessible(self):
        response = self.client.get('/admin/integrations/contentsource/')
        self.assertEqual(response.status_code, 200)

    def test_sync_log_admin_accessible(self):
        response = self.client.get('/admin/integrations/synclog/')
        self.assertEqual(response.status_code, 200)

    def test_content_source_admin_add(self):
        response = self.client.get('/admin/integrations/contentsource/add/')
        self.assertEqual(response.status_code, 200)


# ===========================================================================
# Direct Admin Edit Flag Test
# ===========================================================================


class DirectAdminEditFlagTest(TestCase):
    """Test that directly created/edited content has source_repo = null."""

    def test_article_created_without_source(self):
        """Articles created directly in admin have source_repo=None by default."""
        article = Article.objects.create(
            title='Admin Article', slug='admin-article', date=date.today(),
        )
        self.assertIsNone(article.source_repo)
        self.assertIsNone(article.source_path)
        self.assertIsNone(article.source_commit)

    def test_sync_does_not_overwrite_studio_article(self):
        """When slug matches but source_repo differs, sync skips and logs collision."""
        Article.objects.create(
            title='Admin Version', slug='overwrite-me', date=date.today(),
            source_repo=None,
        )

        source = ContentSource.objects.create(
            repo_name='test-org/blog',
        )
        temp_dir = tempfile.mkdtemp()
        try:
            filepath = os.path.join(temp_dir, 'overwrite-me.md')
            with open(filepath, 'w') as f:
                f.write('---\n')
                f.write('title: "Repo Version"\n')
                f.write('slug: "overwrite-me"\n')
                f.write('date: "2026-01-15"\n')
                f.write('content_id: "e5f6a7b8-c9d0-1234-efab-345678901234"\n')
                f.write('---\n')
                f.write('Content from repo.\n')

            sync_log = sync_content_source(source, repo_dir=temp_dir)

            # Studio article should be untouched
            article = Article.objects.get(slug='overwrite-me')
            self.assertEqual(article.title, 'Admin Version')
            self.assertIsNone(article.source_repo)
            # Slug collision logged
            self.assertTrue(
                any('Slug collision' in str(e.get('error', ''))
                    for e in sync_log.errors),
            )
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# S3 Image Upload Tests
# ===========================================================================


class S3ImageUploadTest(TestCase):
    """Test upload_images_to_s3 with MD5/ETag deduplication."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/content',
        )
        self.temp_dir = tempfile.mkdtemp()
        # Create a test image file
        self.img_path = os.path.join(self.temp_dir, 'hero.png')
        self.img_content = b'\x89PNG fake image data for testing'
        with open(self.img_path, 'wb') as f:
            f.write(self.img_content)
        self.img_md5 = hashlib.md5(self.img_content).hexdigest()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @override_settings(AWS_S3_CONTENT_BUCKET='')
    def test_skips_when_bucket_not_configured(self):
        from integrations.services.github import upload_images_to_s3
        result = upload_images_to_s3(self.temp_dir, self.source)
        self.assertEqual(result, {'uploaded': 0, 'skipped': 0, 'errors': []})

    @override_settings(
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_uploads_new_image(self, mock_boto_client):
        from integrations.services.github import upload_images_to_s3

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        # No existing objects
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{'Contents': []}]
        mock_s3.get_paginator.return_value = mock_paginator

        result = upload_images_to_s3(self.temp_dir, self.source)

        self.assertEqual(result['uploaded'], 1)
        self.assertEqual(result['skipped'], 0)
        mock_s3.upload_file.assert_called_once()
        call_args = mock_s3.upload_file.call_args
        self.assertEqual(call_args[0][1], 'test-bucket')
        self.assertEqual(call_args[0][2], 'content/hero.png')

    @override_settings(
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_skips_when_etag_matches(self, mock_boto_client):
        from integrations.services.github import upload_images_to_s3

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        # Existing object with matching ETag (quoted, as S3 returns it)
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{
            'Contents': [{
                'Key': 'content/hero.png',
                'ETag': f'"{self.img_md5}"',
            }],
        }]
        mock_s3.get_paginator.return_value = mock_paginator

        result = upload_images_to_s3(self.temp_dir, self.source)

        self.assertEqual(result['uploaded'], 0)
        self.assertEqual(result['skipped'], 1)
        mock_s3.upload_file.assert_not_called()

    @override_settings(
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_uploads_when_etag_differs(self, mock_boto_client):
        from integrations.services.github import upload_images_to_s3

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        # Existing object with different ETag
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{
            'Contents': [{
                'Key': 'content/hero.png',
                'ETag': '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            }],
        }]
        mock_s3.get_paginator.return_value = mock_paginator

        result = upload_images_to_s3(self.temp_dir, self.source)

        self.assertEqual(result['uploaded'], 1)
        self.assertEqual(result['skipped'], 0)
        mock_s3.upload_file.assert_called_once()

    @override_settings(
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_ignores_non_image_files(self, mock_boto_client):
        from integrations.services.github import upload_images_to_s3

        # Create a non-image file
        with open(os.path.join(self.temp_dir, 'article.md'), 'w') as f:
            f.write('# Hello')
        # Remove the image so only .md remains
        os.remove(self.img_path)

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{'Contents': []}]
        mock_s3.get_paginator.return_value = mock_paginator

        result = upload_images_to_s3(self.temp_dir, self.source)

        self.assertEqual(result['uploaded'], 0)
        mock_s3.upload_file.assert_not_called()

    @override_settings(
        AWS_S3_CONTENT_BUCKET='test-bucket',
        AWS_S3_CONTENT_REGION='us-east-1',
        AWS_ACCESS_KEY_ID='fake',
        AWS_SECRET_ACCESS_KEY='fake',
    )
    @patch('integrations.services.github_sync.media.boto3.client')
    def test_upload_error_recorded(self, mock_boto_client):
        from integrations.services.github import upload_images_to_s3

        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{'Contents': []}]
        mock_s3.get_paginator.return_value = mock_paginator
        mock_s3.upload_file.side_effect = Exception('Access Denied')

        result = upload_images_to_s3(self.temp_dir, self.source)

        self.assertEqual(result['uploaded'], 0)
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('Access Denied', result['errors'][0]['error'])


# ===========================================================================
# Walker tests — named by intent for spec coverage (issue #310 / #330)
# ===========================================================================
#
# The three classes below are organised by intent (walker happy path,
# walker idempotency, course rename idempotency) so an engineer searching
# for "where is the walker happy path test" or "where is the
# course-rename idempotency test" can find them in a single
# ``grep -rn '^class WalkerHappyPathTest' integrations/tests/``.
#
# The behaviour they assert is also exercised by the per-content-type
# classes higher in this file (``SyncArticlesTest``, ``SyncCoursesTest``,
# ``SyncProjectsTest``, ``SyncResourcesTest``,
# ``SyncSingleCourseRepoTest``). These named classes do not replace
# those — both organisations coexist.


class _WalkerFixtureBase(TestCase):
    """Mixed-content fixture shared by the walker tests.

    Drops one of every primary content type into a single temp repo:

    - one article at the repo root
    - one project under ``projects/``
    - one course folder with one module and one unit
    - one recording yaml under ``recordings/``

    The combined fixture exercises the single ``_sync_repo`` walker that
    #310 introduced — every content type goes through one classify pass
    and one dispatch loop, so no file may be double-claimed (e.g. a
    course unit md must not also become an Article).
    """

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/walker-fixture',
        )
        self.temp_dir = tempfile.mkdtemp(prefix='walker-fixture-')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # Stable content_ids so the fixture is deterministic across runs.
    ARTICLE_CONTENT_ID = '11111111-1111-1111-1111-111111111111'
    PROJECT_CONTENT_ID = '22222222-2222-2222-2222-222222222222'
    COURSE_CONTENT_ID = '33333333-3333-3333-3333-333333333333'
    MODULE_UNIT_CONTENT_ID = '44444444-4444-4444-4444-444444444444'
    RECORDING_CONTENT_ID = '55555555-5555-5555-5555-555555555555'

    ARTICLE_SLUG = 'walker-article'
    PROJECT_SLUG = 'walker-project'
    COURSE_SLUG = 'walker-course'
    MODULE_DIRNAME = 'module-01'
    UNIT_FILENAME = 'unit-01.md'
    RECORDING_SLUG = 'walker-recording'

    def _write_mixed_fixture(self):
        # Article at repo root.
        article_path = os.path.join(self.temp_dir, f'{self.ARTICLE_SLUG}.md')
        with open(article_path, 'w') as f:
            f.write('---\n')
            f.write('title: "Walker Article"\n')
            f.write(f'slug: "{self.ARTICLE_SLUG}"\n')
            f.write('description: "An article."\n')
            f.write('date: "2026-01-15"\n')
            f.write('author: "Test"\n')
            f.write(f'content_id: "{self.ARTICLE_CONTENT_ID}"\n')
            f.write('---\n')
            f.write('Article body.\n')

        # Project under projects/ — the dispatcher buckets path-shaped
        # content by location.
        projects_dir = os.path.join(self.temp_dir, 'projects')
        os.makedirs(projects_dir, exist_ok=True)
        project_path = os.path.join(projects_dir, f'{self.PROJECT_SLUG}.md')
        with open(project_path, 'w') as f:
            f.write('---\n')
            f.write('title: "Walker Project"\n')
            f.write(f'slug: "{self.PROJECT_SLUG}"\n')
            f.write('description: "A project."\n')
            f.write('difficulty: "beginner"\n')
            f.write('date: "2026-01-15"\n')
            f.write(f'content_id: "{self.PROJECT_CONTENT_ID}"\n')
            f.write('---\n')
            f.write('Project body.\n')

        # Course with one module + one unit.
        course_dir = os.path.join(self.temp_dir, self.COURSE_SLUG)
        os.makedirs(course_dir, exist_ok=True)
        with open(os.path.join(course_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Walker Course"\n')
            f.write(f'slug: "{self.COURSE_SLUG}"\n')
            f.write('description: "A course."\n')
            f.write('instructor_name: "Test"\n')
            f.write('required_level: 0\n')
            f.write(f'content_id: "{self.COURSE_CONTENT_ID}"\n')

        module_dir = os.path.join(course_dir, self.MODULE_DIRNAME)
        os.makedirs(module_dir, exist_ok=True)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: "Module 1"\n')
            f.write('sort_order: 1\n')

        with open(os.path.join(module_dir, self.UNIT_FILENAME), 'w') as f:
            f.write('---\n')
            f.write('title: "Unit 1"\n')
            f.write('sort_order: 1\n')
            f.write(f'content_id: "{self.MODULE_UNIT_CONTENT_ID}"\n')
            f.write('---\n')
            f.write('Unit body.\n')

        # Recording under recordings/ — surfaces as an Event row.
        recordings_dir = os.path.join(self.temp_dir, 'recordings')
        os.makedirs(recordings_dir, exist_ok=True)
        with open(
            os.path.join(recordings_dir, f'{self.RECORDING_SLUG}.yaml'), 'w',
        ) as f:
            f.write('title: "Walker Recording"\n')
            f.write(f'slug: "{self.RECORDING_SLUG}"\n')
            f.write('description: "A recording."\n')
            f.write('video_url: "https://www.youtube.com/watch?v=walker"\n')
            f.write('published_at: "2026-01-15"\n')
            f.write(f'content_id: "{self.RECORDING_CONTENT_ID}"\n')


class WalkerHappyPathTest(_WalkerFixtureBase):
    """One sync pass over a mixed-content repo creates one row per type.

    Wraps the per-type assertions ``SyncArticlesTest.test_sync_creates_article``,
    ``SyncProjectsTest.test_sync_creates_project``,
    ``SyncCoursesTest.test_sync_creates_course_with_modules_and_units``, and
    ``SyncResourcesTest.test_sync_recordings`` in a single fixture so the
    walker is exercised end-to-end, not piecemeal.

    Asserts no file is double-claimed: the course's unit ``.md`` must
    NOT also produce an Article row, and the recording's yaml must NOT
    also produce some other content type. This is the "no overlapping
    claim" guard from the #310 spec risk callout.
    """

    def test_walker_creates_one_row_per_content_type(self):
        self._write_mixed_fixture()

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertIn(
            sync_log.status, ('success', 'partial'),
            f'Expected success/partial, got {sync_log.status}; '
            f'errors: {sync_log.errors}',
        )

        # Exactly one row per content type — no double-claim.
        self.assertEqual(
            Article.objects.filter(source_repo=self.source.repo_name).count(),
            1,
            'Walker must create exactly one Article. Double-claim '
            'regression: a course unit .md was also classified as an article.',
        )
        self.assertEqual(
            Project.objects.filter(source_repo=self.source.repo_name).count(),
            1,
        )
        self.assertEqual(
            Course.objects.filter(source_repo=self.source.repo_name).count(),
            1,
        )
        course = Course.objects.get(slug=self.COURSE_SLUG)
        self.assertEqual(Module.objects.filter(course=course).count(), 1)
        self.assertEqual(
            Unit.objects.filter(module__course=course).count(), 1,
        )
        self.assertEqual(
            Event.objects.filter(
                source_repo=self.source.repo_name,
                slug=self.RECORDING_SLUG,
            ).count(),
            1,
        )

        # No Article was created from the unit ``.md`` (which lives
        # inside a course folder) — the walker dispatched it correctly.
        self.assertFalse(
            Article.objects.filter(slug='unit-01').exists(),
            'Course unit .md leaked into Article rows.',
        )


class WalkerIdempotencyTest(_WalkerFixtureBase):
    """Running the walker twice over identical content is a no-op.

    Wraps ``SyncArticlesUnchangedTest``, ``SyncProjectsUnchangedTest``,
    ``SyncCoursesUnchangedTest``, ``SyncResourcesUnchangedTest`` (and
    friends) at the walker level: one fixture, one assertion shape.
    Asserts the second pass reports zero ``items_created``, zero
    ``items_updated``, ``items_unchanged > 0``, zero ``items_deleted``,
    no errors, and every row's pk is preserved.
    """

    def test_second_sync_is_a_noop(self):
        self._write_mixed_fixture()

        first = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertIn(first.status, ('success', 'partial'),
                      f'First sync errors: {first.errors}')

        # Snapshot pks of every row created.
        article_pk = Article.objects.get(slug=self.ARTICLE_SLUG).pk
        project_pk = Project.objects.get(slug=self.PROJECT_SLUG).pk
        course = Course.objects.get(slug=self.COURSE_SLUG)
        course_pk = course.pk
        module = Module.objects.get(course=course)
        module_pk = module.pk
        unit_pk = Unit.objects.get(module=module).pk
        event_pk = Event.objects.get(slug=self.RECORDING_SLUG).pk

        # Second sync — same fixture, no changes on disk.
        second = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(
            second.errors, [],
            f'Second sync produced errors: {second.errors}',
        )
        self.assertEqual(second.items_created, 0)
        self.assertEqual(second.items_updated, 0)
        self.assertGreater(
            second.items_unchanged, 0,
            'Second sync must record at least one unchanged row.',
        )
        self.assertEqual(second.items_deleted, 0)

        # Every pk preserved.
        self.assertEqual(Article.objects.get(slug=self.ARTICLE_SLUG).pk, article_pk)
        self.assertEqual(Project.objects.get(slug=self.PROJECT_SLUG).pk, project_pk)
        self.assertEqual(Course.objects.get(slug=self.COURSE_SLUG).pk, course_pk)
        self.assertEqual(Module.objects.get(course_id=course_pk).pk, module_pk)
        self.assertEqual(
            Unit.objects.get(module_id=module_pk).pk, unit_pk,
        )
        self.assertEqual(
            Event.objects.get(slug=self.RECORDING_SLUG).pk, event_pk,
        )


class CourseRenameIdempotencyTest(TestCase):
    """Re-syncing with the same ``content_id`` but a new slug updates the
    existing Course row in place.

    Mirrors the assertions of
    ``SyncSingleCourseRepoTest.test_root_course_slug_change_updates_existing_course_by_content_id``
    in a class named by intent so a spec-coverage grep finds it. The
    original test stays in ``SyncSingleCourseRepoTest`` for engineers
    debugging single-course-repo behaviour.
    """

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/course-rename',
        )
        self.temp_dir = tempfile.mkdtemp(prefix='course-rename-')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_root_course_yaml(self, *, slug, content_id):
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Course Title"\n')
            f.write(f'slug: "{slug}"\n')
            f.write('description: "Course description."\n')
            f.write('instructor_name: "Test"\n')
            f.write('required_level: 0\n')
            f.write(f'content_id: "{content_id}"\n')

    def _write_module_with_unit(self):
        module_dir = os.path.join(self.temp_dir, '01-intro')
        os.makedirs(module_dir, exist_ok=True)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: "Intro"\n')
            f.write('sort_order: 1\n')
        with open(os.path.join(module_dir, '01-why.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Why"\n')
            f.write('sort_order: 1\n')
            f.write('content_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"\n')
            f.write('---\n')
            f.write('Body.\n')

    def test_slug_change_with_same_content_id_updates_course_in_place(self):
        content_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        original_slug = 'python-course'
        renamed_slug = 'python-course-workshop'

        self._write_root_course_yaml(
            slug=original_slug, content_id=content_id,
        )
        self._write_module_with_unit()

        first_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertIn(first_log.status, ('success', 'partial'))

        original_course = Course.objects.get(slug=original_slug)
        original_course_pk = original_course.pk
        original_module_pks = list(
            Module.objects.filter(course=original_course)
            .values_list('pk', flat=True)
        )
        original_unit_pks = list(
            Unit.objects.filter(module__course=original_course)
            .values_list('pk', flat=True)
        )

        # Rewrite the course.yaml with a new slug but the same content_id.
        self._write_root_course_yaml(
            slug=renamed_slug, content_id=content_id,
        )

        second_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertIn(second_log.status, ('success', 'partial'))

        # No duplicate Course row, no stale draft of the old slug.
        self.assertEqual(
            Course.objects.filter(source_repo=self.source.repo_name).count(),
            1,
            'Slug change with same content_id must not duplicate the Course.',
        )
        self.assertFalse(
            Course.objects.filter(slug=original_slug).exists(),
            'Old slug must be gone — no stale draft row.',
        )

        course_after = Course.objects.get(slug=renamed_slug)
        self.assertEqual(course_after.pk, original_course_pk)
        self.assertEqual(str(course_after.content_id), content_id)
        # Course stays published — no soft-delete on the slug change.
        self.assertEqual(course_after.status, 'published')

        # Stats: 0 created, 1 updated.
        self.assertEqual(second_log.items_created, 0)
        self.assertEqual(second_log.items_updated, 1)

        # Module + Unit pks preserved — the rename did not cascade.
        self.assertEqual(
            list(
                Module.objects.filter(course=course_after)
                .values_list('pk', flat=True)
            ),
            original_module_pks,
        )
        self.assertEqual(
            list(
                Unit.objects.filter(module__course=course_after)
                .values_list('pk', flat=True)
            ),
            original_unit_pks,
        )
