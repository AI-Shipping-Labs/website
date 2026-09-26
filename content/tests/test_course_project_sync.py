"""Course project settings stay connected to source-managed course YAML."""

from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from content.models import Course, Module
from content.models.peer_review import CourseProject
from content.sync_parsers.common import GitHubSyncError
from content.sync_parsers.families.courses import _build_course_defaults, _sync_course_projects


class CourseProjectSyncTest(SimpleTestCase):
    def defaults(self, **overrides):
        data = {'title': 'Course', 'description': 'A course', **overrides}
        return _build_course_defaults(
            data, 'course', 'f876ab29-1f46-45f2-8284-ab86182b76aa',
            '/nonexistent/course', 'course', SimpleNamespace(repo_name='content'),
            'a' * 40, [],
        )

    def test_peer_review_settings_are_imported(self):
        defaults = self.defaults(
            peer_review_enabled=True,
            peer_review_count=3,
            peer_review_deadline_days=7,
            peer_review_criteria='Review the problem and tests.',
        )
        self.assertTrue(defaults['peer_review_enabled'])
        self.assertEqual(defaults['peer_review_count'], 3)
        self.assertEqual(defaults['peer_review_deadline_days'], 7)
        self.assertEqual(defaults['peer_review_criteria'], 'Review the problem and tests.')

    def test_invalid_review_count_rejects_course(self):
        with self.assertRaises(GitHubSyncError):
            self.defaults(peer_review_count=0)


class CourseProjectAttemptSyncTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='sync-attempts', status='published',
            peer_review_enabled=True,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Capstone', slug='capstone', sort_order=7,
        )
        cls.project_module = Module.objects.create(
            course=cls.course, parent=cls.module, title='Project', slug='project',
            sort_order=1,
        )

    def test_imports_two_independent_attempt_deadlines_and_updates_in_place(self):
        entries = [
            {
                'slug': f'attempt-{number}', 'title': f'Attempt {number}',
                'module_path': 'capstone',
                'submission_due_at': f'2026-11-{number + 20:02d}T23:59:00+01:00',
                'review_due_at': f'2026-11-{number + 22:02d}T23:59:00+01:00',
            }
            for number in (1, 2)
        ]
        _sync_course_projects(self.course, {'projects': entries}, 'buildcamp')
        self.assertEqual(CourseProject.objects.filter(course=self.course).count(), 2)
        first = CourseProject.objects.get(course=self.course, slug='attempt-1')
        self.assertTrue(timezone.is_aware(first.submission_due_at))
        self.assertEqual(first.module_id, self.module.pk)
        entries[0]['title'] = 'Updated first attempt'
        _sync_course_projects(self.course, {'projects': entries}, 'buildcamp')
        self.assertEqual(CourseProject.objects.filter(course=self.course).count(), 2)
        first.refresh_from_db()
        self.assertEqual(first.title, 'Updated first attempt')

    def test_rejects_naive_deadline_before_writing_any_attempt(self):
        with self.assertRaisesMessage(GitHubSyncError, 'timezone-aware'):
            _sync_course_projects(self.course, {'projects': [
                {
                    'slug': 'valid', 'title': 'Valid',
                    'module_path': 'capstone',
                    'submission_due_at': '2026-11-21T23:59:00+01:00',
                    'review_due_at': '2026-11-22T23:59:00+01:00',
                },
                {
                    'slug': 'invalid', 'title': 'Invalid',
                    'module_path': 'capstone',
                    'submission_due_at': '2026-11-21T23:59:00',
                    'review_due_at': '2026-11-22T23:59:00+01:00',
                },
            ]}, 'buildcamp')
        self.assertFalse(CourseProject.objects.filter(course=self.course).exists())

    def test_rejects_unroutable_slug(self):
        with self.assertRaisesMessage(GitHubSyncError, 'invalid slug'):
            _sync_course_projects(self.course, {'projects': [{
                'slug': 'attempt/one', 'title': 'Attempt one',
                'module_path': 'capstone',
                'submission_due_at': '2030-11-21T23:59:00+01:00',
                'review_due_at': '2030-11-28T23:59:00+01:00',
            }]}, 'buildcamp')
        self.assertFalse(CourseProject.objects.filter(course=self.course).exists())

    def test_rejects_project_paths_nested_under_topics(self):
        with self.assertRaises(GitHubSyncError):
            _sync_course_projects(self.course, {'projects': [{
                'slug': 'nested-attempt', 'title': 'Nested attempt',
                'module_path': 'capstone/project',
                'submission_due_at': '2030-11-21T23:59:00+01:00',
                'review_due_at': '2030-11-28T23:59:00+01:00',
            }]}, 'buildcamp')
        self.assertFalse(CourseProject.objects.filter(course=self.course).exists())
