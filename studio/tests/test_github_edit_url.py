"""Tests for get_github_edit_url with content_path prefix lookup.

Verifies that the GitHub edit URL correctly prepends the ContentSource
content_path to the object's source_path, producing valid GitHub URLs.
"""

from django.test import TestCase
from django.utils import timezone

from content.models import Article, Course, Module, Project, Unit
from events.models import Event
from integrations.models import ContentSource
from studio.utils import _get_content_path, get_github_edit_url


class GitHubEditUrlWithContentPathTest(TestCase):
    """Test get_github_edit_url prepends content_path from ContentSource."""

    @classmethod
    def setUpTestData(cls):
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
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='event',
            content_path='events',
        )
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='project',
            content_path='projects',
        )

        cls.article = Article.objects.create(
            title='Test Article', slug='test-art',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='test-art.md',
        )
        cls.course = Course.objects.create(
            title='Test Course', slug='test-course', status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='test-course',
        )
        cls.event = Event.objects.create(
            title='Test Event', slug='test-evt',
            start_datetime=timezone.now(),
            source_repo='AI-Shipping-Labs/content',
            source_path='test-evt.yaml',
        )
        cls.project = Project.objects.create(
            title='Test Project', slug='test-proj',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='test-proj.md',
        )

    def test_article_url_includes_blog_prefix(self):
        url = get_github_edit_url(self.article)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/blog/test-art.md',
        )

    def test_course_url_includes_courses_prefix(self):
        url = get_github_edit_url(self.course)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/courses/test-course',
        )

    def test_event_url_includes_events_prefix(self):
        url = get_github_edit_url(self.event)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/events/test-evt.yaml',
        )

    def test_project_url_includes_projects_prefix(self):
        url = get_github_edit_url(self.project)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/projects/test-proj.md',
        )


class GitHubEditUrlFallbackTest(TestCase):
    """Test get_github_edit_url when ContentSource is missing."""

    @classmethod
    def setUpTestData(cls):
        # No ContentSource created -- simulates missing config
        cls.article = Article.objects.create(
            title='Orphan', slug='orphan',
            date=timezone.now().date(),
            source_repo='SomeOrg/some-repo',
            source_path='orphan.md',
        )

    def test_url_uses_source_path_as_is_when_no_content_source(self):
        url = get_github_edit_url(self.article)
        self.assertEqual(
            url,
            'https://github.com/SomeOrg/some-repo/blob/main/orphan.md',
        )


class GitHubEditUrlEmptyContentPathTest(TestCase):
    """Test get_github_edit_url when ContentSource has empty content_path."""

    @classmethod
    def setUpTestData(cls):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='',
        )
        cls.article = Article.objects.create(
            title='Root Article', slug='root-art',
            date=timezone.now().date(),
            source_repo='AI-Shipping-Labs/content',
            source_path='root-art.md',
        )

    def test_url_has_no_extra_slash_when_content_path_empty(self):
        url = get_github_edit_url(self.article)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/root-art.md',
        )


class GitHubEditUrlNonSyncedTest(TestCase):
    """Test get_github_edit_url returns None for non-synced objects."""

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


class ContentPathLookupTest(TestCase):
    """Test _get_content_path helper directly."""

    @classmethod
    def setUpTestData(cls):
        ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='course',
            content_path='courses',
        )
        cls.course = Course.objects.create(
            title='C1', slug='c1', status='draft',
            source_repo='AI-Shipping-Labs/content',
            source_path='c1',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='M1', sort_order=0,
            source_repo='AI-Shipping-Labs/content',
            source_path='c1/m1',
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='U1', sort_order=0,
            source_repo='AI-Shipping-Labs/content',
            source_path='c1/m1/u1.md',
        )

    def test_module_maps_to_course_content_type(self):
        self.assertEqual(_get_content_path(self.module), 'courses')

    def test_unit_maps_to_course_content_type(self):
        self.assertEqual(_get_content_path(self.unit), 'courses')

    def test_module_github_url_includes_courses_prefix(self):
        url = get_github_edit_url(self.module)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/courses/c1/m1',
        )

    def test_unit_github_url_includes_courses_prefix(self):
        url = get_github_edit_url(self.unit)
        self.assertEqual(
            url,
            'https://github.com/AI-Shipping-Labs/content/blob/main/courses/c1/m1/u1.md',
        )
