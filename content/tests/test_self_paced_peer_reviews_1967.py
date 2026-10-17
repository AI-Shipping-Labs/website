"""Rolling peer review and certificate rules for issue #1967."""

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
from content.services import completion as completion_service
from content.services.peer_review_service import PeerReviewService
from content.tasks.peer_reviews import form_self_paced_review_batches
from notifications.models import Notification

User = get_user_model()


class SelfPacedReviewBatchTest(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            title='Rolling course', slug='rolling-1967',
            peer_review_enabled=True, peer_review_count=2,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
            is_active=True,
        )
        self.project = CourseProject.objects.create(
            course=self.course, cohort=self.cohort, slug='capstone',
            title='Capstone', submission_due_at=None,
            review_due_at=None, peer_review_count=2,
        )

    def _submission(self, number, *, project=None, **fields):
        user = User.objects.create_user(email=f'rolling-{number}@test.com')
        CohortEnrollment.objects.create(user=user, cohort=self.cohort)
        return ProjectSubmission.objects.create(
            user=user, course=self.course, cohort=self.cohort,
            course_project=project or self.project,
            project_url=f'https://example.com/{number}', **fields,
        )

    def test_oldest_complete_microbatches_form_without_deadlines(self):
        submissions = [self._submission(number) for number in range(7)]
        result = PeerReviewService.form_self_paced_batches_for_project(
            self.project,
        )

        self.assertEqual(result, {'batched': 6, 'reviews_assigned': 12})
        for submission in submissions[:6]:
            submission.refresh_from_db()
            self.assertEqual(submission.status, 'in_review')
            self.assertIsNone(submission.review_deadline)
            self.assertEqual(submission.reviews.count(), 2)
        submissions[-1].refresh_from_db()
        self.assertEqual(submissions[-1].status, 'submitted')
        bodies = Notification.objects.values_list('body', flat=True)
        self.assertEqual(len(bodies), 6)
        self.assertTrue(all('remain open until completed' in body for body in bodies))

    def test_project_pools_are_isolated_and_idempotent(self):
        second = CourseProject.objects.create(
            course=self.course, cohort=self.cohort, slug='second', title='Second',
            submission_due_at=None, review_due_at=None,
            peer_review_count=2,
        )
        for number in range(2):
            self._submission(number, project=self.project)
            self._submission(number + 10, project=second)

        result = PeerReviewService.form_batches_for_course(self.course)

        self.assertEqual(result, {'batched': 0, 'reviews_assigned': 0})
        self.assertFalse(PeerReview.objects.exists())

    def test_started_lifecycle_is_never_rebatched_after_status_drift(self):
        now = timezone.now()
        assigned = self._submission(
            1, status='submitted', batch_assigned_at=now - timedelta(hours=1),
        )
        reviewed = self._submission(2, status='submitted')
        PeerReview.objects.create(submission=reviewed, reviewer=assigned.user)
        self._submission(3)
        self._submission(4)

        result = PeerReviewService.form_self_paced_batches_for_project(
            self.project,
        )

        self.assertEqual(result, {'batched': 0, 'reviews_assigned': 0})
        assigned.refresh_from_db()
        reviewed.refresh_from_db()
        self.assertEqual(assigned.status, 'submitted')
        self.assertEqual(reviewed.reviews.count(), 1)

    def test_removed_learner_is_excluded_before_batch_assignment(self):
        removed = self._submission(1)
        eligible = [self._submission(number) for number in (2, 3, 4)]
        CohortEnrollment.objects.filter(
            user=removed.user, cohort=self.cohort,
        ).delete()

        result = PeerReviewService.form_self_paced_batches_for_project(
            self.project,
        )

        self.assertEqual(result, {'batched': 3, 'reviews_assigned': 6})
        removed.refresh_from_db()
        self.assertEqual(removed.status, 'submitted')
        self.assertFalse(removed.reviews.exists())
        self.assertEqual(sum(item.reviews.count() for item in eligible), 6)


class SelfPacedReviewTaskTest(TestCase):
    def _project_with_submissions(self, active, *, enabled=True):
        course = Course.objects.create(
            title=f'Course {active} {enabled}',
            slug=f'course-{active}-{enabled}',
            peer_review_count=1, peer_review_enabled=enabled,
        )
        cohort = Cohort.objects.create(
            course=course, name='Self-paced', mode='self_paced', is_active=active,
        )
        project = CourseProject.objects.create(
            course=course, cohort=cohort, slug='project', title='Project',
            submission_due_at=None, review_due_at=None, peer_review_count=1,
        )
        for number in range(2):
            user = User.objects.create_user(
                email=f'{active}-{enabled}-{number}@test.com',
            )
            CohortEnrollment.objects.create(user=user, cohort=cohort)
            ProjectSubmission.objects.create(
                user=user, course=course, cohort=cohort,
                course_project=project,
                project_url=f'https://example.com/{active}/{number}',
            )
        return project

    def test_sweep_only_processes_active_self_paced_projects(self):
        active = self._project_with_submissions(True)
        inactive = self._project_with_submissions(False)
        disabled = self._project_with_submissions(True, enabled=False)

        result = form_self_paced_review_batches()

        self.assertEqual(result, {'batched': 2, 'reviews_assigned': 2})
        self.assertEqual(active.submissions.filter(status='in_review').count(), 2)
        self.assertEqual(inactive.submissions.filter(status='submitted').count(), 2)
        self.assertEqual(disabled.submissions.filter(status='submitted').count(), 2)


class SelfPacedCertificateTest(TestCase):
    def setUp(self):
        self.course = Course.objects.create(
            title='Certificate course', slug='certificate-1967',
            peer_review_count=2,
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
            is_active=True,
        )
        self.module = Module.objects.create(
            course=self.course, title='Core', slug='core', sort_order=0,
        )
        self.core = Unit.objects.create(
            module=self.module, title='Core unit', slug='core-unit', sort_order=0,
        )
        self.project = CourseProject.objects.create(
            course=self.course, cohort=self.cohort, slug='project', title='Project',
            submission_due_at=None, review_due_at=None,
            peer_review_count=2,
        )
        self.user = User.objects.create_user(email='certificate@test.com')
        self.own = ProjectSubmission.objects.create(
            user=self.user, course=self.course, cohort=self.cohort,
            course_project=self.project, project_url='https://example.com/own',
            status='review_complete',
        )
        self.peer_submissions = self._complete_review_rows()

    def _complete_review_rows(self):
        submissions = []
        for number in range(2):
            peer = User.objects.create_user(email=f'peer-{number}@test.com')
            submission = ProjectSubmission.objects.create(
                user=peer, course=self.course, cohort=self.cohort,
                course_project=self.project,
                project_url=f'https://example.com/peer/{number}',
            )
            PeerReview.objects.create(
                submission=self.own, reviewer=peer, is_complete=True,
            )
            PeerReview.objects.create(
                submission=submission, reviewer=self.user, is_complete=True,
            )
            submissions.append(submission)
        return submissions

    def test_configured_assigned_and_received_counts_are_required(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.core, completed_at=timezone.now(),
        )
        self.peer_submissions[1].reviews.filter(reviewer=self.user).delete()

        certificate = PeerReviewService.check_certificate_eligibility(
            self.user, self.course, course_project=self.project,
        )

        self.assertIsNone(certificate)

    def test_final_received_review_triggers_owner_certificate(self):
        UserCourseProgress.objects.create(
            user=self.user, unit=self.core, completed_at=timezone.now(),
        )
        final = self.own.reviews.order_by('pk').last()
        final.is_complete = False
        final.save(update_fields=['is_complete'])
        self.assertIsNone(PeerReviewService.check_certificate_eligibility(
            self.user, self.course, course_project=self.project,
        ))

        final.is_complete = True
        final.save(update_fields=['is_complete'])
        PeerReviewService.check_and_update_submission_status(self.own)

        self.assertEqual(CourseCertificate.objects.filter(user=self.user).count(), 1)

    def test_last_core_completion_issues_once_and_bonus_is_irrelevant(self):
        bonus_module = Module.objects.create(
            course=self.course, title='Bonus', slug='bonus',
            sort_order=1, is_bonus=True,
        )
        bonus = Unit.objects.create(
            module=bonus_module, title='Bonus unit', slug='bonus-unit', sort_order=0,
        )

        completion_service.mark_completed(self.user, bonus)
        self.assertFalse(CourseCertificate.objects.filter(user=self.user).exists())
        completion_service.mark_completed(self.user, self.core)
        completion_service.mark_completed(self.user, self.core)

        certificate = CourseCertificate.objects.get(user=self.user)
        self.assertEqual(certificate.submission, self.own)
        self.assertEqual(CourseCertificate.objects.filter(user=self.user).count(), 1)
