"""Certificate unit requirements for Self-paced project attempts (issue #1979)."""

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

User = get_user_model()


class SelfPacedCertificateUnitRequirementsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Certificate units', slug='certificate-units-1979',
            peer_review_count=1,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
            is_active=True,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Core', slug='core', sort_order=0,
        )
        cls._create_units()
        cls.project = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, slug='project',
            title='Project', submission_due_at=None,
            review_due_at=None, peer_review_count=1,
        )
        cls.user = User.objects.create_user(email='certificate-units@test.com')

    @classmethod
    def _create_units(cls):
        cls.lesson = Unit.objects.create(
            module=cls.module, title='Lesson', slug='lesson',
            sort_order=0, kind='lesson',
        )
        cls.homework = Unit.objects.create(
            module=cls.module, title='Homework', slug='homework',
            sort_order=1, kind='homework',
        )
        cls.checklist = Unit.objects.create(
            module=cls.module, title='Checklist', slug='checklist',
            sort_order=2, kind='checklist_item',
        )
        cls.event = Unit.objects.create(
            module=cls.module, title='Live session', slug='live-session',
            sort_order=3, kind='event', session_position=1,
        )

    def _review_scenario(self, *, received_complete=True):
        own = ProjectSubmission.objects.create(
            user=self.user, course=self.course, cohort=self.cohort,
            course_project=self.project, project_url='https://example.com/own',
            status='in_review',
        )
        peer = User.objects.create_user(email='certificate-peer@test.com')
        peer_submission = ProjectSubmission.objects.create(
            user=peer, course=self.course, cohort=self.cohort,
            course_project=self.project, project_url='https://example.com/peer',
            status='in_review',
        )
        received = PeerReview.objects.create(
            submission=own, reviewer=peer, is_complete=received_complete,
        )
        PeerReview.objects.create(
            submission=peer_submission, reviewer=self.user, is_complete=True,
        )
        return own, received

    def _complete(self, *units):
        for unit in units:
            UserCourseProgress.objects.create(
                user=self.user, unit=unit, completed_at=timezone.now(),
            )

    def assert_event_incomplete(self):
        self.assertFalse(UserCourseProgress.objects.filter(
            user=self.user, unit=self.event,
        ).exists())

    def test_self_paced_requires_all_non_event_kinds_but_not_event(self):
        own, _received = self._review_scenario()
        self._complete(self.lesson, self.homework)

        missing_checklist = PeerReviewService.check_certificate_eligibility(
            self.user, self.course, course_project=self.project,
        )
        self.assertIsNone(missing_checklist)

        self._complete(self.checklist)
        certificate = PeerReviewService.check_certificate_eligibility(
            self.user, self.course, course_project=self.project,
        )

        self.assertEqual(certificate.submission, own)
        self.assert_event_incomplete()

    def test_only_event_units_preserve_zero_required_unit_safeguard(self):
        self.lesson.delete()
        self.homework.delete()
        self.checklist.delete()
        self._review_scenario()

        certificate = PeerReviewService.check_certificate_eligibility(
            self.user, self.course, course_project=self.project,
        )

        self.assertIsNone(certificate)
        self.assertFalse(CourseCertificate.objects.filter(
            user=self.user, course=self.course,
        ).exists())

    def test_last_non_event_completion_issues_once_without_event_progress(self):
        self._review_scenario()
        self._complete(self.homework, self.checklist)

        completion_service.mark_completed(self.user, self.lesson)
        completion_service.mark_completed(self.user, self.lesson)

        self.assertEqual(CourseCertificate.objects.filter(
            user=self.user, course=self.course,
        ).count(), 1)
        self.assert_event_incomplete()

    def test_final_received_review_issues_without_event_progress(self):
        own, received = self._review_scenario(received_complete=False)
        self._complete(self.lesson, self.homework, self.checklist)
        self.assertIsNone(PeerReviewService.check_certificate_eligibility(
            self.user, self.course, course_project=self.project,
        ))

        received.is_complete = True
        received.completed_at = timezone.now()
        received.save(update_fields=['is_complete', 'completed_at'])
        PeerReviewService.check_and_update_submission_status(own)

        self.assertEqual(CourseCertificate.objects.filter(
            user=self.user, course=self.course,
        ).count(), 1)
        self.assert_event_incomplete()

    def _dated_reviewed_attempt(self):
        today = timezone.localdate()
        dated_cohort = Cohort.objects.create(
            course=self.course, name='Dated', mode='cohort', is_active=True,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        now = timezone.now()
        dated_project = CourseProject.objects.create(
            course=self.course, cohort=dated_cohort, slug='dated-project',
            title='Dated project', submission_due_at=now,
            review_due_at=now + timedelta(days=1), peer_review_count=1,
        )
        dated_own = ProjectSubmission.objects.create(
            user=self.user, course=self.course, cohort=dated_cohort,
            course_project=dated_project, project_url='https://example.com/dated',
            status='review_complete',
        )
        dated_peer = User.objects.create_user(email='dated-peer@test.com')
        dated_peer_submission = ProjectSubmission.objects.create(
            user=dated_peer, course=self.course, cohort=dated_cohort,
            course_project=dated_project, project_url='https://example.com/dated-peer',
        )
        PeerReview.objects.create(
            submission=dated_own, reviewer=dated_peer, is_complete=True,
        )
        PeerReview.objects.create(
            submission=dated_peer_submission, reviewer=self.user, is_complete=True,
        )

    def test_final_review_uses_self_paced_attempt_not_earlier_dated_attempt(self):
        self._dated_reviewed_attempt()
        self_paced_own, final_review = self._review_scenario(received_complete=False)
        self._complete(self.lesson, self.homework, self.checklist)

        final_review.is_complete = True
        final_review.save(update_fields=['is_complete'])
        PeerReviewService.check_and_update_submission_status(self_paced_own)

        certificate = CourseCertificate.objects.get(user=self.user, course=self.course)
        self.assertEqual(certificate.submission, self_paced_own)
        self.assertEqual(CourseCertificate.objects.filter(user=self.user).count(), 1)
        self.assert_event_incomplete()


class ScheduledCertificateUnitRequirementsTest(TestCase):
    def _curriculum_and_user(self, attempt_kind):
        course = Course.objects.create(
            title=f'{attempt_kind} certificate',
            slug=f'{attempt_kind}-certificate-1979', peer_review_count=1,
        )
        module = Module.objects.create(
            course=course, title='Core', slug='core', sort_order=0,
        )
        lesson = Unit.objects.create(
            module=module, title='Lesson', slug='lesson', sort_order=0,
        )
        event = Unit.objects.create(
            module=module, title='Live session', slug='live-session',
            sort_order=1, kind='event', session_position=1,
        )
        user = User.objects.create_user(email=f'{attempt_kind}@test.com')
        self_paced = Cohort.objects.create(
            course=course, name='Unrelated Self-paced', mode='self_paced',
            is_active=True,
        )
        CohortEnrollment.objects.create(user=user, cohort=self_paced)
        return course, lesson, event, user

    def _review_scenario(self, course, user, project, cohort, attempt_kind):
        own = ProjectSubmission.objects.create(
            user=user, course=course, cohort=cohort, course_project=project,
            project_url='https://example.com/own', status='review_complete',
        )
        peer = User.objects.create_user(email=f'{attempt_kind}-peer@test.com')
        peer_submission = ProjectSubmission.objects.create(
            user=peer, course=course, cohort=cohort, course_project=project,
            project_url='https://example.com/peer', status='review_complete',
        )
        PeerReview.objects.create(submission=own, reviewer=peer, is_complete=True)
        PeerReview.objects.create(
            submission=peer_submission, reviewer=user, is_complete=True,
        )
        return own

    def _assert_event_required(self, attempt_kind):
        course, lesson, event, user = self._curriculum_and_user(attempt_kind)
        project, cohort = self._project_scope(course, attempt_kind)
        own = self._review_scenario(
            course, user, project, cohort, attempt_kind,
        )
        UserCourseProgress.objects.create(
            user=user, unit=lesson, completed_at=timezone.now(),
        )

        self.assertIsNone(PeerReviewService.check_certificate_eligibility(
            user, course, course_project=project,
        ))
        UserCourseProgress.objects.create(
            user=user, unit=event, completed_at=timezone.now(),
        )
        certificate = PeerReviewService.check_certificate_eligibility(
            user, course, course_project=project,
        )
        self.assertEqual(certificate.submission, own)

    def _project_scope(self, course, attempt_kind):
        if attempt_kind == 'legacy':
            return None, None
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=course, name='Dated', mode='cohort', is_active=True,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        now = timezone.now()
        project = CourseProject.objects.create(
            course=course, cohort=cohort, slug='project', title='Project',
            submission_due_at=now, review_due_at=now + timedelta(days=1),
            peer_review_count=1,
        )
        return project, cohort

    def test_dated_and_legacy_attempts_still_require_event_units(self):
        for attempt_kind in ('dated', 'legacy'):
            with self.subTest(attempt_kind=attempt_kind):
                self._assert_event_required(attempt_kind)
