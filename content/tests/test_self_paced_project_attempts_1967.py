"""Learner-facing self-paced CourseProject flows for issue #1967."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    CourseCertificate,
    CourseProject,
    Module,
    PeerReview,
    ProjectSubmission,
    Unit,
    UserCourseProgress,
)

User = get_user_model()


class SelfPacedProjectAttemptTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='learner-1967@test.com', password='pw',
        )
        self.course = Course.objects.create(
            title='Self-paced course', slug='self-paced-1967',
            status='published', required_level=0,
            peer_review_enabled=True, peer_review_count=2,
        )
        self.module = Module.objects.create(
            course=self.course, title='Core', slug='core', sort_order=0,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Lesson', slug='lesson', sort_order=0,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Self-paced', external_key='self-paced',
            mode='self_paced', is_active=True,
        )
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)
        now = timezone.now()
        self.project = CourseProject.objects.create(
            course=self.course, cohort=self.cohort, module=self.module,
            slug='project', title='Project',
            submission_due_at=now - timedelta(days=30),
            review_due_at=now - timedelta(days=20), peer_review_count=2,
        )
        self.client.force_login(self.user)

    def test_copied_dates_do_not_block_or_render_on_submission(self):
        response = self.client.post(
            '/courses/self-paced-1967/projects/project/submit',
            {'github_link': 'https://github.com/me/project', 'commit_id': 'a1b2c3d'},
        )

        self.assertTrue(ProjectSubmission.objects.filter(
            user=self.user, course_project=self.project,
        ).exists())
        self.assertTrue(response.context['self_paced_project'])
        self.assertFalse(response.context['deadline_passed'])
        self.assertIsNone(
            response.context['project_form'].project.submission_due_date,
        )

    def test_projects_tab_keeps_past_copied_attempt_open(self):
        response = self.client.get('/courses/self-paced-1967/home/projects')

        self.assertContains(response, 'Submit project')
        self.assertNotContains(response, 'Closed')

    def _eligible_except_final_assigned_review(self):
        own = ProjectSubmission.objects.create(
            user=self.user, course=self.course, cohort=self.cohort,
            course_project=self.project, project_url='https://example.com/own',
            status='review_complete',
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.unit, completed_at=timezone.now(),
        )
        assigned = []
        for number in range(2):
            peer = User.objects.create_user(email=f'peer-view-{number}@test.com')
            peer_submission = ProjectSubmission.objects.create(
                user=peer, course=self.course, cohort=self.cohort,
                course_project=self.project,
                project_url=f'https://example.com/peer/{number}',
            )
            PeerReview.objects.create(
                submission=own, reviewer=peer, is_complete=True,
            )
            assigned.append(PeerReview.objects.create(
                submission=peer_submission, reviewer=self.user,
                is_complete=number == 0,
            ))
        return assigned[-1]

    def test_final_assigned_review_issues_certificate_and_dashboard_cta(self):
        review = self._eligible_except_final_assigned_review()

        response = self.client.post(
            f'/courses/self-paced-1967/projects/project/reviews/{review.submission_id}',
            {'feedback': 'Completed without a fixed deadline'},
        )

        self.assertRedirects(
            response, '/courses/self-paced-1967/projects/project/reviews',
            fetch_redirect_response=False,
        )
        self.assertEqual(CourseCertificate.objects.filter(user=self.user).count(), 1)
        dashboard = self.client.get(response['Location'])
        self.assertContains(dashboard, 'peer-review-certificate-cta')
        self.assertContains(dashboard, 'Reviews remain open until completed')
        self.assertNotContains(dashboard, 'Reviews due')
