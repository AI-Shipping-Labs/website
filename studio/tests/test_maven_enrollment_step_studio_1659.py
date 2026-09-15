"""Studio surfaces for the Maven ``enrollment`` ledger step (issue #1659)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import STEP_NAMES

User = get_user_model()


class MavenEnrollmentStepStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-1659@example.com", password="x", is_staff=True,
        )
        cls.enrollee = User.objects.create_user(
            email="enrollee-1659@example.com", password="x",
        )
        cls.course = Course.objects.create(
            title="Buildcamp", slug="buildcamp-studio-1659",
            maven_course_key="from-rag-to-agents",
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, external_key="cohort-4", name="Cohort 4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        cls.event = MavenEnrollmentEvent.objects.create(
            dedupe_key="dedupe-1659",
            identity_hash="identity-1659",
            user=cls.enrollee,
            email=cls.enrollee.email,
            course="Buildcamp",
            cohort="Cohort 4",
            course_key="from-rag-to-agents",
            cohort_key="cohort-4",
            event_type="user_cohort.enrolled",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            enrollment_status=MavenEnrollmentEvent.STEP_PENDING,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_list_shows_enrollment_status_column(self):
        response = self.client.get(reverse("studio_maven_event_list"))
        self.assertContains(response, "Enrollment")
        self.assertContains(response, 'data-testid="maven-occurrence-list"')

    def test_detail_shows_the_raw_course_and_cohort_keys(self):
        # Issue #1659 PM follow-up: the raw course_key/cohort_key strings
        # must be visible on the page an operator opens first, since a key
        # mismatch is diagnosed by comparing these exact values against the
        # real Maven webhook payload.
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )
        self.assertContains(response, 'data-testid="maven-occurrence-keys"')
        self.assertContains(response, "from-rag-to-agents")
        self.assertContains(response, "cohort-4")

    def test_detail_shows_and_retries_enrollment_step(self):
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )
        self.assertEqual(
            [s["name"] for s in response.context["steps"]], list(STEP_NAMES),
        )
        self.assertContains(
            response,
            reverse("studio_maven_event_retry", args=[self.event.pk, "enrollment"]),
        )

        retry_response = self.client.post(
            reverse("studio_maven_event_retry", args=[self.event.pk, "enrollment"])
        )
        self.assertEqual(retry_response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.enrollment_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(user=self.enrollee, course=self.course).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=self.enrollee).exists()
        )
