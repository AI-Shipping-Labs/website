"""Course project settings stay connected to source-managed course YAML."""

from types import SimpleNamespace

from django.test import SimpleTestCase

from content.sync_parsers.common import GitHubSyncError
from content.sync_parsers.families.courses import _build_course_defaults


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
