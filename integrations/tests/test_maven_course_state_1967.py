"""Maven course state and self-paced mapping regressions for issue #1967."""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, CourseAccess, Enrollment
from content.models.enrollment import SOURCE_MAVEN
from content.services.course_announcements import show_announcements_tab
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import (
    _previous_cohort_enrollment,
    retry_occurrence_step,
)
from integrations.tests.test_maven_enrollment_step_1659 import _MavenFixtureMixin
from payments.models import Tier


class MavenCourseStateTest(_MavenFixtureMixin, TestCase):
    def setUp(self):
        self.course = self._course()
        self.course.access_mode = 'entitlement'
        self.course.required_level = 20
        self.course.save(update_fields=['access_mode', 'required_level'])
        self.cohort = self._cohort(self.course)
        self.user = self._user('state-1967@example.com')

    def _active_occurrence(self, key='active-1967'):
        return self._occurrence(
            self.user, course_key='from-rag-to-agents',
            cohort_key='cohort-4', key=key,
        )

    def _removed_occurrence(self, key='removed-1967', cohort_key='cohort-4'):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, user=self.user,
            course_key='from-rag-to-agents', cohort_key=cohort_key,
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED,
            event_type='user_cohort.removed',
            enrollment_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            removal_status=MavenEnrollmentEvent.STEP_PENDING,
        )

    def _run_removal(self, occurrence):
        with patch(
            'community.services.staff_notifications.notify_maven_cohort_removal',
        ) as staff_notice, patch(
            'integrations.services.maven._send_removal_student_email',
            return_value=None,
        ):
            retry_occurrence_step(occurrence, 'removal')
        return staff_notice.call_args.kwargs['outcome']

    def test_enrollment_creates_three_idempotent_state_records(self):
        occurrence = self._active_occurrence()
        retry_occurrence_step(occurrence, 'enrollment')
        occurrence.enrollment_status = MavenEnrollmentEvent.STEP_PENDING
        occurrence.save(update_fields=['enrollment_status'])
        retry_occurrence_step(occurrence, 'enrollment')

        self.assertEqual(CourseAccess.objects.filter(
            user=self.user, course=self.course,
        ).count(), 1)
        enrollment = Enrollment.objects.get(
            user=self.user, course=self.course, unenrolled_at__isnull=True,
        )
        self.assertEqual(enrollment.source, SOURCE_MAVEN)
        self.assertEqual(CohortEnrollment.objects.filter(
            user=self.user, cohort=self.cohort,
        ).count(), 1)

    def test_enrollment_reactivates_history_but_preserves_active_manual_source(self):
        Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MAVEN,
            unenrolled_at=timezone.now(),
        )
        retry_occurrence_step(self._active_occurrence('reactivate'), 'enrollment')
        active = Enrollment.objects.get(
            user=self.user, course=self.course, unenrolled_at__isnull=True,
        )
        self.assertEqual(active.source, SOURCE_MAVEN)

        active.unenrolled_at = timezone.now()
        active.save(update_fields=['unenrolled_at'])
        manual = Enrollment.objects.create(user=self.user, course=self.course)
        retry_occurrence_step(self._active_occurrence('manual'), 'enrollment')
        manual.refresh_from_db()
        self.assertEqual(manual.source, 'manual')
        self.assertIsNone(manual.unenrolled_at)

    def test_inactive_exact_cohort_mapping_is_retryable_without_side_effects(self):
        self.cohort.is_active = False
        self.cohort.save(update_fields=['is_active'])
        occurrence = self._active_occurrence('inactive')

        result = retry_occurrence_step(occurrence, 'enrollment')

        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_FAILED)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.enrollment_error, 'MavenUnknownCohortError')
        self.assertFalse(CourseAccess.objects.filter(user=self.user).exists())
        self.assertFalse(Enrollment.objects.filter(user=self.user).exists())
        self.assertFalse(CohortEnrollment.objects.filter(user=self.user).exists())

    def test_removal_ends_only_maven_owned_enrollment(self):
        for source in (SOURCE_MAVEN, 'manual'):
            Enrollment.objects.filter(user=self.user).delete()
            CourseAccess.objects.update_or_create(
                user=self.user, course=self.course,
                defaults={'access_type': 'granted'},
            )
            CohortEnrollment.objects.get_or_create(
                user=self.user, cohort=self.cohort,
            )
            enrollment = Enrollment.objects.create(
                user=self.user, course=self.course, source=source,
            )
            self._run_removal(self._removed_occurrence(f'remove-{source}'))
            enrollment.refresh_from_db()
            if source == SOURCE_MAVEN:
                self.assertIsNotNone(enrollment.unenrolled_at)
            else:
                self.assertIsNone(enrollment.unenrolled_at)

    def test_removal_retains_maven_enrollment_with_purchased_access(self):
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='purchased',
        )
        enrollment = Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MAVEN,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)

        self._run_removal(self._removed_occurrence('purchased'))

        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.unenrolled_at)

    def test_open_course_readability_does_not_retain_maven_enrollment(self):
        self.course.access_mode = 'tier'
        self.course.required_level = 0
        self.course.save(update_fields=['access_mode', 'required_level'])
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='granted',
        )
        enrollment = Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MAVEN,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)

        outcome = self._run_removal(self._removed_occurrence('open-course'))

        enrollment.refresh_from_db()
        self.assertIsNotNone(enrollment.unenrolled_at)
        self.assertFalse(CourseAccess.objects.filter(
            user=self.user, course=self.course,
        ).exists())
        self.assertIn(
            f'Revoked access to {self.course.title}.',
            outcome['summary_lines'],
        )

    def test_staff_owned_course_grant_survives_maven_removal(self):
        actor = self._user('staff-grant-1967@example.com')
        actor.is_staff = True
        actor.save(update_fields=['is_staff'])
        access = CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='granted',
            granted_by=actor,
        )
        enrollment = Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MAVEN,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)

        outcome = self._run_removal(self._removed_occurrence('staff-grant'))

        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.unenrolled_at)
        self.assertTrue(CourseAccess.objects.filter(pk=access.pk).exists())
        self.assertIn(
            'Kept course access: they have an independent course grant.',
            outcome['summary_lines'],
        )

    def test_tier_course_retains_enrollment_with_effective_membership_access(self):
        self.course.access_mode = 'tier'
        self.course.required_level = 20
        self.course.save(update_fields=['access_mode', 'required_level'])
        membership = self.user.membership
        membership.tier = Tier.objects.get(slug='main')
        membership.save(update_fields=['tier'])
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='granted',
        )
        enrollment = Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MAVEN,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)

        outcome = self._run_removal(
            self._removed_occurrence('membership-retained'),
        )

        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.unenrolled_at)
        self.assertIn(
            'Kept course access: their membership still includes this course.',
            outcome['summary_lines'],
        )

    def test_same_course_transfer_keeps_course_enrollment_active(self):
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type='granted',
        )
        enrollment = Enrollment.objects.create(
            user=self.user, course=self.course, source=SOURCE_MAVEN,
        )
        CohortEnrollment.objects.create(user=self.user, cohort=self.cohort)
        self._occurrence(
            self.user, course_key='from-rag-to-agents',
            cohort_key='cohort-5', key='transfer-active',
        )

        self._run_removal(self._removed_occurrence('transfer-removed'))

        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.unenrolled_at)

    def test_enrollment_controls_announcement_audience_across_removal(self):
        occurrence = self._active_occurrence('announcement-grant')
        retry_occurrence_step(occurrence, 'enrollment')
        self.assertTrue(show_announcements_tab(self.user, self.course))
        self._run_removal(self._removed_occurrence('announcement-remove'))
        self.assertFalse(show_announcements_tab(self.user, self.course))

    def test_undated_removal_finds_prior_dated_alumnus(self):
        alumnus = self._cohort(
            self.course, external_key='cohort-2', name='Cohort 2',
            start_date='2025-09-01', end_date='2025-11-01',
        )
        rolling = CohortEnrollment.objects.create(
            user=self.user,
            cohort=Cohort.objects.create(
                course=self.course,
                external_key='self-paced', name='Self-paced',
                mode='self_paced', is_active=True,
            ),
        )
        CohortEnrollment.objects.create(user=self.user, cohort=alumnus)
        occurrence = self._removed_occurrence(
            'undated-alumnus', cohort_key=rolling.cohort.external_key,
        )

        previous = _previous_cohort_enrollment(occurrence)

        self.assertEqual(previous.cohort, alumnus)
