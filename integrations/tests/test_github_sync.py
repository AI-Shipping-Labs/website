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
import uuid
from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from content.models import (
    Article,
    Course,
    CuratedLink,
    Download,
    Module,
    Project,
    Unit,
)
from events.models import Event
from integrations.models import ContentSource, SyncLog
from integrations.services.github import (
    GitHubSyncError,
    find_content_source,
    rewrite_image_urls,
    sync_content_source,
    validate_webhook_signature,
)

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


class ContentSourceModelTest(TestCase):
    """Test ContentSource model fields and behavior."""

    def test_create_content_source(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
            webhook_secret='secret123',
            is_private=False,
        )
        self.assertEqual(source.repo_name, 'AI-Shipping-Labs/blog')
        self.assertEqual(source.content_type, 'article')
        self.assertFalse(source.is_private)
        self.assertIsNone(source.last_synced_at)
        self.assertIsNone(source.last_sync_status)
        self.assertIsNone(source.last_sync_log)
        self.assertIsNotNone(source.id)
        self.assertIsNotNone(source.created_at)

    def test_content_source_str(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        self.assertEqual(str(source), 'AI-Shipping-Labs/blog (article)')

    def test_content_source_short_name(self):
        source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        self.assertEqual(source.short_name, 'blog')

    def test_repo_name_unique(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        with self.assertRaises(IntegrityError):
            ContentSource.objects.create(
                repo_name='AI-Shipping-Labs/blog',
                content_type='article',
            )

    def test_content_type_choices(self):
        for ct in ['article', 'course', 'resource', 'project', 'interview_question']:
            source = ContentSource.objects.create(
                repo_name=f'test-org/{ct}-repo',
                content_type=ct,
            )
            self.assertEqual(source.content_type, ct)

    def test_is_private_default_false(self):
        source = ContentSource.objects.create(
            repo_name='test/repo',
            content_type='article',
        )
        self.assertFalse(source.is_private)

    def test_private_source(self):
        source = ContentSource.objects.create(
            repo_name='test/private-repo',
            content_type='course',
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
            content_type='article',
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

    def test_sync_log_str(self):
        log = SyncLog.objects.create(
            source=self.source,
            status='success',
        )
        self.assertIn('AI-Shipping-Labs/blog', str(log))
        self.assertIn('success', str(log))

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
            content_type='article',
            content_path='blog',
        )
        found = find_content_source('AI-Shipping-Labs/content')
        self.assertEqual(found.first().pk, source.pk)

    def test_find_multiple_sources_for_monorepo(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses',
        )
        found = find_content_source('AI-Shipping-Labs/content')
        self.assertEqual(found.count(), 2)

    def test_find_nonexistent_source(self):
        found = find_content_source('nonexistent/repo')
        self.assertFalse(found.exists())


# ===========================================================================
# Image URL Rewriting Tests
# ===========================================================================


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


class GitHubWebhookEndpointTest(TestCase):
    """Test POST /api/webhooks/github endpoint."""

    def setUp(self):
        self.client = Client()
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
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


class SyncArticlesTest(TestCase):
    """Test syncing articles from a mock repo directory."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_article(self, filename, frontmatter_dict, body):
        """Helper to write a markdown file with frontmatter."""
        filepath = os.path.join(self.temp_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if 'content_id' not in frontmatter_dict:
            frontmatter_dict['content_id'] = str(uuid.uuid4())
        lines = ['---']
        for key, value in frontmatter_dict.items():
            if isinstance(value, list):
                lines.append(f'{key}:')
                for item in value:
                    lines.append(f'  - "{item}"')
            else:
                lines.append(f'{key}: "{value}"')
        lines.append('---')
        lines.append(body)
        with open(filepath, 'w') as f:
            f.write('\n'.join(lines))

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
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
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
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
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
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_deleted, 1)
        article = Article.objects.get(slug='stale-article')
        self.assertFalse(article.published)
        self.assertEqual(article.status, 'draft')

    def test_sync_does_not_soft_delete_direct_admin_edits(self):
        """Articles with source_repo = null (direct admin edits) are not touched."""
        Article.objects.create(
            title='Admin Edit', slug='admin-article', date=date.today(),
            source_repo=None,
            published=True,
        )
        sync_content_source(self.source, repo_dir=self.temp_dir)
        article = Article.objects.get(slug='admin-article')
        self.assertTrue(article.published)

    def test_sync_skips_readme(self):
        """README.md files should be skipped."""
        self._write_article(
            'README.MD',
            {'title': 'Readme', 'slug': 'readme'},
            'This is the readme.',
        )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 0)
        self.assertFalse(Article.objects.filter(slug='readme').exists())

    def test_sync_multiple_articles(self):
        for i in range(3):
            self._write_article(
                f'article-{i}.md',
                {'title': f'Article {i}', 'slug': f'article-{i}', 'date': '2026-01-15'},
                f'Body of article {i}.',
            )
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 3)
        self.assertEqual(Article.objects.filter(source_repo='AI-Shipping-Labs/blog').count(), 3)

    def test_sync_log_created(self):
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertIsNotNone(sync_log)
        self.assertEqual(sync_log.source, self.source)
        self.assertIsNotNone(sync_log.finished_at)

    def test_sync_updates_source_status(self):
        sync_content_source(self.source, repo_dir=self.temp_dir)
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
        filepath = os.path.join(self.temp_dir, 'bad-article.md')
        with open(filepath, 'wb') as f:
            f.write(b'\x00\x01\x02---\ntitle: bad\n---\n\x80\x81')
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
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
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
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


class SyncProjectsTest(TestCase):
    """Test syncing projects from a mock repo directory."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/projects',
            content_type='project',
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


class SyncCoursesTest(TestCase):
    """Test syncing courses from a mock repo directory."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/courses',
            content_type='course',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_course_structure(self):
        """Create a minimal course directory structure."""
        course_dir = os.path.join(self.temp_dir, 'python-data-ai')
        os.makedirs(course_dir)

        # course.yaml
        with open(os.path.join(course_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Python for Data AI"\n')
            f.write('slug: "python-data-ai"\n')
            f.write('description: "Learn Python"\n')
            f.write('instructor_name: "Test Instructor"\n')
            f.write('required_level: 0\n')
            f.write('content_id: "22222222-2222-2222-2222-222222222222"\n')
            f.write('tags:\n  - python\n  - data\n')

        # module directory
        module_dir = os.path.join(course_dir, 'module-01-setup')
        os.makedirs(module_dir)

        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: "Getting Started"\n')
            f.write('sort_order: 1\n')

        # unit file
        with open(os.path.join(module_dir, 'unit-01-intro.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Introduction"\n')
            f.write('sort_order: 1\n')
            f.write('is_preview: true\n')
            f.write('content_id: "33333333-3333-3333-3333-333333333333"\n')
            f.write('---\n')
            f.write('Welcome to the course!\n')

        return course_dir

    def test_sync_creates_course_with_modules_and_units(self):
        self._create_course_structure()
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

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
        sync_content_source(self.source, repo_dir=self.temp_dir)
        course = Course.objects.get(slug='stale-course')
        self.assertEqual(course.status, 'draft')

    def test_sync_ignores_legacy_is_free_key_in_yaml(self):
        """A leftover `is_free` key in course.yaml must not break sync.

        The field was removed in favor of deriving from `required_level`,
        but older content YAML may still contain the key. The parser must
        silently ignore it.
        """
        course_dir = os.path.join(self.temp_dir, 'legacy-course')
        os.makedirs(course_dir)
        with open(os.path.join(course_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Legacy Course"\n')
            f.write('slug: "legacy-course"\n')
            f.write('description: "Has leftover is_free key."\n')
            f.write('instructor_name: "Test"\n')
            f.write('required_level: 0\n')
            # Deprecated key that must be silently ignored by sync.
            f.write('is_free: true\n')
            f.write('content_id: "44444444-4444-4444-4444-444444444444"\n')

        module_dir = os.path.join(course_dir, 'module-01')
        os.makedirs(module_dir)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: "Intro"\n')
            f.write('sort_order: 1\n')
        with open(os.path.join(module_dir, 'unit-01.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Unit 1"\n')
            f.write('sort_order: 1\n')
            f.write('content_id: "55555555-5555-5555-5555-555555555555"\n')
            f.write('---\n')
            f.write('Body.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertIn(sync_log.status, ('success', 'partial'))
        course = Course.objects.get(slug='legacy-course')
        self.assertEqual(course.required_level, 0)
        # The property derives from required_level, not from the YAML key.
        self.assertTrue(course.is_free)


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
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/python-course',
            content_type='course',
            content_path='',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_root_course_yaml(self, content_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
                                slug='python-course'):
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Python Course"\n')
            f.write(f'slug: "{slug}"\n')
            f.write('description: "Learn Python from scratch."\n')
            f.write('instructor_name: "Alexey Grigorev"\n')
            f.write('required_level: 20\n')
            f.write(f'content_id: "{content_id}"\n')
            f.write('tags:\n  - python\n  - fundamentals\n')

    def _write_module(self, dirname, title, content_id, sort_order=None):
        module_dir = os.path.join(self.temp_dir, dirname)
        os.makedirs(module_dir, exist_ok=True)
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write(f'title: "{title}"\n')
            f.write(f'content_id: "{content_id}"\n')
            if sort_order is not None:
                f.write(f'sort_order: {sort_order}\n')
        return module_dir

    def _write_unit(self, module_dir, filename, title, content_id, body='Body text.\n'):
        with open(os.path.join(module_dir, filename), 'w') as f:
            f.write('---\n')
            f.write(f'title: "{title}"\n')
            f.write(f'content_id: "{content_id}"\n')
            f.write('---\n')
            f.write(body)

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

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

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
        self.assertEqual(unit.sort_order, 1)

    def test_no_root_course_yaml_falls_back_to_multi_course_walk(self):
        """Without root course.yaml, each child dir with course.yaml is its own course (regression guard)."""
        # Two child course dirs, each with their own course.yaml + module + unit.
        for idx, slug in enumerate(['course-a', 'course-b'], start=1):
            cdir = os.path.join(self.temp_dir, slug)
            os.makedirs(cdir)
            with open(os.path.join(cdir, 'course.yaml'), 'w') as f:
                f.write(f'title: "Course {slug}"\n')
                f.write(f'slug: "{slug}"\n')
                f.write(f'content_id: "1{idx:07d}-1111-1111-1111-111111111111"\n')

            mdir = os.path.join(cdir, '01-mod')
            os.makedirs(mdir)
            with open(os.path.join(mdir, 'module.yaml'), 'w') as f:
                f.write('title: "Module 1"\n')

            with open(os.path.join(mdir, '01-intro.md'), 'w') as f:
                f.write('---\n')
                f.write('title: "Intro"\n')
                f.write(f'content_id: "2{idx:07d}-2222-2222-2222-222222222222"\n')
                f.write('---\n')
                f.write('Body.\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

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
        child = os.path.join(self.temp_dir, 'child-course')
        os.makedirs(child)
        with open(os.path.join(child, 'course.yaml'), 'w') as f:
            f.write('title: "Child Course"\n')
            f.write('slug: "child-course"\n')
            f.write('content_id: "dddddddd-dddd-dddd-dddd-dddddddddddd"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

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

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
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
        with open(os.path.join(self.temp_dir, 'course.yaml'), 'w') as f:
            f.write('title: "Python Course"\n')
            f.write('slug: "python-course"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

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

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

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


class SyncResourcesTest(TestCase):
    """Test syncing resources (recordings, curated links, downloads)."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/resources',
            content_type='resource',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sync_recordings(self):
        # Recordings are now synced as events via content_type='event'
        event_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/resources',
            content_type='event',
            content_path='recordings',
        )
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
        # Recordings are now synced as events via content_type='event'
        event_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/resources',
            content_type='event',
            content_path='recordings',
        )
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


# ===========================================================================
# Sync Failure Tests
# ===========================================================================


class SyncFailureTest(TestCase):
    """Test sync failure handling."""

    def test_sync_with_invalid_content_type(self):
        source = ContentSource.objects.create(
            repo_name='test/invalid',
            content_type='article',
        )
        # Manually set an invalid content type after creation
        ContentSource.objects.filter(pk=source.pk).update(content_type='invalid')
        source.refresh_from_db()

        temp_dir = tempfile.mkdtemp()
        try:
            sync_log = sync_content_source(source, repo_dir=temp_dir)
            self.assertEqual(sync_log.status, 'failed')
            self.assertTrue(len(sync_log.errors) > 0)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sync_failure_updates_source(self):
        source = ContentSource.objects.create(
            repo_name='test/fail',
            content_type='article',
        )
        ContentSource.objects.filter(pk=source.pk).update(content_type='invalid')
        source.refresh_from_db()

        temp_dir = tempfile.mkdtemp()
        try:
            sync_content_source(source, repo_dir=temp_dir)
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
    @patch('integrations.services.github.jwt.encode')
    @patch('integrations.services.github.requests.post')
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
    @patch('integrations.services.github.jwt.encode')
    @patch('integrations.services.github.requests.post')
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
            content_type='article',
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
            content_type='course',
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
            content_type='article',
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
            content_type='article',
        )

    def test_trigger_sync_redirects(self):
        with patch('integrations.views.admin_sync.sync_content_source'):
            response = self.client.post(
                f'/admin/sync/{self.source.pk}/trigger/',
            )
        self.assertEqual(response.status_code, 302)

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
            content_type='article',
        )
        with patch('integrations.views.admin_sync.sync_content_source'):
            response = self.client.post('/admin/sync/all/')
        self.assertEqual(response.status_code, 302)

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
        out = StringIO()
        call_command('seed_content_sources', stdout=out)
        # 4 entries from the AI-Shipping-Labs/content monorepo
        # + 1 entry for AI-Shipping-Labs/python-course (single-course repo)
        self.assertEqual(ContentSource.objects.count(), 5)

    def test_seed_is_idempotent(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        call_command('seed_content_sources', stdout=StringIO())
        self.assertEqual(ContentSource.objects.count(), 5)

    def test_seed_creates_expected_repos(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        repos = set(ContentSource.objects.values_list('repo_name', flat=True))
        expected = {
            'AI-Shipping-Labs/content',
            'AI-Shipping-Labs/python-course',
        }
        self.assertEqual(repos, expected)

    def test_all_sources_are_private(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        self.assertTrue(
            all(s.is_private for s in ContentSource.objects.all())
        )

    def test_content_types_correct(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        types = set(ContentSource.objects.values_list('content_type', flat=True))
        self.assertEqual(types, {'article', 'course', 'project', 'interview_question'})

    def test_content_paths_correct(self):
        from io import StringIO

        from django.core.management import call_command
        call_command('seed_content_sources', stdout=StringIO())
        # Two sources have content_type='course' (the monorepo's courses/
        # subtree and the standalone python-course repo). Look them up by
        # (repo_name, content_type) instead.
        paths = {
            (s.repo_name, s.content_type): s.content_path
            for s in ContentSource.objects.all()
        }
        self.assertEqual(paths[('AI-Shipping-Labs/content', 'article')], 'blog')
        self.assertEqual(paths[('AI-Shipping-Labs/content', 'course')], 'courses')
        self.assertEqual(paths[('AI-Shipping-Labs/content', 'project')], 'projects')
        self.assertEqual(
            paths[('AI-Shipping-Labs/content', 'interview_question')],
            'interview-questions',
        )
        self.assertEqual(
            paths[('AI-Shipping-Labs/python-course', 'course')], '',
        )


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
            content_type='article',
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
            content_type='article',
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
    @patch('integrations.services.github.boto3.client')
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
    @patch('integrations.services.github.boto3.client')
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
    @patch('integrations.services.github.boto3.client')
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
    @patch('integrations.services.github.boto3.client')
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
    @patch('integrations.services.github.boto3.client')
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
