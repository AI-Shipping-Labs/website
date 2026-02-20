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
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from content.models import (
    Article, Course, CuratedLink, Download, Module, Project, Recording, Unit,
)
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
        with self.assertRaises(Exception):
            ContentSource.objects.create(
                repo_name='AI-Shipping-Labs/blog',
                content_type='article',
            )

    def test_content_type_choices(self):
        for ct in ['article', 'course', 'resource', 'project']:
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
        log1 = SyncLog.objects.create(source=self.source, status='success')
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
        recording = Recording.objects.create(
            title='Test', slug='test-rec', date=date.today(),
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
            course=course, title='Module 1',
            source_repo='AI-Shipping-Labs/courses',
            source_path='test-course/module-01',
        )
        self.assertEqual(module.source_repo, 'AI-Shipping-Labs/courses')

    def test_unit_source_fields(self):
        course = Course.objects.create(title='Test', slug='test-cu')
        module = Module.objects.create(course=course, title='M1')
        unit = Unit.objects.create(
            module=module, title='Unit 1',
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
            repo_name='AI-Shipping-Labs/blog',
            content_type='article',
        )
        found = find_content_source('AI-Shipping-Labs/blog')
        self.assertEqual(found.pk, source.pk)

    def test_find_nonexistent_source(self):
        found = find_content_source('nonexistent/repo')
        self.assertIsNone(found)


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
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
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

    def test_sync_overwrites_direct_admin_edit_by_slug(self):
        """If slug matches in repo, sync overwrites the direct admin edit."""
        Article.objects.create(
            title='Admin Version', slug='same-slug', date=date.today(),
            source_repo=None,  # direct admin edit
            published=True,
        )
        self._write_article(
            'same-slug.md',
            {'title': 'Repo Version', 'slug': 'same-slug', 'date': '2026-01-15'},
            'From repo.',
        )
        sync_content_source(self.source, repo_dir=self.temp_dir)
        article = Article.objects.get(slug='same-slug')
        self.assertEqual(article.title, 'Repo Version')
        self.assertEqual(article.source_repo, 'AI-Shipping-Labs/blog')


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
            f.write('is_free: true\n')
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
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        course = Course.objects.get(slug='stale-course')
        self.assertEqual(course.status, 'draft')


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
        rec_dir = os.path.join(self.temp_dir, 'recordings')
        os.makedirs(rec_dir)
        with open(os.path.join(rec_dir, 'my-workshop.yaml'), 'w') as f:
            f.write('title: "My Workshop"\n')
            f.write('slug: "my-workshop"\n')
            f.write('description: "A great workshop"\n')
            f.write('video_url: "https://youtube.com/watch?v=abc"\n')
            f.write('published_at: "2026-01-15"\n')
            f.write('tags:\n  - workshop\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)
        recording = Recording.objects.get(slug='my-workshop')
        self.assertEqual(recording.title, 'My Workshop')
        self.assertEqual(recording.source_repo, 'AI-Shipping-Labs/resources')

    def test_sync_curated_links(self):
        links_dir = os.path.join(self.temp_dir, 'curated-links')
        os.makedirs(links_dir)
        with open(os.path.join(links_dir, 'links.yaml'), 'w') as f:
            f.write('- item_id: "link-1"\n')
            f.write('  title: "Awesome Tool"\n')
            f.write('  url: "https://example.com"\n')
            f.write('  category: "tools"\n')
            f.write('- item_id: "link-2"\n')
            f.write('  title: "Cool Model"\n')
            f.write('  url: "https://example.com/model"\n')
            f.write('  category: "models"\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 2)
        self.assertTrue(CuratedLink.objects.filter(item_id='link-1').exists())

    def test_sync_downloads(self):
        dl_dir = os.path.join(self.temp_dir, 'downloads')
        os.makedirs(dl_dir)
        with open(os.path.join(dl_dir, 'cheatsheet.yaml'), 'w') as f:
            f.write('title: "Cheat Sheet"\n')
            f.write('slug: "cheatsheet"\n')
            f.write('file_url: "https://example.com/file.pdf"\n')
            f.write('file_type: "pdf"\n')
            f.write('file_size_bytes: 1024\n')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.items_created, 1)
        dl = Download.objects.get(slug='cheatsheet')
        self.assertEqual(dl.title, 'Cheat Sheet')
        self.assertEqual(dl.source_repo, 'AI-Shipping-Labs/resources')

    def test_sync_soft_deletes_stale_recordings(self):
        Recording.objects.create(
            title='Stale', slug='stale-rec', date=date.today(),
            source_repo='AI-Shipping-Labs/resources',
            published=True,
        )
        # Create an empty recordings directory so the sync runs the recordings path
        os.makedirs(os.path.join(self.temp_dir, 'recordings'))
        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        recording = Recording.objects.get(slug='stale-rec')
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

    def test_seeds_four_sources(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command('seed_content_sources', stdout=out)
        self.assertEqual(ContentSource.objects.count(), 4)

    def test_seed_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_content_sources', stdout=StringIO())
        call_command('seed_content_sources', stdout=StringIO())
        self.assertEqual(ContentSource.objects.count(), 4)

    def test_seed_creates_expected_repos(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_content_sources', stdout=StringIO())
        repos = set(ContentSource.objects.values_list('repo_name', flat=True))
        expected = {
            'AI-Shipping-Labs/blog',
            'AI-Shipping-Labs/courses',
            'AI-Shipping-Labs/resources',
            'AI-Shipping-Labs/projects',
        }
        self.assertEqual(repos, expected)

    def test_courses_repo_is_private(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_content_sources', stdout=StringIO())
        courses_source = ContentSource.objects.get(
            repo_name='AI-Shipping-Labs/courses',
        )
        self.assertTrue(courses_source.is_private)

    def test_blog_repo_is_public(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_content_sources', stdout=StringIO())
        blog_source = ContentSource.objects.get(
            repo_name='AI-Shipping-Labs/blog',
        )
        self.assertFalse(blog_source.is_private)

    def test_content_types_correct(self):
        from django.core.management import call_command
        from io import StringIO
        call_command('seed_content_sources', stdout=StringIO())
        self.assertEqual(
            ContentSource.objects.get(repo_name='AI-Shipping-Labs/blog').content_type,
            'article',
        )
        self.assertEqual(
            ContentSource.objects.get(repo_name='AI-Shipping-Labs/courses').content_type,
            'course',
        )
        self.assertEqual(
            ContentSource.objects.get(repo_name='AI-Shipping-Labs/resources').content_type,
            'resource',
        )
        self.assertEqual(
            ContentSource.objects.get(repo_name='AI-Shipping-Labs/projects').content_type,
            'project',
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

    def test_sync_overwrites_admin_edit(self):
        """When slug matches in repo, sync overwrites even if source_repo was null."""
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
                f.write('---\n')
                f.write('Content from repo.\n')

            sync_content_source(source, repo_dir=temp_dir)

            article = Article.objects.get(slug='overwrite-me')
            self.assertEqual(article.title, 'Repo Version')
            self.assertEqual(article.source_repo, 'test-org/blog')
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
