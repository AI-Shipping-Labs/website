"""Tests for ``get_github_edit_url``.

Issue #310: with ``ContentSource.content_path`` removed, the model's
``source_path`` already carries the full repo-relative path. The
``get_github_edit_url`` helper just concatenates
``https://github.com/<repo>/blob/main/<source_path>``.
"""

from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course, Module, Project, Unit
from events.models import Event
from studio.utils import get_github_edit_url, is_synced


class GitHubEditUrlPrefixedSourcePathTest(TestCase):
    """``source_path`` is now repo-relative; the URL is the simple concat."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Test Article', slug='test-art',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='blog/test-art.md',
        )
        cls.course = Course.objects.create(
            title='Test Course', slug='test-course', status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/test-course',
        )
        cls.event = Event.objects.create(
            title='Test Event', slug='test-evt',
            start_datetime=timezone.now(),
            origin='github',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/test-evt.yaml',
        )
        cls.project = Project.objects.create(
            title='Test Project', slug='test-proj',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='projects/test-proj.md',
        )

    def test_article_url(self):
        url = get_github_edit_url(self.article)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/blog/test-art.md',
        )

    def test_course_url(self):
        url = get_github_edit_url(self.course)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/courses/test-course',
        )

    def test_event_url(self):
        url = get_github_edit_url(self.event)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/events/test-evt.yaml',
        )

    def test_project_url(self):
        url = get_github_edit_url(self.project)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/projects/test-proj.md',
        )


class GitHubEditUrlNonSyncedTest(TestCase):
    """Returns None for objects without ``source_repo`` / ``source_path``."""

    @classmethod
    def setUpTestData(cls):
        cls.manual = Article.objects.create(
            title='Manual', slug='manual',
            date=timezone.now().date(),
        )
        cls.no_path = Article.objects.create(
            title='No Path', slug='no-path',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path=None,
        )

    def test_returns_none_for_manual_item(self):
        self.assertIsNone(get_github_edit_url(self.manual))

    def test_returns_none_when_source_path_is_none(self):
        self.assertIsNone(get_github_edit_url(self.no_path))


class CourseHierarchyTest(TestCase):
    """Module and Unit URLs use their ``source_path`` (already prefixed)."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='C1', slug='c1', status='draft',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/c1',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='M1', sort_order=0,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/c1/m1',
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='U1', sort_order=0,
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/c1/m1/u1.md',
        )

    def test_module_url(self):
        url = get_github_edit_url(self.module)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/courses/c1/m1',
        )

    def test_unit_url(self):
        url = get_github_edit_url(self.unit)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/courses/c1/m1/u1.md',
        )


class IsSyncedTest(TestCase):
    """``is_synced`` simply checks ``source_repo``."""

    def test_synced_object(self):
        article = Article.objects.create(
            title='X', slug='x',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
        )
        self.assertTrue(is_synced(article))

    def test_unsynced_object(self):
        article = Article.objects.create(
            title='Y', slug='y',
            date=timezone.now().date(),
        )
        self.assertFalse(is_synced(article))
