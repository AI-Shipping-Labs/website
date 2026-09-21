"""Learner flows for separate dated course project attempts."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course, Module, PeerReview, ProjectSubmission
from content.models.cohort import CohortEnrollment
from content.models.peer_review import CourseProject


class CourseProjectAttemptViewsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(email='learner@example.com', password='pw')
        cls.course = Course.objects.create(
            title='Buildcamp', slug='attempt-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=2,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Capstone project', slug='capstone-project',
            sort_order=7,
        )
        now = timezone.now()
        cls.first = CourseProject.objects.create(
            course=cls.course, module=cls.module, slug='first', title='First attempt',
            submission_due_at=now + timedelta(days=1),
            review_due_at=now + timedelta(days=8),
        )
        cls.second = CourseProject.objects.create(
            course=cls.course, module=cls.module, slug='second', title='Second attempt',
            submission_due_at=now + timedelta(days=15),
            review_due_at=now + timedelta(days=22),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_course_curriculum_lists_both_attempts_with_distinct_links(self):
        response = self.client.get('/courses/attempt-course')
        self.assertContains(response, 'First attempt')
        self.assertContains(response, 'Second attempt')
        self.assertContains(response, '/courses/attempt-course/projects/first/submit')
        self.assertContains(response, '/courses/attempt-course/projects/second/submit')
        self.assertContains(response, 'data-testid="syllabus-project-group"')
        self.assertNotContains(response, 'data-testid="course-project"')

    def test_submissions_are_isolated_per_attempt(self):
        for slug in ('first', 'second'):
            response = self.client.post(
                f'/courses/attempt-course/projects/{slug}/submit',
                {'project_url': f'https://example.com/{slug}'},
            )
            self.assertContains(response, 'Your project has been submitted.')
        self.assertEqual(ProjectSubmission.objects.filter(user=self.user).count(), 2)
        response = self.client.get('/courses/attempt-course/projects/second/reviews')
        self.assertContains(response, 'https://example.com/second')
        self.assertNotContains(response, 'https://example.com/first')

    def test_submission_deadline_blocks_late_post(self):
        self.first.submission_due_at = timezone.now() - timedelta(minutes=1)
        self.first.save(update_fields=['submission_due_at'])
        response = self.client.post(
            '/courses/attempt-course/projects/first/submit',
            {'project_url': 'https://example.com/late'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProjectSubmission.objects.filter(course_project=self.first).exists())
        response = self.client.get('/courses/attempt-course/projects/second/submit')
        self.assertContains(response, 'Submit project')

    def test_legacy_submit_link_points_to_attempt_picker(self):
        response = self.client.get('/courses/attempt-course/submit')
        self.assertRedirects(response, '/courses/attempt-course#syllabus', fetch_redirect_response=False)

    def test_review_cannot_be_submitted_after_attempt_deadline(self):
        other = get_user_model().objects.create_user(email='other@example.com', password='pw')
        submission = ProjectSubmission.objects.create(
            user=other, course=self.course, course_project=self.first,
            project_url='https://example.com/other',
        )
        review = PeerReview.objects.create(submission=submission, reviewer=self.user)
        self.first.review_due_at = timezone.now() - timedelta(minutes=1)
        self.first.save(update_fields=['review_due_at'])
        response = self.client.post(
            f'/courses/attempt-course/projects/first/reviews/{submission.pk}',
            {'feedback': 'Looks good'},
        )
        self.assertEqual(response.status_code, 403)
        review.refresh_from_db()
        self.assertFalse(review.is_complete)

    def test_cohort_attempt_is_only_available_to_its_members(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Cohort 4', external_key='4',
            start_date=date(2026, 9, 21), end_date=date(2026, 11, 22),
        )
        self.first.cohort = cohort
        self.first.save(update_fields=['cohort'])
        response = self.client.get('/courses/attempt-course')
        self.assertNotContains(response, '/courses/attempt-course/projects/first/submit')
        self.assertEqual(self.client.get('/courses/attempt-course/projects/first/submit').status_code, 404)
        CohortEnrollment.objects.create(cohort=cohort, user=self.user)
        response = self.client.get('/courses/attempt-course')
        self.assertContains(response, '/courses/attempt-course/projects/first/submit')
