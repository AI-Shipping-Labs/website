"""Maven ``enrollment`` ledger step + removal revocation coverage (issue #1659)."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import TierOverride
from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from events.models import EventSeries, SeriesRegistration
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import (
    STEP_NAMES,
    MavenUnknownCohortError,
    MavenUnknownCourseError,
    resolve_maven_cohort,
    resolve_maven_course,
    retry_occurrence_step,
    run_occurrence_steps,
)
from payments.models import Tier

User = get_user_model()


class MavenEnrollmentStepOrderingTest(TestCase):
    def test_step_names_order(self):
        self.assertEqual(
            STEP_NAMES,
            ("override", "enrollment", "notification", "slack", "welcome", "removal"),
        )


class _MavenFixtureMixin:
    def _course(self, maven_course_key="from-rag-to-agents", **fields):
        return Course.objects.create(
            title="Buildcamp", slug=f"buildcamp-{fields.get('slug_suffix', '1')}",
            maven_course_key=maven_course_key,
        )

    def _cohort(self, course, external_key="cohort-4", **fields):
        defaults = {
            "name": "Cohort 4",
            "start_date": "2026-09-21",
            "end_date": "2026-11-22",
        }
        defaults.update(fields)
        return Cohort.objects.create(
            course=course, external_key=external_key, **defaults,
        )

    def _user(self, email="member@example.com"):
        return User.objects.create_user(email=email, password="pw")

    def _occurrence(self, user, *, course_key, cohort_key, key, **fields):
        defaults = {
            "override_status": MavenEnrollmentEvent.STEP_SUCCEEDED,
            "enrollment_status": MavenEnrollmentEvent.STEP_PENDING,
            "notification_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "slack_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "welcome_status": MavenEnrollmentEvent.STEP_SKIPPED,
        }
        defaults.update(fields)
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key,
            identity_hash=key,
            user=user,
            course_key=course_key,
            cohort_key=cohort_key,
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            event_type="user_cohort.enrolled",
            **defaults,
        )


class MavenEnrollmentStepResolutionTest(_MavenFixtureMixin, TestCase):
    def setUp(self):
        self.course = self._course()
        self.cohort = self._cohort(self.course)
        self.user = self._user()

    def test_resolves_case_insensitively(self):
        resolved = resolve_maven_course("From-Rag-To-Agents")
        self.assertEqual(resolved.pk, self.course.pk)
        resolved_cohort = resolve_maven_cohort(self.course, "COHORT-4")
        self.assertEqual(resolved_cohort.pk, self.cohort.pk)

    def test_blank_course_key_is_unresolvable(self):
        with self.assertRaises(MavenUnknownCourseError):
            resolve_maven_course("")

    def test_unmatched_course_key_is_unresolvable(self):
        with self.assertRaises(MavenUnknownCourseError):
            resolve_maven_course("no-such-course")

    def test_blank_maven_course_key_rows_never_match(self):
        Course.objects.create(title="Unconfigured", slug="unconfigured-1659")
        with self.assertRaises(MavenUnknownCourseError):
            resolve_maven_course("")

    def test_unmatched_cohort_key_is_unresolvable(self):
        with self.assertRaises(MavenUnknownCohortError):
            resolve_maven_cohort(self.course, "no-such-cohort")

    def test_cohort_lookup_is_scoped_to_course(self):
        other_course = self._course(maven_course_key="other-course", slug_suffix="2")
        self._cohort(other_course, external_key="cohort-4", name="Other cohort")
        # Same external_key, different course — must not cross-resolve.
        resolved = resolve_maven_cohort(self.course, "cohort-4")
        self.assertEqual(resolved.pk, self.cohort.pk)


class MavenEnrollmentStepGrantTest(_MavenFixtureMixin, TestCase):
    def setUp(self):
        self.course = self._course()
        self.cohort = self._cohort(self.course)
        self.user = self._user()

    def test_resolvable_keys_grant_course_access_and_cohort_enrollment(self):
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="grant-1",
        )
        result = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.user, course=self.course, access_type="granted",
            ).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(
                cohort=self.cohort, user=self.user,
            ).exists()
        )

    def test_idempotent_no_duplicate_rows_on_repeat(self):
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="grant-idempotent",
        )
        retry_occurrence_step(occurrence, "enrollment")
        occurrence.refresh_from_db()
        occurrence.enrollment_status = MavenEnrollmentEvent.STEP_PENDING
        occurrence.save(update_fields=["enrollment_status"])
        retry_occurrence_step(occurrence, "enrollment")

        self.assertEqual(
            CourseAccess.objects.filter(user=self.user, course=self.course).count(), 1,
        )
        self.assertEqual(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=self.user).count(), 1,
        )

    def test_existing_purchased_access_is_not_overwritten(self):
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type="purchased",
        )
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="grant-purchased",
        )
        retry_occurrence_step(occurrence, "enrollment")
        access = CourseAccess.objects.get(user=self.user, course=self.course)
        self.assertEqual(access.access_type, "purchased")

    def test_unresolvable_course_key_fails_with_safe_exception_class(self):
        occurrence = self._occurrence(
            self.user, course_key="no-such-course", cohort_key="cohort-4",
            key="fail-course",
        )
        result = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_FAILED)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.enrollment_error, "MavenUnknownCourseError")
        self.assertFalse(
            CourseAccess.objects.filter(user=self.user, course=self.course).exists()
        )

    def test_unresolvable_cohort_key_fails_with_safe_exception_class(self):
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="no-such-cohort",
            key="fail-cohort",
        )
        result = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_FAILED)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.enrollment_error, "MavenUnknownCohortError")

    def test_retry_recovers_once_keys_are_configured(self):
        occurrence = self._occurrence(
            self.user, course_key="not-yet-configured", cohort_key="cohort-4",
            key="recover-1",
        )
        first = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(first.outcome, MavenEnrollmentEvent.STEP_FAILED)

        self.course.maven_course_key = "not-yet-configured"
        self.course.save(update_fields=["maven_course_key"])
        occurrence.refresh_from_db()
        occurrence.enrollment_status = MavenEnrollmentEvent.STEP_PENDING
        occurrence.save(update_fields=["enrollment_status"])

        second = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(second.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(user=self.user, course=self.course).exists()
        )

    def test_force_retry_recovers_skipped_enrollment_step(self):
        # The #1659 step migration backfilled pre-existing occurrences with
        # an enrollment_status of SKIPPED, and the five-minute scheduled
        # recovery job targets pending/failed/running only. The force-retry
        # path is how those rows get their grant once course.yaml declares
        # the matching keys (website #1662 roster replay, checklist step 10).
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="skipped-recovery",
            enrollment_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        result = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(user=self.user, course=self.course).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(user=self.user, cohort=self.cohort).exists()
        )

    def test_force_retry_of_skipped_welcome_stays_not_retryable(self):
        # Only the enrollment step may leave the skipped state under force:
        # a preference-suppressed welcome must never re-send member-visible
        # email.
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="skipped-welcome",
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        result = retry_occurrence_step(occurrence, "welcome")
        self.assertEqual(result.reason, "not_retryable")
        self.assertFalse(result.attempted)

    def test_force_retry_recovers_skipped_enrollment_step(self):
        # The #1659 step migration backfilled pre-existing occurrences with
        # an enrollment_status of SKIPPED, and the five-minute scheduled
        # recovery job targets pending/failed/running only. The force-retry
        # path is how those rows get their grant once course.yaml declares
        # the matching keys (website #1662 roster replay, checklist step 10).
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="skipped-recovery",
            enrollment_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        result = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(user=self.user, course=self.course).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(user=self.user, cohort=self.cohort).exists()
        )

    def test_force_retry_of_skipped_welcome_stays_not_retryable(self):
        # Only the enrollment step may leave the skipped state under force:
        # a preference-suppressed welcome must never re-send member-visible
        # email.
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="skipped-welcome",
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        result = retry_occurrence_step(occurrence, "welcome")
        self.assertEqual(result.reason, "not_retryable")
        self.assertFalse(result.attempted)

    def test_scheduled_recovery_job_picks_up_pending_enrollment_step(self):
        # jobs.tasks.cleanup.retry_maven_enrollment_steps is the five-minute
        # scheduled recovery job; it must retry an incomplete ``enrollment``
        # step the same way it already retries the other five steps.
        from jobs.tasks.cleanup import retry_maven_enrollment_steps

        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="scheduled-recovery",
            notification_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        result = retry_maven_enrollment_steps()
        self.assertEqual(result["processed"], 1)
        occurrence.refresh_from_db()
        self.assertEqual(
            occurrence.enrollment_status, MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

    def test_new_active_occurrence_starts_enrollment_pending_regardless_of_membership(self):
        from integrations.services.maven import _handle_enrolled

        payload = {
            "event": "user_cohort.enrolled", "email": "fresh-1659@example.com",
            "course": "Buildcamp", "cohort": "Cohort 4",
        }
        _handle_enrolled(
            payload, "fresh-1659@example.com", "Buildcamp", "Cohort 4",
            "buildcamp-course-key", "cohort-key-x", "identity-hash-fresh-1659",
        )
        occurrence = MavenEnrollmentEvent.objects.get(identity_hash="identity-hash-fresh-1659")
        # enrollment must have at least been attempted (it starts pending,
        # not skipped) — override succeeding drives the ordinary run.
        self.assertNotEqual(occurrence.enrollment_status, MavenEnrollmentEvent.STEP_SKIPPED)


class MavenEnrollmentStepSeriesRegistrationTest(_MavenFixtureMixin, TestCase):
    def setUp(self):
        self.course = self._course()
        self.cohort = self._cohort(self.course)
        self.user = self._user()

    def test_no_linked_series_is_a_clean_noop(self):
        occurrence = self._occurrence(
            self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
            key="no-series",
        )
        result = retry_occurrence_step(occurrence, "enrollment")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(SeriesRegistration.objects.count(), 0)

    def test_linked_series_creates_standing_registration(self):
        series = EventSeries.objects.create(name="Office hours", slug="office-hours-1659")
        # Simulate #1660's Cohort.event_series field defensively, without
        # depending on that migration having landed in this worktree.
        self.cohort.event_series_id = series.pk
        self.cohort.event_series = series

        with patch(
            "integrations.services.maven.resolve_maven_cohort",
            return_value=self.cohort,
        ):
            occurrence = self._occurrence(
                self.user, course_key="from-rag-to-agents", cohort_key="cohort-4",
                key="with-series",
            )
            result = retry_occurrence_step(occurrence, "enrollment")

        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            SeriesRegistration.objects.filter(series=series, user=self.user).exists()
        )


class MavenEnrollmentStepNonBlockingTest(_MavenFixtureMixin, TestCase):
    def test_failed_enrollment_does_not_block_notification_slack_welcome(self):
        user = self._user("nonblock-1659@example.com")
        occurrence = self._occurrence(
            user, course_key="unmatched-key", cohort_key="unmatched-key",
            key="nonblocking-1", override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            enrollment_status=MavenEnrollmentEvent.STEP_PENDING,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
            slack_status=MavenEnrollmentEvent.STEP_PENDING,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        run_occurrence_steps(occurrence)
        occurrence.refresh_from_db()

        self.assertEqual(occurrence.enrollment_status, MavenEnrollmentEvent.STEP_FAILED)
        self.assertGreaterEqual(occurrence.notification_attempts, 1)
        self.assertGreaterEqual(occurrence.slack_attempts, 1)
        self.assertGreaterEqual(occurrence.welcome_attempts, 1)


class MavenRemovalRevocationTest(_MavenFixtureMixin, TestCase):
    def setUp(self):
        self.course = self._course()
        self.cohort = self._cohort(self.course)
        self.user = self._user("removal-1659@example.com")

    def _grant(self, occurrence):
        CourseAccess.objects.get_or_create(
            user=self.user, course=self.course, defaults={"access_type": "granted"},
        )
        CohortEnrollment.objects.get_or_create(cohort=self.cohort, user=self.user)

    def _removed_occurrence(
        self, key, *, course_key="from-rag-to-agents", cohort_key="cohort-4", **fields,
    ):
        defaults = {
            "override_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "notification_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "slack_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "welcome_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "enrollment_status": MavenEnrollmentEvent.STEP_SUCCEEDED,
            "removal_status": MavenEnrollmentEvent.STEP_PENDING,
        }
        defaults.update(fields)
        occurrence = MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, user=self.user,
            course_key=course_key, cohort_key=cohort_key,
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED,
            event_type="user_cohort.removed",
            **defaults,
        )
        return occurrence

    def test_removal_deletes_cohort_enrollment_unconditionally(self):
        occurrence = self._removed_occurrence("removal-cohort")
        self._grant(occurrence)
        retry_occurrence_step(occurrence, "removal")
        self.assertFalse(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=self.user).exists()
        )

    def test_removal_deletes_course_access_when_no_other_active_occurrence(self):
        occurrence = self._removed_occurrence("removal-course-access")
        self._grant(occurrence)
        retry_occurrence_step(occurrence, "removal")
        self.assertFalse(
            CourseAccess.objects.filter(
                user=self.user, course=self.course, access_type="granted",
            ).exists()
        )

    def test_removal_keeps_course_access_when_another_active_occurrence_grants_it(self):
        # A second, still-active occurrence for the same resolved course
        # under a different cohort.
        MavenEnrollmentEvent.objects.create(
            dedupe_key="still-active-cohort5", identity_hash="still-active-cohort5",
            user=self.user, course_key="from-rag-to-agents", cohort_key="cohort-5",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            event_type="user_cohort.enrolled",
        )
        occurrence = self._removed_occurrence("removal-two-cohorts")
        self._grant(occurrence)
        retry_occurrence_step(occurrence, "removal")
        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.user, course=self.course, access_type="granted",
            ).exists()
        )
        # The cohort-4 enrollment itself is still gone unconditionally.
        self.assertFalse(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=self.user).exists()
        )

    def test_removal_never_touches_purchased_course_access(self):
        occurrence = self._removed_occurrence("removal-purchased")
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type="purchased",
        )
        retry_occurrence_step(occurrence, "removal")
        self.assertTrue(
            CourseAccess.objects.filter(
                user=self.user, course=self.course, access_type="purchased",
            ).exists()
        )

    def test_removal_deletes_linked_series_registration(self):
        series = EventSeries.objects.create(name="Office hours", slug="office-hours-removal-1659")
        self.cohort.event_series_id = series.pk
        self.cohort.event_series = series
        SeriesRegistration.objects.create(series=series, user=self.user)

        occurrence = self._removed_occurrence("removal-series")
        self._grant(occurrence)
        with patch(
            "integrations.services.maven.resolve_maven_cohort",
            return_value=self.cohort,
        ):
            retry_occurrence_step(occurrence, "removal")

        self.assertFalse(
            SeriesRegistration.objects.filter(series=series, user=self.user).exists()
        )

    def test_removal_never_touches_tier_override_or_slack_field(self):
        tier = Tier.objects.get(slug="main")
        from datetime import timedelta

        from django.utils import timezone

        override = TierOverride.objects.create(
            user=self.user, original_tier=self.user.membership.tier,
            override_tier=tier, expires_at=timezone.now() + timedelta(days=365),
            is_active=True, source="maven:removal-1659-source",
        )
        self.user.slack_member = True
        self.user.save(update_fields=["slack_member"])

        occurrence = self._removed_occurrence("removal-override-slack")
        self._grant(occurrence)
        retry_occurrence_step(occurrence, "removal")

        override.refresh_from_db()
        self.user.refresh_from_db()
        self.assertTrue(override.is_active)
        self.assertTrue(self.user.slack_member)

    def test_removal_with_unresolvable_keys_is_best_effort_and_still_succeeds(self):
        occurrence = self._removed_occurrence(
            "removal-unresolvable", course_key="gone-course-key", cohort_key="gone-cohort-key",
        )
        result = retry_occurrence_step(occurrence, "removal")
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
