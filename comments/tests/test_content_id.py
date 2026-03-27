"""Tests for content_id UUID infrastructure (Part A)."""
import os
import tempfile
import uuid

from django.test import TestCase

from content.models import Article, Course, Unit, Module, Project, Download, Tutorial
from events.models import Event


class ContentIdFieldExistsTest(TestCase):
    """Verify content_id field exists on all required models."""

    def test_unit_has_content_id(self):
        field = Unit._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_article_has_content_id(self):
        field = Article._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_course_has_content_id(self):
        field = Course._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_event_has_content_id(self):
        field = Event._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_tutorial_has_content_id(self):
        field = Tutorial._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_project_has_content_id(self):
        field = Project._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_download_has_content_id(self):
        field = Download._meta.get_field('content_id')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)


class AssignContentIdsScriptTest(TestCase):
    """Test the scripts/assign_content_ids.py logic."""

    def test_assigns_ids_to_files_missing_them(self):
        import frontmatter as fm
        from scripts.assign_content_ids import assign_content_ids

        with tempfile.TemporaryDirectory() as tmpdir:
            # File without content_id
            post1 = fm.Post('Body text', title='No ID', slug='no-id')
            with open(os.path.join(tmpdir, 'no-id.md'), 'wb') as f:
                fm.dump(post1, f)

            # File with content_id
            existing_id = str(uuid.uuid4())
            post2 = fm.Post('Body', title='Has ID', slug='has-id', content_id=existing_id)
            with open(os.path.join(tmpdir, 'has-id.md'), 'wb') as f:
                fm.dump(post2, f)

            assigned, already_had = assign_content_ids(tmpdir)

            self.assertEqual(assigned, 1)
            self.assertEqual(already_had, 1)

            # Verify the file now has a content_id
            updated = fm.load(os.path.join(tmpdir, 'no-id.md'))
            self.assertIsNotNone(updated.get('content_id'))
            # Verify UUID is valid
            uuid.UUID(updated['content_id'])

            # Verify existing content_id was preserved
            preserved = fm.load(os.path.join(tmpdir, 'has-id.md'))
            self.assertEqual(preserved['content_id'], existing_id)


class SyncPipelineContentIdTest(TestCase):
    """Test that sync pipeline skips content without content_id."""

    def test_sync_articles_skips_without_content_id(self):
        """Articles without content_id in frontmatter are skipped."""
        import frontmatter as fm
        from integrations.models import ContentSource, SyncLog
        from integrations.services.github import _sync_articles

        source = ContentSource.objects.create(
            content_type='article',
            repo_name='test/repo',
            content_path='blog',
        )
        sync_log = SyncLog.objects.create(source=source)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Article without content_id
            post = fm.Post('Body', title='No ID', slug='no-id')
            with open(os.path.join(tmpdir, 'no-id.md'), 'wb') as f:
                fm.dump(post, f)

            stats = _sync_articles(source, tmpdir, 'abc123', sync_log)

        self.assertEqual(Article.objects.filter(slug='no-id').count(), 0)
        self.assertTrue(
            any('missing content_id' in e['error'] for e in stats['errors']),
            f"Expected 'missing content_id' error, got: {stats['errors']}",
        )

    def test_sync_articles_stores_content_id(self):
        """Articles with content_id in frontmatter store it on the model."""
        import frontmatter as fm
        from integrations.models import ContentSource, SyncLog
        from integrations.services.github import _sync_articles

        source = ContentSource.objects.create(
            content_type='article',
            repo_name='test/repo2',
            content_path='blog',
        )
        sync_log = SyncLog.objects.create(source=source)
        test_uuid = str(uuid.uuid4())

        with tempfile.TemporaryDirectory() as tmpdir:
            post = fm.Post('Body', title='With ID', slug='with-id', content_id=test_uuid)
            with open(os.path.join(tmpdir, 'with-id.md'), 'wb') as f:
                fm.dump(post, f)

            _sync_articles(source, tmpdir, 'abc123', sync_log)

        article = Article.objects.get(slug='with-id')
        self.assertEqual(str(article.content_id), test_uuid)
