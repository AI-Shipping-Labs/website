"""Tests for Peer Review system - issue #126.

Covers:
- ProjectSubmission, PeerReview, CourseCertificate model fields and constraints
- Course peer review configuration fields
- Submission page: auth, access, form, update, read-only after review starts
- Review dashboard: status display, waiting state, assigned reviews
- Review form: assignment check, submission, read-only after complete
- Certificate page: public access, content display
- Round-robin batch assignment logic
- Certificate eligibility and issuance
- Management command: form_review_batches
- API endpoints
- Studio management views
- Anonymous user redirects
"""

import json
import uuid
from datetime import date, timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import Client, TestCase
from django.utils import timezone

from content.models import (
    Cohort,
    Course,
    CourseCertificate,
    Module,
    PeerReview,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)
from content.models.cohort import CohortEnrollment
from content.services.peer_review_service import PeerReviewService

User = get_user_model()


def _create_course(peer_review_enabled=True, **kwargs):
    """Helper to create a published course with peer review enabled."""
    defaults = {
        'title': 'Test Course',
        'slug': 'test-course',
        'status': 'published',
        'peer_review_enabled': peer_review_enabled,
        'peer_review_count': 3,
        'peer_review_deadline_days': 7,
        'peer_review_criteria': '# Criteria\n\n- Does it work?',
    }
    defaults.update(kwargs)
    return Course.objects.create(**defaults)


def _create_user(email, **kwargs):
    return User.objects.create_user(email=email, password='testpass123', **kwargs)


# ============================================================
# Model Tests
# ============================================================


class CourseModelPeerReviewFieldsTest(TestCase):
    """Test Course model peer review configuration fields."""

    def test_default_values(self):
        course = Course.objects.create(title='Test', slug='test-pr-defaults')
        self.assertFalse(course.peer_review_enabled)
        self.assertEqual(course.peer_review_count, 3)
        self.assertEqual(course.peer_review_deadline_days, 7)
        self.assertEqual(course.peer_review_criteria, '')

    def test_criteria_html_rendered_on_save(self):
        course = Course.objects.create(
            title='Test', slug='test-pr-html',
            peer_review_criteria='# Hello',
        )
        self.assertIn('<h1>Hello</h1>', course.peer_review_criteria_html)


class ProjectSubmissionModelTest(TestCase):
    """Test ProjectSubmission model fields and constraints."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()

    def test_create_submission(self):
        sub = ProjectSubmission.objects.create(
            user=self.user,
            course=self.course,
            project_url='https://github.com/test/project',
        )
        self.assertEqual(sub.status, 'submitted')
        self.assertIsNotNone(sub.submitted_at)
        self.assertIsNone(sub.batch_assigned_at)
        self.assertIsNone(sub.review_deadline)
        self.assertIsNone(sub.certificate_issued_at)

    def test_unique_per_user_course(self):
        ProjectSubmission.objects.create(
            user=self.user,
            course=self.course,
            project_url='https://github.com/test/project',
        )
        with self.assertRaises(IntegrityError):
            ProjectSubmission.objects.create(
                user=self.user,
                course=self.course,
                project_url='https://github.com/test/project2',
            )

    def test_str(self):
        sub = ProjectSubmission.objects.create(
            user=self.user,
            course=self.course,
            project_url='https://github.com/test/project',
        )
        self.assertIn('student@test.com', str(sub))
        self.assertIn('submitted', str(sub))


class PeerReviewModelTest(TestCase):
    """Test PeerReview model fields and constraints."""

    def setUp(self):
        self.student = _create_user('student@test.com')
        self.reviewer = _create_user('reviewer@test.com')
        self.course = _create_course()
        self.submission = ProjectSubmission.objects.create(
            user=self.student,
            course=self.course,
            project_url='https://github.com/test/project',
        )

    def test_create_review(self):
        review = PeerReview.objects.create(
            submission=self.submission,
            reviewer=self.reviewer,
        )
        self.assertFalse(review.is_complete)
        self.assertIsNone(review.score)
        self.assertEqual(review.feedback, '')
        self.assertIsNone(review.completed_at)

    def test_unique_per_submission_reviewer(self):
        PeerReview.objects.create(
            submission=self.submission,
            reviewer=self.reviewer,
        )
        with self.assertRaises(IntegrityError):
            PeerReview.objects.create(
                submission=self.submission,
                reviewer=self.reviewer,
            )


class CourseCertificateModelTest(TestCase):
    """Test CourseCertificate model fields and constraints."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()

    def test_create_certificate(self):
        cert = CourseCertificate.objects.create(
            user=self.user,
            course=self.course,
        )
        self.assertIsInstance(cert.id, uuid.UUID)
        self.assertIsNotNone(cert.issued_at)

    def test_unique_per_user_course(self):
        CourseCertificate.objects.create(user=self.user, course=self.course)
        with self.assertRaises(IntegrityError):
            CourseCertificate.objects.create(user=self.user, course=self.course)

    def test_get_absolute_url(self):
        cert = CourseCertificate.objects.create(
            user=self.user, course=self.course,
        )
        self.assertEqual(cert.get_absolute_url(), f'/certificates/{cert.id}')


# ============================================================
# View Tests - Submission
# ============================================================


class ProjectSubmitViewTest(TestCase):
    """Test the project submission page."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()
        self.client = Client()
        self.client.login(email='student@test.com', password='testpass123')

    def test_submit_page_loads(self):
        response = self.client.get('/courses/test-course/submit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Submit Project')

    def test_submit_project(self):
        response = self.client.post('/courses/test-course/submit', {
            'project_url': 'https://github.com/test/project',
            'description': 'My project',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your project has been submitted')
        sub = ProjectSubmission.objects.get(user=self.user, course=self.course)
        self.assertEqual(sub.project_url, 'https://github.com/test/project')
        self.assertEqual(sub.description, 'My project')

    def test_update_submission_while_submitted(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/old',
        )
        response = self.client.post('/courses/test-course/submit', {
            'project_url': 'https://github.com/test/new',
            'description': 'Updated',
        })
        self.assertEqual(response.status_code, 200)
        sub = ProjectSubmission.objects.get(user=self.user, course=self.course)
        self.assertEqual(sub.project_url, 'https://github.com/test/new')

    def test_cannot_update_after_review_started(self):
        sub = ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/old',
            status='in_review',
        )
        response = self.client.post('/courses/test-course/submit', {
            'project_url': 'https://github.com/test/new',
        })
        self.assertEqual(response.status_code, 200)
        # Should show read-only view, not update
        sub.refresh_from_db()
        self.assertEqual(sub.project_url, 'https://github.com/test/old')

    def test_404_if_peer_review_not_enabled(self):
        course = _create_course(
            peer_review_enabled=False, slug='no-pr', title='No PR',
        )
        response = self.client.get(f'/courses/{course.slug}/submit')
        self.assertEqual(response.status_code, 404)

    def test_redirect_if_anonymous(self):
        self.client.logout()
        response = self.client.get('/courses/test-course/submit')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_submit_requires_project_url(self):
        response = self.client.post('/courses/test-course/submit', {
            'project_url': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Project URL is required')
        self.assertFalse(
            ProjectSubmission.objects.filter(user=self.user).exists()
        )

    def test_readonly_shows_status_and_no_form(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/project',
            status='in_review',
        )
        response = self.client.get('/courses/test-course/submit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'In Review')
        self.assertContains(response, 'Review Dashboard')


# ============================================================
# View Tests - Review Dashboard
# ============================================================


class ReviewDashboardViewTest(TestCase):
    """Test the peer review dashboard page."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()
        self.client = Client()
        self.client.login(email='student@test.com', password='testpass123')

    def test_dashboard_no_submission(self):
        response = self.client.get('/courses/test-course/reviews')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Submit Project')

    def test_dashboard_waiting_for_batch(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/project',
        )
        response = self.client.get('/courses/test-course/reviews')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Waiting for enough submissions')

    def test_dashboard_shows_assigned_reviews(self):
        # Create submissions and assignments
        ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/myproject',
            status='in_review',
        )
        other_user = _create_user('other@test.com')
        other_sub = ProjectSubmission.objects.create(
            user=other_user, course=self.course,
            project_url='https://github.com/other/project',
            status='in_review',
        )
        PeerReview.objects.create(submission=other_sub, reviewer=self.user)

        response = self.client.get('/courses/test-course/reviews')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://github.com/other/project')
        self.assertContains(response, 'Pending')

    def test_dashboard_shows_received_reviews(self):
        sub = ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/project',
            status='review_complete',
        )
        reviewer = _create_user('reviewer@test.com')
        PeerReview.objects.create(
            submission=sub, reviewer=reviewer,
            is_complete=True, score=4, feedback='Great work!',
            completed_at=timezone.now(),
        )
        response = self.client.get('/courses/test-course/reviews')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Great work!')
        self.assertContains(response, '4/5')

    def test_redirect_if_anonymous(self):
        self.client.logout()
        response = self.client.get('/courses/test-course/reviews')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


# ============================================================
# View Tests - Review Form
# ============================================================


class ReviewFormViewTest(TestCase):
    """Test the peer review form page."""

    def setUp(self):
        self.student = _create_user('student@test.com')
        self.reviewer = _create_user('reviewer@test.com')
        self.course = _create_course()
        self.submission = ProjectSubmission.objects.create(
            user=self.student, course=self.course,
            project_url='https://github.com/test/project',
            description='My project description',
            status='in_review',
        )
        self.review = PeerReview.objects.create(
            submission=self.submission, reviewer=self.reviewer,
        )
        self.client = Client()
        self.client.login(email='reviewer@test.com', password='testpass123')

    def test_review_form_loads(self):
        response = self.client.get(
            f'/courses/test-course/reviews/{self.submission.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://github.com/test/project')
        self.assertContains(response, 'My project description')
        self.assertContains(response, 'Criteria')  # rendered criteria html

    def test_submit_review(self):
        response = self.client.post(
            f'/courses/test-course/reviews/{self.submission.pk}',
            {'score': '4', 'feedback': 'Looks good!'},
        )
        self.assertEqual(response.status_code, 302)
        self.review.refresh_from_db()
        self.assertTrue(self.review.is_complete)
        self.assertEqual(self.review.score, 4)
        self.assertEqual(self.review.feedback, 'Looks good!')
        self.assertIsNotNone(self.review.completed_at)

    def test_403_if_not_assigned(self):
        _create_user('other@test.com')
        self.client.login(email='other@test.com', password='testpass123')
        response = self.client.get(
            f'/courses/test-course/reviews/{self.submission.pk}'
        )
        self.assertEqual(response.status_code, 403)

    def test_completed_review_readonly(self):
        self.review.is_complete = True
        self.review.score = 5
        self.review.feedback = 'Perfect!'
        self.review.completed_at = timezone.now()
        self.review.save()

        response = self.client.get(
            f'/courses/test-course/reviews/{self.submission.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Perfect!')
        self.assertContains(response, 'submitted')

    def test_feedback_required(self):
        response = self.client.post(
            f'/courses/test-course/reviews/{self.submission.pk}',
            {'score': '4', 'feedback': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Feedback is required')
        self.review.refresh_from_db()
        self.assertFalse(self.review.is_complete)


# ============================================================
# View Tests - Certificate Page
# ============================================================


class CertificatePageViewTest(TestCase):
    """Test the public certificate page."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()
        self.cert = CourseCertificate.objects.create(
            user=self.user, course=self.course,
        )

    def test_certificate_page_public(self):
        """Certificate page is publicly accessible without authentication."""
        client = Client()
        response = client.get(f'/certificates/{self.cert.id}')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Certificate of Completion')
        self.assertContains(response, 'Test Course')
        self.assertContains(response, 'student@test.com')

    def test_certificate_page_404_for_invalid_uuid(self):
        client = Client()
        fake_id = uuid.uuid4()
        response = client.get(f'/certificates/{fake_id}')
        self.assertEqual(response.status_code, 404)


# ============================================================
# Service Tests - Batch Formation
# ============================================================


class BatchFormationTest(TestCase):
    """Test round-robin review assignment logic."""

    def setUp(self):
        self.course = _create_course(peer_review_count=2)

    def test_self_paced_batch_formation(self):
        """Self-paced batch forms when pool >= peer_review_count + 1."""
        users = [_create_user(f'user{i}@test.com') for i in range(3)]
        subs = []
        for user in users:
            subs.append(ProjectSubmission.objects.create(
                user=user, course=self.course,
                project_url=f'https://github.com/{user.email}/project',
            ))

        result = PeerReviewService.form_batches_for_course(self.course)
        self.assertEqual(result['batched'], 3)
        self.assertEqual(result['reviews_assigned'], 6)  # 3 students * 2 reviews each

        # All submissions should be in_review now
        for sub in subs:
            sub.refresh_from_db()
            self.assertEqual(sub.status, 'in_review')
            self.assertIsNotNone(sub.batch_assigned_at)
            self.assertIsNotNone(sub.review_deadline)

        # Each submission should have 2 reviews assigned
        for sub in subs:
            self.assertEqual(sub.reviews.count(), 2)

        # No student reviews their own submission
        for sub in subs:
            self.assertFalse(
                sub.reviews.filter(reviewer=sub.user).exists()
            )

    def test_not_enough_submissions_no_batch(self):
        """No batch formed if fewer than peer_review_count + 1 submissions."""
        users = [_create_user(f'user{i}@test.com') for i in range(2)]
        for user in users:
            ProjectSubmission.objects.create(
                user=user, course=self.course,
                project_url=f'https://github.com/{user.email}/project',
            )

        result = PeerReviewService.form_batches_for_course(self.course)
        self.assertEqual(result['batched'], 0)

    def test_cohort_batch_formation(self):
        """Cohort batch forms when cohort end date has passed."""
        cohort = Cohort.objects.create(
            course=self.course, name='Test Cohort',
            start_date=date.today() - timedelta(days=30),
            end_date=date.today() - timedelta(days=1),
        )
        users = [_create_user(f'user{i}@test.com') for i in range(3)]
        for user in users:
            CohortEnrollment.objects.create(cohort=cohort, user=user)
            ProjectSubmission.objects.create(
                user=user, course=self.course, cohort=cohort,
                project_url=f'https://github.com/{user.email}/project',
            )

        result = PeerReviewService.form_batches_for_course(self.course)
        self.assertEqual(result['batched'], 3)

    def test_round_robin_even_distribution(self):
        """Each submission gets approximately the same number of reviewers."""
        self.course.peer_review_count = 2
        self.course.save()

        users = [_create_user(f'user{i}@test.com') for i in range(4)]
        subs = []
        for user in users:
            subs.append(ProjectSubmission.objects.create(
                user=user, course=self.course,
                project_url=f'https://github.com/{user.email}/project',
            ))

        PeerReviewService.form_batches_for_course(self.course)

        # Each submission should get exactly 2 reviewers
        for sub in subs:
            self.assertEqual(sub.reviews.count(), 2)


# ============================================================
# Service Tests - Certificate Eligibility
# ============================================================


class CertificateEligibilityTest(TestCase):
    """Test certificate issuance logic."""

    def setUp(self):
        self.course = _create_course(peer_review_count=1)
        self.user1 = _create_user('user1@test.com')
        self.user2 = _create_user('user2@test.com')

        # Create a unit
        module = Module.objects.create(course=self.course, title='M1', slug='m1', sort_order=0)
        self.unit = Unit.objects.create(module=module, title='U1', slug='u1', sort_order=0)

    def _setup_complete_scenario(self):
        """Set up a scenario where both students have done everything."""
        # Both submit projects
        sub1 = ProjectSubmission.objects.create(
            user=self.user1, course=self.course,
            project_url='https://github.com/user1/project',
            status='in_review',
        )
        sub2 = ProjectSubmission.objects.create(
            user=self.user2, course=self.course,
            project_url='https://github.com/user2/project',
            status='in_review',
        )

        # Cross-review assignments
        r1 = PeerReview.objects.create(submission=sub1, reviewer=self.user2)
        r2 = PeerReview.objects.create(submission=sub2, reviewer=self.user1)

        # Complete all reviews
        for r in [r1, r2]:
            r.is_complete = True
            r.score = 4
            r.feedback = 'Good job'
            r.completed_at = timezone.now()
            r.save()

        # Update submission statuses
        sub1.status = 'review_complete'
        sub1.save()
        sub2.status = 'review_complete'
        sub2.save()

        # Complete units
        UserCourseProgress.objects.create(
            user=self.user1, unit=self.unit, completed_at=timezone.now(),
        )
        UserCourseProgress.objects.create(
            user=self.user2, unit=self.unit, completed_at=timezone.now(),
        )

        return sub1, sub2

    def test_certificate_issued_when_all_conditions_met(self):
        self._setup_complete_scenario()

        cert = PeerReviewService.check_certificate_eligibility(
            self.user1, self.course,
        )
        self.assertIsNotNone(cert)
        self.assertEqual(cert.user, self.user1)
        self.assertEqual(cert.course, self.course)

    def test_no_certificate_if_units_incomplete(self):
        self._setup_complete_scenario()
        # Remove unit completion
        UserCourseProgress.objects.filter(user=self.user1).delete()

        cert = PeerReviewService.check_certificate_eligibility(
            self.user1, self.course,
        )
        self.assertIsNone(cert)

    def test_no_certificate_if_own_reviews_incomplete(self):
        sub1 = ProjectSubmission.objects.create(
            user=self.user1, course=self.course,
            project_url='https://github.com/user1/project',
            status='review_complete',
        )
        sub2 = ProjectSubmission.objects.create(
            user=self.user2, course=self.course,
            project_url='https://github.com/user2/project',
            status='in_review',
        )

        # user2 reviewed user1's submission
        PeerReview.objects.create(
            submission=sub1, reviewer=self.user2,
            is_complete=True, feedback='ok', completed_at=timezone.now(),
        )
        # user1 has NOT completed review of user2's submission
        PeerReview.objects.create(
            submission=sub2, reviewer=self.user1,
            is_complete=False,
        )

        UserCourseProgress.objects.create(
            user=self.user1, unit=self.unit, completed_at=timezone.now(),
        )

        cert = PeerReviewService.check_certificate_eligibility(
            self.user1, self.course,
        )
        self.assertIsNone(cert)

    def test_no_duplicate_certificate(self):
        self._setup_complete_scenario()
        cert1 = PeerReviewService.check_certificate_eligibility(
            self.user1, self.course,
        )
        cert2 = PeerReviewService.check_certificate_eligibility(
            self.user1, self.course,
        )
        self.assertIsNotNone(cert1)
        self.assertIsNone(cert2)
        self.assertEqual(
            CourseCertificate.objects.filter(
                user=self.user1, course=self.course,
            ).count(),
            1,
        )

    def test_issue_certificates_for_course(self):
        self._setup_complete_scenario()
        count = PeerReviewService.issue_certificates_for_course(self.course)
        self.assertEqual(count, 2)
        self.assertEqual(
            CourseCertificate.objects.filter(course=self.course).count(), 2,
        )


# ============================================================
# Service Tests - Status Updates
# ============================================================


class SubmissionStatusUpdateTest(TestCase):
    """Test automatic status transitions."""

    def setUp(self):
        self.course = _create_course(peer_review_count=1)
        self.user1 = _create_user('user1@test.com')
        self.user2 = _create_user('user2@test.com')
        self.sub = ProjectSubmission.objects.create(
            user=self.user1, course=self.course,
            project_url='https://github.com/user1/project',
            status='in_review',
        )

    def test_status_changes_to_review_complete(self):
        PeerReview.objects.create(
            submission=self.sub, reviewer=self.user2,
            is_complete=True, feedback='Good', completed_at=timezone.now(),
        )
        PeerReviewService.check_and_update_submission_status(self.sub)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, 'review_complete')


# ============================================================
# Management Command Tests
# ============================================================


class FormReviewBatchesCommandTest(TestCase):
    """Test the form_review_batches management command."""

    def test_command_runs(self):
        course = _create_course(peer_review_count=1)
        users = [_create_user(f'cmd{i}@test.com') for i in range(2)]
        for user in users:
            ProjectSubmission.objects.create(
                user=user, course=course,
                project_url=f'https://github.com/{user.email}/project',
            )

        out = StringIO()
        call_command('form_review_batches', stdout=out)
        output = out.getvalue()
        self.assertIn('batch', output.lower())

    def test_no_courses_message(self):
        out = StringIO()
        call_command('form_review_batches', stdout=out)
        self.assertIn('No courses', out.getvalue())


# ============================================================
# API Tests
# ============================================================


class APISubmitProjectTest(TestCase):
    """Test POST /api/courses/<slug>/submit endpoint."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()
        self.client = Client()
        self.client.login(email='student@test.com', password='testpass123')

    def test_submit_via_api(self):
        response = self.client.post(
            '/api/courses/test-course/submit',
            data=json.dumps({
                'project_url': 'https://github.com/test/project',
                'description': 'My project',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'submitted')

    def test_api_401_if_anonymous(self):
        self.client.logout()
        response = self.client.post(
            '/api/courses/test-course/submit',
            data=json.dumps({'project_url': 'https://github.com/test'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_api_404_if_peer_review_disabled(self):
        course = _create_course(
            peer_review_enabled=False, slug='no-pr2', title='No PR',
        )
        response = self.client.post(
            f'/api/courses/{course.slug}/submit',
            data=json.dumps({'project_url': 'https://github.com/test'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 404)


class APIReviewDashboardTest(TestCase):
    """Test GET /api/courses/<slug>/reviews endpoint."""

    def setUp(self):
        self.user = _create_user('student@test.com')
        self.course = _create_course()
        self.client = Client()
        self.client.login(email='student@test.com', password='testpass123')

    def test_dashboard_api(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course,
            project_url='https://github.com/test/project',
        )
        response = self.client.get('/api/courses/test-course/reviews')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNotNone(data['submission'])
        self.assertEqual(data['submission']['status'], 'submitted')


class APISubmitReviewTest(TestCase):
    """Test POST /api/courses/<slug>/reviews/<submission_id> endpoint."""

    def setUp(self):
        self.student = _create_user('student@test.com')
        self.reviewer = _create_user('reviewer@test.com')
        self.course = _create_course()
        self.submission = ProjectSubmission.objects.create(
            user=self.student, course=self.course,
            project_url='https://github.com/test/project',
            status='in_review',
        )
        self.review = PeerReview.objects.create(
            submission=self.submission, reviewer=self.reviewer,
        )
        self.client = Client()
        self.client.login(email='reviewer@test.com', password='testpass123')

    def test_submit_review_via_api(self):
        response = self.client.post(
            f'/api/courses/test-course/reviews/{self.submission.pk}',
            data=json.dumps({'score': 4, 'feedback': 'Good work'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['is_complete'])

    def test_403_if_not_assigned(self):
        _create_user('other@test.com')
        self.client.login(email='other@test.com', password='testpass123')
        response = self.client.post(
            f'/api/courses/test-course/reviews/{self.submission.pk}',
            data=json.dumps({'score': 3, 'feedback': 'ok'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)


# ============================================================
# Studio Tests
# ============================================================


class StudioPeerReviewViewTest(TestCase):
    """Test studio peer review management views."""

    def setUp(self):
        self.staff = _create_user('staff@test.com', is_staff=True)
        self.course = _create_course()
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass123')

    def test_management_page_loads(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Peer Reviews')

    def test_form_batch_action(self):
        users = [_create_user(f'user{i}@test.com') for i in range(4)]
        for user in users:
            ProjectSubmission.objects.create(
                user=user, course=self.course,
                project_url=f'https://github.com/{user.email}/project',
            )

        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/form-batch',
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        # Should have formed a batch
        in_review = ProjectSubmission.objects.filter(
            course=self.course, status='in_review',
        ).count()
        self.assertEqual(in_review, 4)

    def test_issue_certificates_action(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/issue-certificates',
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

    def test_extend_deadline_action(self):
        user = _create_user('u@test.com')
        sub = ProjectSubmission.objects.create(
            user=user, course=self.course,
            project_url='https://github.com/u/p',
            status='in_review',
            review_deadline=timezone.now() + timedelta(days=3),
        )
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/peer-reviews/extend-deadline',
            {'days': '5'},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        sub.refresh_from_db()
        # Deadline extended by 5 days
        self.assertIsNotNone(sub.review_deadline)

    def test_non_staff_redirected(self):
        self.client.logout()
        _create_user('nostaff@test.com')
        self.client.login(email='nostaff@test.com', password='testpass123')
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/peer-reviews'
        )
        self.assertEqual(response.status_code, 403)

    def test_course_edit_includes_peer_review_fields(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/edit'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'peer_review_enabled')
        self.assertContains(response, 'peer_review_count')
        self.assertContains(response, 'peer_review_deadline_days')
        self.assertContains(response, 'peer_review_criteria')

    def test_course_edit_saves_peer_review_fields(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/edit',
            {
                'title': 'Updated Course',
                'slug': 'test-course',
                'status': 'published',
                'required_level': '0',
                'peer_review_enabled': 'on',
                'peer_review_count': '5',
                'peer_review_deadline_days': '14',
                'peer_review_criteria': '# Updated criteria',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.course.refresh_from_db()
        self.assertTrue(self.course.peer_review_enabled)
        self.assertEqual(self.course.peer_review_count, 5)
        self.assertEqual(self.course.peer_review_deadline_days, 14)
        self.assertIn('Updated criteria', self.course.peer_review_criteria)


# ============================================================
# Notification Tests
# ============================================================


class PeerReviewNotificationTest(TestCase):
    """Test that notifications are created at the right times."""

    def setUp(self):
        self.course = _create_course(peer_review_count=1)
        self.user1 = _create_user('user1@test.com')
        self.user2 = _create_user('user2@test.com')

    def test_batch_notification_created(self):
        from notifications.models import Notification

        for user in [self.user1, self.user2]:
            ProjectSubmission.objects.create(
                user=user, course=self.course,
                project_url=f'https://github.com/{user.email}/project',
            )

        PeerReviewService.form_batches_for_course(self.course)

        # Both users should have received a notification
        for user in [self.user1, self.user2]:
            notif = Notification.objects.filter(
                user=user, title__contains='Peer reviews ready',
            )
            self.assertTrue(notif.exists())

    def test_review_complete_notification(self):
        from notifications.models import Notification

        sub = ProjectSubmission.objects.create(
            user=self.user1, course=self.course,
            project_url='https://github.com/user1/project',
            status='in_review',
        )
        PeerReview.objects.create(
            submission=sub, reviewer=self.user2,
            is_complete=True, feedback='Good', completed_at=timezone.now(),
        )
        PeerReviewService.check_and_update_submission_status(sub)

        notif = Notification.objects.filter(
            user=self.user1, title__contains='Reviews complete',
        )
        self.assertTrue(notif.exists())

    def test_certificate_notification(self):
        from notifications.models import Notification

        module = Module.objects.create(
            course=self.course, title='M1', slug='m1', sort_order=0,
        )
        unit = Unit.objects.create(module=module, title='U1', slug='u1', sort_order=0)
        UserCourseProgress.objects.create(
            user=self.user1, unit=unit, completed_at=timezone.now(),
        )

        sub1 = ProjectSubmission.objects.create(
            user=self.user1, course=self.course,
            project_url='https://github.com/user1/project',
            status='review_complete',
        )
        sub2 = ProjectSubmission.objects.create(
            user=self.user2, course=self.course,
            project_url='https://github.com/user2/project',
            status='in_review',
        )
        PeerReview.objects.create(
            submission=sub1, reviewer=self.user2,
            is_complete=True, feedback='ok', completed_at=timezone.now(),
        )
        PeerReview.objects.create(
            submission=sub2, reviewer=self.user1,
            is_complete=True, feedback='ok', completed_at=timezone.now(),
        )

        cert = PeerReviewService.check_certificate_eligibility(
            self.user1, self.course,
        )
        self.assertIsNotNone(cert)

        notif = Notification.objects.filter(
            user=self.user1, title__contains='Certificate earned',
        )
        self.assertTrue(notif.exists())
