"""Course project settings stay connected to source-managed course YAML."""

from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from content.models import Cohort, Course, Module
from content.models.peer_review import (
    CourseCertificate,
    CourseProject,
    PeerReview,
    ProjectSubmission,
)
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
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', external_key='self-paced',
            mode='self_paced',
        )
        cls.dated = Cohort.objects.create(
            course=cls.course, name='Dated', external_key='dated',
            start_date='2026-10-01', end_date='2026-12-01',
        )

    def _self_paced_entry(self, **overrides):
        entry = {
            'slug': 'self-paced-attempt',
            'title': 'Self-paced attempt',
            'module_path': 'capstone',
            'cohort_key': 'self-paced',
        }
        entry.update(overrides)
        return entry

    def _dated_entry(self, **overrides):
        entry = {
            'slug': 'dated-attempt',
            'title': 'Dated attempt',
            'module_path': 'capstone',
            'cohort_key': 'dated',
        }
        entry.update(overrides)
        return entry

    def _project_learner_graph(self, project):
        learner = get_user_model().objects.create_user(
            email='project-1976@example.com',
        )
        submission = ProjectSubmission.objects.create(
            user=learner, course=self.course, cohort=self.self_paced,
            course_project=project, project_url='https://example.com/project',
        )
        review = PeerReview.objects.create(
            submission=submission, reviewer=learner, is_complete=True,
        )
        certificate = CourseCertificate.objects.create(
            user=learner, course=self.course, submission=submission,
        )
        return learner, submission, review, certificate

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

    def test_self_paced_attempt_omits_dates_and_resyncs_in_place(self):
        entry = self._self_paced_entry()

        _sync_course_projects(self.course, {'projects': [entry]}, 'buildcamp')
        project = CourseProject.objects.get(slug='self-paced-attempt')
        original_pk = project.pk
        self.assertIsNone(project.submission_due_at)
        self.assertIsNone(project.review_due_at)
        self.assertTrue(project.is_submission_open(timezone.now()))
        self.assertTrue(project.is_review_open(timezone.now()))

        _sync_course_projects(self.course, {'projects': [entry]}, 'buildcamp')
        self.assertEqual(CourseProject.objects.get(slug=project.slug).pk, original_pk)

    def test_self_paced_date_cleanup_preserves_learner_graph(self):
        due = timezone.now()
        project = CourseProject.objects.create(
            course=self.course, cohort=self.self_paced, module=self.module,
            slug='self-paced-attempt', title='Copied dates',
            submission_due_at=due, review_due_at=due + timedelta(days=7),
        )
        learner, submission, review, certificate = self._project_learner_graph(
            project,
        )

        _sync_course_projects(
            self.course, {'projects': [self._self_paced_entry()]}, 'buildcamp',
        )

        project.refresh_from_db()
        submission.refresh_from_db()
        review.refresh_from_db()
        certificate.refresh_from_db()
        self.assertIsNone(project.submission_due_at)
        self.assertIsNone(project.review_due_at)
        self.assertEqual(submission.course_project_id, project.pk)
        self.assertEqual(review.submission_id, submission.pk)
        self.assertEqual(certificate.submission_id, submission.pk)
        self.assertEqual(submission.user_id, learner.pk)

    def test_self_paced_rejects_partial_or_fixed_dates_before_any_write(self):
        invalid_entries = [
            self._self_paced_entry(submission_due_at='2026-11-21T23:59:00+01:00'),
            self._self_paced_entry(
                submission_due_at='2026-11-21T23:59:00+01:00',
                review_due_at='2026-11-28T23:59:00+01:00',
            ),
        ]
        for entry in invalid_entries:
            with self.subTest(entry=entry):
                with self.assertRaisesMessage(GitHubSyncError, 'Self-paced project'):
                    _sync_course_projects(
                        self.course, {'projects': [entry]}, 'buildcamp',
                    )
                self.assertFalse(CourseProject.objects.filter(course=self.course).exists())

    def test_unknown_cohort_does_not_allow_omitted_dates(self):
        entry = self._self_paced_entry(cohort_key='missing')

        with self.assertRaisesMessage(GitHubSyncError, 'unknown cohort_key'):
            _sync_course_projects(self.course, {'projects': [entry]}, 'buildcamp')

        self.assertFalse(CourseProject.objects.filter(course=self.course).exists())

    def test_dated_and_unscoped_attempts_still_require_both_dates(self):
        for cohort_key in ('dated', None):
            entry = {
                'slug': f'dated-{cohort_key or "unscoped"}',
                'title': 'Dated attempt',
                'module_path': 'capstone',
            }
            if cohort_key is not None:
                entry['cohort_key'] = cohort_key
            with self.subTest(cohort_key=cohort_key):
                with self.assertRaisesMessage(GitHubSyncError, 'timezone-aware'):
                    _sync_course_projects(
                        self.course, {'projects': [entry]}, 'buildcamp',
                    )

    def test_dated_attempt_rejects_each_invalid_deadline_pair(self):
        submission = '2026-11-21T23:59:00+01:00'
        review = '2026-11-28T23:59:00+01:00'
        invalid_pairs = [
            {'review_due_at': review},
            {'submission_due_at': submission},
            {'submission_due_at': submission, 'review_due_at': '2026-11-28T23:59:00'},
            {'submission_due_at': submission, 'review_due_at': submission},
            {'submission_due_at': review, 'review_due_at': submission},
        ]
        for pair in invalid_pairs:
            with self.subTest(pair=pair), self.assertRaises(GitHubSyncError):
                _sync_course_projects(
                    self.course,
                    {'projects': [self._dated_entry(**pair)]},
                    'buildcamp',
                )
            self.assertFalse(CourseProject.objects.filter(course=self.course).exists())

    def test_model_rejects_invalid_date_pairs_and_non_self_paced_nulls(self):
        self_paced = CourseProject(
            course=self.course, cohort=self.self_paced, module=self.module,
            slug='model-self-paced', title='Self-paced',
        )
        self_paced.full_clean()
        self_paced.submission_due_at = timezone.now()
        self_paced.review_due_at = timezone.now() + timedelta(days=7)
        with self.assertRaises(ValidationError):
            self_paced.full_clean()
        malformed = CourseProject(
            course=self.course, cohort=self.dated, module=self.module,
            slug='model-dated', title='Dated',
        )
        with self.assertRaises(ValidationError):
            malformed.full_clean()
        self.assertFalse(malformed.is_submission_open(timezone.now()))
        self.assertFalse(malformed.is_review_open(timezone.now()))
