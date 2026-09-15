"""Operator API coverage for the Maven ``enrollment`` step (issue #1659)."""

from api.tests.test_maven_occurrences_1568 import (
    COLLECTION_URL,
    MavenOccurrenceApiTestBase,
)
from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from integrations.models import MavenEnrollmentEvent


class MavenEnrollmentStepApiTest(MavenOccurrenceApiTestBase):
    def _course_cohort(self):
        course = Course.objects.create(
            title="Buildcamp", slug="buildcamp-api-1659",
            maven_course_key="from-rag-to-agents",
        )
        cohort = Cohort.objects.create(
            course=course, external_key="cohort-4", name="Cohort 4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        return course, cohort

    def test_detail_includes_course_access_granted_and_cohort_enrolled_true(self):
        course, cohort = self._course_cohort()
        CourseAccess.objects.create(
            user=self.member, course=course, access_type="granted",
        )
        CohortEnrollment.objects.create(cohort=cohort, user=self.member)
        occurrence = self.event(
            "detail-grant-flags",
            user=self.member,
            course_key="from-rag-to-agents",
            cohort_key="cohort-4",
        )

        body = self.client.get(self.detail_url(occurrence), **self.auth()).json()
        self.assertTrue(body["course_access_granted"])
        self.assertTrue(body["cohort_enrolled"])

    def test_detail_reports_false_when_keys_do_not_resolve(self):
        occurrence = self.event(
            "detail-grant-flags-unresolved",
            user=self.member,
            course_key="no-such-course",
            cohort_key="no-such-cohort",
        )
        body = self.client.get(self.detail_url(occurrence), **self.auth()).json()
        self.assertFalse(body["course_access_granted"])
        self.assertFalse(body["cohort_enrolled"])

    def test_enrollment_appears_in_failed_steps_and_needs_attention(self):
        occurrence = self.event(
            "enrollment-failed-steps",
            user=self.member,
            enrollment_status=MavenEnrollmentEvent.STEP_FAILED,
            enrollment_error="MavenUnknownCourseError",
        )
        body = self.client.get(self.detail_url(occurrence), **self.auth()).json()
        self.assertIn("enrollment", body["failed_steps"])

    def test_failed_step_filter_accepts_enrollment(self):
        failing = self.event(
            "enrollment-filter-failing", user=self.member,
            enrollment_status=MavenEnrollmentEvent.STEP_FAILED,
        )
        self.event(
            "enrollment-filter-ok", user=self.member,
            enrollment_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        response = self.client.get(
            f"{COLLECTION_URL}?failed_step=enrollment", **self.auth()
        )
        ids = [row["id"] for row in response.json()["occurrences"]]
        self.assertEqual(ids, [failing.pk])

    def test_retry_enrollment_step_succeeds_once_keys_resolve(self):
        course, cohort = self._course_cohort()
        occurrence = self.event(
            "retry-enrollment",
            user=self.member,
            course_key="from-rag-to-agents",
            cohort_key="cohort-4",
            enrollment_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        response = self.client.post(
            self.retry_url(occurrence, "enrollment"), **self.auth()
        )
        self.assertEqual(response.json()["retry"]["outcome"], "succeeded")
        self.assertTrue(
            CourseAccess.objects.filter(user=self.member, course=course).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(cohort=cohort, user=self.member).exists()
        )
