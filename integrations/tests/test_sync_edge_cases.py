"""Tests for GitHub content sync edge cases - issue #130.

Covers all 12 Django test scenarios from the spec:
- Slug collision between repo and Studio content
- Slug collision within same repo (two files, same slug)
- Parse failure does not soft-delete existing content
- S3 upload failure does not abort content sync
- Concurrent sync is skipped
- Stale lock is reclaimed after 10 minutes
- Webhook flood triggers at most two syncs
- Frontmatter missing required fields
- Missing required_level defaults to open
- Broken image reference is logged but content is published
- Unit rename migrates completion records
- Max files limit prevents runaway sync
"""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from content.models import (
    Article, Course, Module, Project, Recording, Unit, UserCourseProgress,
)
from integrations.models import ContentSource, SyncLog
from integrations.services.github import (
    _compute_content_hash,
    _validate_frontmatter,
    acquire_sync_lock,
    release_sync_lock,
    sync_content_source,
)

User = get_user_model()


class _ArticleSyncTestBase(TestCase):
    """Base class for article sync tests with helper methods."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
        )
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_article(self, filename, metadata, body):
        filepath = os.path.join(self.temp_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        # Auto-generate content_id if not provided
        if 'content_id' not in metadata:
            metadata = {**metadata, 'content_id': str(uuid.uuid4())}
        with open(filepath, 'w') as f:
            f.write('---\n')
            for key, value in metadata.items():
                if isinstance(value, list):
                    f.write(f'{key}:\n')
                    for item in value:
                        f.write(f'  - {item}\n')
                else:
                    f.write(f'{key}: "{value}"\n')
            f.write('---\n')
            f.write(body)
        return filepath


# ===========================================================================
# Scenario: Slug collision between repo and Studio content
# ===========================================================================


class SlugCollisionStudioTest(_ArticleSyncTestBase):
    """Test that Studio content is not overwritten by repo syncs."""

    def test_slug_collision_with_studio_content(self):
        """Given a Studio article with slug 'hello-world',
        when a sync runs with a repo file that has the same slug,
        then the Studio article is untouched and the sync log has an error."""
        Article.objects.create(
            title='Studio Article',
            slug='hello-world',
            date=date.today(),
            source_repo=None,
            published=True,
        )

        self._write_article('hello-world.md', {
            'title': 'Repo Article',
            'slug': 'hello-world',
            'date': '2026-01-15',
        }, 'Content from repo.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # Studio article should be untouched
        article = Article.objects.get(slug='hello-world')
        self.assertEqual(article.title, 'Studio Article')
        self.assertIsNone(article.source_repo)

        # Sync log should contain a collision error
        collision_errors = [
            e for e in sync_log.errors
            if 'Slug collision' in str(e.get('error', ''))
        ]
        self.assertTrue(len(collision_errors) > 0)
        self.assertIn('different source', collision_errors[0]['error'])


# ===========================================================================
# Scenario: Slug collision within same repo (two files, same slug)
# ===========================================================================


class SlugCollisionSameRepoTest(_ArticleSyncTestBase):
    """Test same-source slug collision (last-file-wins)."""

    def test_two_files_same_slug_last_wins(self):
        """Given two .md files both defining slug: duplicate-slug,
        then one article is created (last-file-wins)."""
        # Create two files in different subdirs with same slug
        os.makedirs(os.path.join(self.temp_dir, 'a'), exist_ok=True)
        os.makedirs(os.path.join(self.temp_dir, 'b'), exist_ok=True)

        self._write_article('a/article.md', {
            'title': 'First Version',
            'slug': 'duplicate-slug',
            'date': '2026-01-01',
        }, 'First body.')

        self._write_article('b/article.md', {
            'title': 'Second Version',
            'slug': 'duplicate-slug',
            'date': '2026-01-02',
        }, 'Second body.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # Only one article with this slug should exist for this source
        articles = Article.objects.filter(
            slug='duplicate-slug', source_repo='test-org/blog',
        )
        self.assertEqual(articles.count(), 1)


# ===========================================================================
# Scenario: Parse failure does not soft-delete existing content
# ===========================================================================


class ParseFailureNoDeleteTest(_ArticleSyncTestBase):
    """Test that parse failures do not cause soft-deletes of existing content."""

    def test_failed_parse_does_not_soft_delete(self):
        """Given 3 articles from a previous sync,
        when 1 of 3 files has invalid YAML frontmatter,
        then 0 articles are soft-deleted and the failed article retains content."""
        # Create 3 existing articles from this repo
        for i in range(1, 4):
            Article.objects.create(
                title=f'Article {i}',
                slug=f'article-{i}',
                date=date.today(),
                source_repo='test-org/blog',
                published=True,
            )

        # Write 2 valid articles and 1 with broken frontmatter
        self._write_article('article-1.md', {
            'title': 'Article 1 Updated',
            'slug': 'article-1',
            'date': '2026-01-01',
        }, 'Updated body 1.')

        self._write_article('article-2.md', {
            'title': 'Article 2 Updated',
            'slug': 'article-2',
            'date': '2026-01-01',
        }, 'Updated body 2.')

        # Write invalid file (broken YAML frontmatter)
        filepath = os.path.join(self.temp_dir, 'article-3.md')
        with open(filepath, 'w') as f:
            f.write('---\n')
            f.write('title: "Article 3"\n')
            f.write('slug: "article-3"\n')
            f.write('date: [[[invalid yaml\n')
            f.write('---\n')
            f.write('Body.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # 2 articles updated, 1 error, 0 soft-deleted
        self.assertEqual(sync_log.items_updated, 2)
        self.assertTrue(len(sync_log.errors) > 0)
        self.assertEqual(sync_log.items_deleted, 0)

        # The failed article should still be published
        article3 = Article.objects.get(slug='article-3')
        self.assertTrue(article3.published)


# ===========================================================================
# Scenario: S3 upload failure does not abort content sync
# ===========================================================================


class S3FailureNoAbortTest(_ArticleSyncTestBase):
    """Test that S3 upload failures do not abort content sync."""

    @override_settings(AWS_S3_CONTENT_BUCKET='test-bucket')
    @patch('integrations.services.github.upload_images_to_s3')
    def test_s3_error_does_not_abort_sync(self, mock_upload):
        """S3 upload error should not prevent content sync."""
        mock_upload.return_value = {
            'uploaded': 0,
            'skipped': 0,
            'errors': [{'file': 'images/broken.png', 'error': 'S3 timeout'}],
        }

        self._write_article('article.md', {
            'title': 'My Article',
            'slug': 'my-article',
            'date': '2026-01-01',
        }, 'Content.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # Content should be synced despite S3 error
        self.assertTrue(Article.objects.filter(slug='my-article').exists())
        self.assertEqual(sync_log.status, 'partial')
        # S3 error should be in the log
        s3_errors = [
            e for e in sync_log.errors
            if 'S3' in str(e.get('error', ''))
        ]
        self.assertTrue(len(s3_errors) > 0)


# ===========================================================================
# Scenario: Concurrent sync is skipped
# ===========================================================================


class ConcurrentSyncSkipTest(TestCase):
    """Test that concurrent syncs on the same source are prevented."""

    def test_locked_source_skips_sync(self):
        """Given a source with sync_locked_at set (recent),
        when a second sync tries to acquire the lock,
        then it fails."""
        source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
            sync_locked_at=timezone.now(),  # Recent lock
        )

        # Second lock should fail
        acquired = acquire_sync_lock(source)
        self.assertFalse(acquired)


# ===========================================================================
# Scenario: Stale lock is reclaimed after 10 minutes
# ===========================================================================


class StaleLockReclaimTest(TestCase):
    """Test that stale locks can be reclaimed."""

    def test_stale_lock_is_reclaimed(self):
        """Given a source with sync_locked_at set to 15 minutes ago,
        when a new sync tries to acquire the lock,
        then the lock is acquired."""
        source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
            sync_locked_at=timezone.now() - timedelta(minutes=15),
        )

        acquired = acquire_sync_lock(source)
        self.assertTrue(acquired)


# ===========================================================================
# Scenario: Webhook flood triggers at most two syncs
# ===========================================================================


class WebhookFloodTest(TestCase):
    """Test webhook dedup logic with sync_requested flag."""

    def test_sync_requested_flag_set_when_locked(self):
        """Given a source with sync_locked_at set (sync running),
        when 5 webhooks set sync_requested,
        then when the running sync completes, it returns True for follow-up."""
        source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
            sync_locked_at=timezone.now(),
        )

        # Simulate multiple webhooks setting the flag
        for _ in range(5):
            source.sync_requested = True
            source.save(update_fields=['sync_requested'])

        # When sync completes, release_sync_lock returns True
        follow_up = release_sync_lock(source)
        self.assertTrue(follow_up)

        # After release, the flag and lock are cleared
        source.refresh_from_db()
        self.assertFalse(source.sync_requested)
        self.assertIsNone(source.sync_locked_at)

    def test_no_follow_up_when_not_requested(self):
        """When no follow-up was requested, release returns False."""
        source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
            sync_locked_at=timezone.now(),
            sync_requested=False,
        )

        follow_up = release_sync_lock(source)
        self.assertFalse(follow_up)


# ===========================================================================
# Scenario: Frontmatter missing required fields
# ===========================================================================


class FrontmatterValidationTest(_ArticleSyncTestBase):
    """Test that missing required frontmatter fields cause file to be skipped."""

    def test_missing_title_skips_file(self):
        """Given a markdown file with no title,
        when article sync processes it,
        then the file is skipped and error is logged."""
        # Write a file without 'title' field
        filepath = os.path.join(self.temp_dir, 'notitle.md')
        with open(filepath, 'w') as f:
            f.write('---\n')
            f.write('slug: "notitle"\n')
            f.write('date: "2026-01-01"\n')
            f.write('---\n')
            f.write('Body text.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # Article should not be created
        self.assertFalse(Article.objects.filter(slug='notitle').exists())

        # Error should mention missing field
        missing_errors = [
            e for e in sync_log.errors
            if 'Missing required field' in str(e.get('error', ''))
        ]
        self.assertTrue(len(missing_errors) > 0)
        self.assertIn('title', missing_errors[0]['error'])

    def test_validate_frontmatter_helper(self):
        """Test the _validate_frontmatter helper directly."""
        # Missing title
        with self.assertRaises(ValueError) as ctx:
            _validate_frontmatter({'slug': 'test'}, 'article', 'test.md')
        self.assertIn('title', str(ctx.exception))

        # Title only is valid (slug derived from filename)
        _validate_frontmatter(
            {'title': 'Test'}, 'article', 'test.md',
        )

        # Title + slug is also valid
        _validate_frontmatter(
            {'title': 'Test', 'slug': 'test'}, 'article', 'test.md',
        )

    def test_slug_not_required_in_frontmatter(self):
        """Slug is derived from filename when missing from frontmatter.
        Articles, courses, recordings, projects, and downloads should
        not require slug in REQUIRED_FIELDS."""
        from integrations.services.github import REQUIRED_FIELDS
        for content_type in ['article', 'course', 'recording', 'project', 'download']:
            self.assertNotIn(
                'slug', REQUIRED_FIELDS.get(content_type, []),
                f'{content_type} should not require slug in frontmatter',
            )


# ===========================================================================
# Scenario: Slug derived from filename when not in frontmatter
# ===========================================================================


class SlugDerivedFromFilenameTest(_ArticleSyncTestBase):
    """Test that slug is derived from the filename stem when not in frontmatter."""

    def test_article_slug_from_filename(self):
        """Given a markdown file with title but no slug,
        then the article slug is derived from the filename stem."""
        self._write_article('my-great-article.md', {
            'title': 'My Great Article',
            'date': '2026-01-01',
        }, 'Body text.')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        article = Article.objects.get(slug='my-great-article')
        self.assertEqual(article.title, 'My Great Article')

    def test_explicit_slug_overrides_filename(self):
        """Given a markdown file with an explicit slug in frontmatter,
        then the explicit slug is used instead of the filename stem."""
        self._write_article('some-filename.md', {
            'title': 'My Article',
            'slug': 'custom-slug',
            'date': '2026-01-01',
        }, 'Body text.')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertTrue(Article.objects.filter(slug='custom-slug').exists())
        self.assertFalse(Article.objects.filter(slug='some-filename').exists())


# ===========================================================================
# Scenario: Missing required_level defaults to open
# ===========================================================================


class DefaultRequiredLevelTest(_ArticleSyncTestBase):
    """Test that missing required_level defaults to 0."""

    def test_missing_required_level_defaults_to_zero(self):
        """Given a markdown file with valid title and slug but no required_level,
        then Article is created with required_level=0."""
        self._write_article('article.md', {
            'title': 'Open Article',
            'slug': 'open-article',
            'date': '2026-01-01',
        }, 'Body.')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        article = Article.objects.get(slug='open-article')
        self.assertEqual(article.required_level, 0)


# ===========================================================================
# Scenario: Broken image reference is logged but content is published
# ===========================================================================


class BrokenImageReferenceTest(_ArticleSyncTestBase):
    """Test that broken image references are logged but content is published."""

    def test_broken_image_logged_content_published(self):
        """Given an article referencing a missing image,
        then the article is published and error is logged."""
        self._write_article('article.md', {
            'title': 'Article With Image',
            'slug': 'img-article',
            'date': '2026-01-01',
        }, '# Hello\n\n![diagram](images/missing.png)\n\nMore text.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        # Article should be published
        article = Article.objects.get(slug='img-article')
        self.assertTrue(article.published)

        # Broken image reference should be in errors
        img_errors = [
            e for e in sync_log.errors
            if 'Broken image reference' in str(e.get('error', ''))
        ]
        self.assertTrue(len(img_errors) > 0)
        self.assertIn('missing.png', img_errors[0]['error'])


# ===========================================================================
# Scenario: Unit rename migrates completion records
# ===========================================================================


class UnitRenameMigrationTest(TestCase):
    """Test that unit rename migrates UnitCompletion records."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/courses',
            content_type='course',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            email='student@example.com', password='pass123',
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rename_migrates_completion_records(self):
        """Given a unit at module-01/unit-01.md with completions,
        when the file is renamed to module-01/intro.md (same content),
        then completions are migrated to the new unit."""
        body_text = 'This is the unit content for testing rename detection.'
        content_hash = _compute_content_hash(body_text)

        # Create existing course structure
        course = Course.objects.create(
            title='Test Course', slug='test-course',
            source_repo='test-org/courses',
            status='published',
        )
        module = Module.objects.create(
            course=course, title='Module 1', sort_order=1,
            source_repo='test-org/courses',
            source_path='test-course/module-01',
        )
        old_unit = Unit.objects.create(
            module=module, title='Unit 1', sort_order=1,
            body=body_text,
            content_hash=content_hash,
            source_repo='test-org/courses',
            source_path='test-course/module-01/unit-01.md',
        )

        # Create a completion record for the old unit
        progress = UserCourseProgress.objects.create(
            user=self.user, unit=old_unit,
            completed_at=timezone.now(),
        )

        # Set up repo with renamed file (new path, same content)
        course_dir = os.path.join(self.temp_dir, 'test-course')
        module_dir = os.path.join(course_dir, 'module-01')
        os.makedirs(module_dir, exist_ok=True)

        # Course yaml
        course_cid = str(uuid.uuid4())
        with open(os.path.join(course_dir, 'course.yaml'), 'w') as f:
            f.write(f'title: Test Course\nslug: test-course\ncontent_id: {course_cid}\n')

        # Module yaml
        with open(os.path.join(module_dir, 'module.yaml'), 'w') as f:
            f.write('title: Module 1\nsort_order: 1\n')

        # Renamed unit file (same content, different filename)
        unit_cid = str(uuid.uuid4())
        with open(os.path.join(module_dir, 'intro.md'), 'w') as f:
            f.write('---\n')
            f.write('title: "Intro"\n')
            f.write('sort_order: "1"\n')
            f.write(f'content_id: "{unit_cid}"\n')
            f.write('---\n')
            f.write(body_text)

        sync_content_source(self.source, repo_dir=self.temp_dir)

        # Old unit should be deleted
        self.assertFalse(
            Unit.objects.filter(source_path='test-course/module-01/unit-01.md').exists(),
        )

        # New unit should exist
        new_unit = Unit.objects.get(
            source_path='test-course/module-01/intro.md',
        )
        self.assertEqual(new_unit.content_hash, content_hash)

        # Completion record should be migrated to new unit
        progress.refresh_from_db()
        self.assertEqual(progress.unit_id, new_unit.id)


# ===========================================================================
# Scenario: Max files limit prevents runaway sync
# ===========================================================================


class MaxFilesLimitTest(_ArticleSyncTestBase):
    """Test that max_files limit prevents runaway sync."""

    def test_max_files_aborts_sync(self):
        """Given a source with max_files=5 and a repo with 10 files,
        then sync aborts with an error."""
        self.source.max_files = 5
        self.source.save()

        # Create 10 article files
        for i in range(10):
            self._write_article(f'article-{i}.md', {
                'title': f'Article {i}',
                'slug': f'article-{i}',
                'date': '2026-01-01',
            }, f'Body {i}.')

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)

        self.assertEqual(sync_log.status, 'failed')
        self.assertTrue(
            any('more than 5 content files' in str(e.get('error', ''))
                for e in sync_log.errors),
        )
        # No articles should have been created
        self.assertEqual(
            Article.objects.filter(source_repo='test-org/blog').count(), 0,
        )


# ===========================================================================
# Model field tests
# ===========================================================================


class ContentSourceNewFieldsTest(TestCase):
    """Test new ContentSource model fields."""

    def test_new_fields_defaults(self):
        source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
        )
        self.assertIsNone(source.sync_locked_at)
        self.assertFalse(source.sync_requested)
        self.assertIsNone(source.last_webhook_at)
        self.assertEqual(source.max_files, 1000)

    def test_sync_locked_at_can_be_set(self):
        source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
        )
        now = timezone.now()
        source.sync_locked_at = now
        source.save()
        source.refresh_from_db()
        self.assertIsNotNone(source.sync_locked_at)


class UnitContentHashTest(TestCase):
    """Test Unit.content_hash field."""

    def test_content_hash_field(self):
        course = Course.objects.create(
            title='Course', slug='course', status='published',
        )
        module = Module.objects.create(
            course=course, title='Module', sort_order=1,
        )
        unit = Unit.objects.create(
            module=module, title='Unit', sort_order=1,
            body='test content',
            content_hash=_compute_content_hash('test content'),
        )
        self.assertEqual(len(unit.content_hash), 32)
        unit.refresh_from_db()
        self.assertEqual(unit.content_hash, _compute_content_hash('test content'))


# ===========================================================================
# Webhook handler edge case tests
# ===========================================================================


class WebhookDeduplicationTest(TestCase):
    """Test webhook handler dedup logic."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/blog',
            content_type='article',
            webhook_secret='test-secret',
        )

    def _make_signature(self, body):
        import hmac as hmac_mod
        sig = hmac_mod.new(
            b'test-secret', body, hashlib.sha256,
        ).hexdigest()
        return f'sha256={sig}'

    def test_webhook_updates_last_webhook_at(self):
        """last_webhook_at is updated on every webhook received."""
        payload = json.dumps({
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'test-org/blog'},
        }).encode()

        with patch('integrations.views.github_webhook.sync_content_source'):
            response = self.client.post(
                '/api/webhooks/github',
                data=payload,
                content_type='application/json',
                HTTP_X_GITHUB_EVENT='push',
                HTTP_X_HUB_SIGNATURE_256=self._make_signature(payload),
            )

        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertIsNotNone(self.source.last_webhook_at)

    def test_webhook_sets_sync_requested_when_locked(self):
        """If sync is running, webhook sets sync_requested flag."""
        self.source.sync_locked_at = timezone.now()
        self.source.save()

        payload = json.dumps({
            'ref': 'refs/heads/main',
            'repository': {'full_name': 'test-org/blog'},
        }).encode()

        with patch('integrations.views.github_webhook.sync_content_source') as mock_sync:
            response = self.client.post(
                '/api/webhooks/github',
                data=payload,
                content_type='application/json',
                HTTP_X_GITHUB_EVENT='push',
                HTTP_X_HUB_SIGNATURE_256=self._make_signature(payload),
            )

        self.assertEqual(response.status_code, 200)
        self.source.refresh_from_db()
        self.assertTrue(self.source.sync_requested)
        # sync_content_source should NOT have been called
        mock_sync.assert_not_called()


# ===========================================================================
# Clone timeout configuration test
# ===========================================================================


class CloneTimeoutConfigTest(TestCase):
    """Test that clone timeout is configurable."""

    @override_settings(GITHUB_SYNC_CLONE_TIMEOUT=60)
    @patch('integrations.services.github.subprocess.run')
    def test_clone_uses_configured_timeout(self, mock_run):
        from integrations.services.github import clone_or_pull_repo

        mock_run.return_value = MagicMock(returncode=0, stdout='abc123\n', stderr='')

        temp_dir = tempfile.mkdtemp()
        try:
            clone_or_pull_repo('test-org/blog', temp_dir)
            # First call is the clone, check timeout
            call_args = mock_run.call_args_list[0]
            self.assertEqual(call_args.kwargs.get('timeout'), 60)
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


# ===========================================================================
# Scenario: Project sync renders markdown to HTML
# ===========================================================================


class ProjectSyncMarkdownRenderingTest(TestCase):
    """Test that synced projects have content_html rendered from markdown."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='test-org/content',
            content_type='project',
            content_path='projects',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.projects_dir = os.path.join(self.temp_dir, 'projects')
        os.makedirs(self.projects_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_project(self, filename, metadata, body):
        filepath = os.path.join(self.projects_dir, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if 'content_id' not in metadata:
            metadata = {**metadata, 'content_id': str(uuid.uuid4())}
        with open(filepath, 'w') as f:
            f.write('---\n')
            for key, value in metadata.items():
                if isinstance(value, list):
                    f.write(f'{key}:\n')
                    for item in value:
                        f.write(f'  - {item}\n')
                else:
                    f.write(f'{key}: "{value}"\n')
            f.write('---\n')
            f.write(body)

    def test_synced_project_has_rendered_html(self):
        """Given a project markdown file with images and formatting,
        when synced, then content_html contains rendered HTML."""
        self._write_project('test-project.md', {
            'title': 'Test Project',
            'description': 'A test project',
        }, '# Overview\n\n![diagram](https://cdn.example.com/img.png)\n\nSome **bold** text.')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        project = Project.objects.get(slug='test-project')
        self.assertIn('<h1>Overview</h1>', project.content_html)
        self.assertIn('<img', project.content_html)
        self.assertIn('<strong>bold</strong>', project.content_html)

    def test_synced_project_with_code_block(self):
        """Given a project with a code block in markdown,
        when synced, then content_html contains code element."""
        self._write_project('code-project.md', {
            'title': 'Code Project',
            'description': 'Has code',
        }, '```python\nprint("hello")\n```')

        sync_content_source(self.source, repo_dir=self.temp_dir)

        project = Project.objects.get(slug='code-project')
        self.assertIn('<code', project.content_html)
        self.assertIn('print', project.content_html)
