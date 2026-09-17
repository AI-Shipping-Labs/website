"""Studio surfaces for the Maven ``tagging`` step and signup source (#1732)."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models.user import SIGNUP_SOURCE_MAVEN_WEBHOOK
from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import MAVEN_TAGS_NO_PREFIX_NOTE

User = get_user_model()


class MavenTaggingStepStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-1732@example.com", password="x", is_staff=True,
        )
        cls.enrollee = User.objects.create_user(
            email="enrollee-1732@example.com", password="x",
        )
        cls.event = MavenEnrollmentEvent.objects.create(
            dedupe_key="dedupe-1732",
            identity_hash="identity-1732",
            user=cls.enrollee,
            email=cls.enrollee.email,
            course="Buildcamp",
            cohort="4/11",
            course_key="from-rag-to-agents",
            cohort_key="4",
            event_type="user_cohort.enrolled",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            tagging_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            tagging_attempts=1,
            override_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_detail_lists_the_tagging_step_with_its_status_and_attempts(self):
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )
        steps = [step["name"] for step in response.context["steps"]]
        self.assertEqual(steps[0], "tagging")
        self.assertContains(response, 'data-testid="maven-step-note-tagging"')
        self.assertContains(response, "Attempts: 1")

    def test_detail_renders_the_unconfigured_prefix_note_verbatim(self):
        MavenEnrollmentEvent.objects.filter(pk=self.event.pk).update(
            tagging_error=MAVEN_TAGS_NO_PREFIX_NOTE,
        )
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )
        self.assertContains(response, MAVEN_TAGS_NO_PREFIX_NOTE)

    def test_list_shows_a_tagging_status_column(self):
        response = self.client.get(reverse("studio_maven_event_list"))
        self.assertContains(response, 'data-label="Tagging"')

    def test_retry_button_recovers_a_failed_tagging_step(self):
        MavenEnrollmentEvent.objects.filter(pk=self.event.pk).update(
            tagging_status=MavenEnrollmentEvent.STEP_FAILED,
            tagging_error="RuntimeError",
        )
        response = self.client.post(
            reverse("studio_maven_event_retry", args=[self.event.pk, "tagging"]),
            follow=True,
        )
        self.event.refresh_from_db()
        self.assertEqual(
            self.event.tagging_status, MavenEnrollmentEvent.STEP_SUCCEEDED
        )
        self.assertContains(response, "Maven tagging step recovered.")
        self.enrollee.refresh_from_db()
        self.assertEqual(
            sorted(self.enrollee.tags),
            ["ai-buildcamp", "ai-buildcamp-4", "maven"],
        )


class MavenWebhookSignupSourceStudioTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-source-1732@example.com", password="x", is_staff=True,
        )
        cls.enrollee = User.objects.create_user(
            email="source-1732@example.com",
            password="x",
            signup_source=SIGNUP_SOURCE_MAVEN_WEBHOOK,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_member_detail_labels_the_source_as_the_maven_webhook(self):
        response = self.client.get(
            reverse("studio_user_detail", args=[self.enrollee.pk])
        )
        self.assertContains(response, "Maven enrollment webhook")
        self.assertNotContains(response, "Bulk import (Stripe / CSV / course DB)")
