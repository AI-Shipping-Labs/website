"""Tests for the watch_content management command helpers.

Tests cover path mapping, file filtering, and debounce logic.
"""

import threading
from unittest.mock import MagicMock, patch

from django.test import TestCase

from integrations.management.commands.watch_content import (
    TIERS_SENTINEL,
    DebouncedSyncer,
    _build_path_mapping,
    _get_content_type_for_path,
    _is_content_file,
)
from integrations.models import ContentSource


class PathMappingTest(TestCase):
    """Test _get_content_type_for_path maps file paths to the correct ContentSource."""

    @classmethod
    def setUpTestData(cls):
        cls.article_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog',
        )
        cls.course_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses',
        )
        cls.resource_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='resource',
            content_path='resources',
        )
        cls.project_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects',
        )
        cls.iq_source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='interview_question',
            content_path='interview-questions',
        )
        cls.path_map = _build_path_mapping(ContentSource.objects.all())

    def test_blog_article_maps_to_article_source(self):
        result = _get_content_type_for_path('blog/my-article.md', self.path_map)
        self.assertEqual(result, self.article_source)

    def test_nested_course_maps_to_course_source(self):
        result = _get_content_type_for_path(
            'courses/ai-hero/01-module/01-unit.md', self.path_map,
        )
        self.assertEqual(result, self.course_source)

    def test_resources_recording_maps_to_resource_source(self):
        result = _get_content_type_for_path(
            'resources/recordings/some-recording.md', self.path_map,
        )
        self.assertEqual(result, self.resource_source)

    def test_project_maps_to_project_source(self):
        result = _get_content_type_for_path(
            'projects/my-project.md', self.path_map,
        )
        self.assertEqual(result, self.project_source)

    def test_interview_questions_maps_correctly(self):
        result = _get_content_type_for_path(
            'interview-questions/q1.yaml', self.path_map,
        )
        self.assertEqual(result, self.iq_source)

    def test_tiers_yaml_returns_sentinel(self):
        result = _get_content_type_for_path('tiers.yaml', self.path_map)
        self.assertEqual(result, TIERS_SENTINEL)

    def test_unknown_path_returns_none(self):
        result = _get_content_type_for_path(
            'random/unknown-file.md', self.path_map,
        )
        self.assertIsNone(result)

    def test_git_config_returns_none(self):
        result = _get_content_type_for_path('.git/config', self.path_map)
        self.assertIsNone(result)

    def test_root_file_not_tiers_returns_none(self):
        result = _get_content_type_for_path('README.md', self.path_map)
        self.assertIsNone(result)


class FileFilteringTest(TestCase):
    """Test _is_content_file filters based on extension and path patterns."""

    def test_markdown_file_passes(self):
        self.assertTrue(_is_content_file('blog/article.md'))

    def test_yaml_file_passes(self):
        self.assertTrue(_is_content_file('resources/links.yaml'))

    def test_yml_file_passes(self):
        self.assertTrue(_is_content_file('resources/links.yml'))

    def test_png_file_rejected(self):
        self.assertFalse(_is_content_file('blog/image.png'))

    def test_jpg_file_rejected(self):
        self.assertFalse(_is_content_file('blog/photo.jpg'))

    def test_python_file_rejected(self):
        self.assertFalse(_is_content_file('scripts/sync.py'))

    def test_swp_file_rejected(self):
        self.assertFalse(_is_content_file('blog/.article.md.swp'))

    def test_tilde_backup_rejected(self):
        self.assertFalse(_is_content_file('blog/article.md~'))

    def test_tmp_file_rejected(self):
        self.assertFalse(_is_content_file('blog/article.tmp'))

    def test_git_directory_rejected(self):
        self.assertFalse(_is_content_file('.git/config'))

    def test_nested_git_rejected(self):
        self.assertFalse(_is_content_file('.git/refs/heads/main'))

    def test_pycache_rejected(self):
        self.assertFalse(_is_content_file('__pycache__/module.md'))

    def test_dotfile_rejected(self):
        self.assertFalse(_is_content_file('.hidden/file.md'))

    def test_root_yaml_passes(self):
        self.assertTrue(_is_content_file('tiers.yaml'))


class BuildPathMappingTest(TestCase):
    """Test _build_path_mapping builds correct mapping from ContentSource queryset."""

    @classmethod
    def setUpTestData(cls):
        cls.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog',
        )

    def test_mapping_uses_content_path_as_key(self):
        mapping = _build_path_mapping(ContentSource.objects.all())
        self.assertIn('blog', mapping)
        self.assertEqual(mapping['blog'], self.source)

    def test_empty_content_path_excluded(self):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/other',
            content_type='course',
            content_path='',
        )
        mapping = _build_path_mapping(ContentSource.objects.all())
        self.assertNotIn('', mapping)


class DebounceTest(TestCase):
    """Test DebouncedSyncer debounce behavior."""

    def setUp(self):
        self.stdout = MagicMock()
        self.stderr = MagicMock()
        self.style = MagicMock()
        self.style.NOTICE = lambda x: x
        self.style.SUCCESS = lambda x: x

    @patch(
        'integrations.management.commands.watch_content.sync_content_source'
    )
    def test_two_events_same_type_single_sync(self, mock_sync):
        """Two rapid events for the same content type produce one sync call."""
        mock_sync.return_value = MagicMock(
            items_created=1, items_updated=0, items_deleted=0,
        )
        syncer = DebouncedSyncer(
            debounce_seconds=0.1,
            repo_dir='/tmp/test',
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
        )

        source = MagicMock(spec=ContentSource)
        source.content_type = 'article'

        # Schedule two events rapidly
        syncer.schedule('article', source)
        syncer.schedule('article', source)

        # Wait for debounce to fire
        done = threading.Event()
        original = syncer._execute_sync

        def patched_execute(key):
            original(key)
            done.set()

        syncer._execute_sync = patched_execute
        # Re-schedule to use the patched version
        syncer.schedule('article', source)
        done.wait(timeout=2)

        self.assertEqual(mock_sync.call_count, 1)
        syncer.cancel_all()

    @patch(
        'integrations.management.commands.watch_content.sync_content_source'
    )
    def test_different_types_separate_syncs(self, mock_sync):
        """Events for different content types trigger separate syncs."""
        mock_sync.return_value = MagicMock(
            items_created=0, items_updated=1, items_deleted=0,
        )

        syncer = DebouncedSyncer(
            debounce_seconds=0.1,
            repo_dir='/tmp/test',
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
        )

        article_source = MagicMock(spec=ContentSource)
        article_source.content_type = 'article'

        course_source = MagicMock(spec=ContentSource)
        course_source.content_type = 'course'

        done_count = threading.Event()
        call_count = [0]

        original = syncer._execute_sync

        def patched_execute(key):
            original(key)
            call_count[0] += 1
            if call_count[0] >= 2:
                done_count.set()

        syncer._execute_sync = patched_execute

        syncer.schedule('article', article_source)
        syncer.schedule('course', course_source)

        done_count.wait(timeout=2)
        self.assertEqual(mock_sync.call_count, 2)
        syncer.cancel_all()

    def test_cancel_all_prevents_sync(self):
        """Cancelling all timers prevents pending syncs from executing."""
        syncer = DebouncedSyncer(
            debounce_seconds=10,
            repo_dir='/tmp/test',
            stdout=self.stdout,
            stderr=self.stderr,
            style=self.style,
        )

        source = MagicMock(spec=ContentSource)
        source.content_type = 'article'

        syncer.schedule('article', source)
        syncer.cancel_all()

        # Verify internal state is cleared
        self.assertEqual(len(syncer._timers), 0)
        self.assertEqual(len(syncer._pending), 0)
