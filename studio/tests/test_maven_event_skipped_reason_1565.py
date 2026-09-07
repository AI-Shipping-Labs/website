"""Studio surfaces why an enrollee never reached Slack (issue #1565).

Support has to be able to read the outcome off the occurrence page alone:
a step that could not run reads ``skipped`` with a reason, never
``succeeded``, and the reason is not mislabelled as an error.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import SLACK_NOT_IN_WORKSPACE_NOTE

User = get_user_model()


class MavenOccurrenceSkippedReasonTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-1565@example.com", password="x", is_staff=True,
        )
        cls.enrollee = User.objects.create_user(
            email="enrollee-1565@example.com", password="x",
        )
        cls.event = MavenEnrollmentEvent.objects.create(
            dedupe_key="dedupe-1565",
            identity_hash="identity-1565",
            user=cls.enrollee,
            email=cls.enrollee.email,
            course="Buildcamp",
            cohort="Cohort 1",
            event_type="user_cohort.enrolled",
            slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
            slack_error=SLACK_NOT_IN_WORKSPACE_NOTE,
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_skipped_slack_step_shows_the_reason_not_an_error_label(self):
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )

        note = response.context["steps"][2]
        self.assertEqual(note["name"], "slack")
        self.assertEqual(note["status"], MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertContains(response, "not in the Slack workspace")
        self.assertContains(response, "the join link was delivered in")
        self.assertContains(
            response,
            f"Reason: {SLACK_NOT_IN_WORKSPACE_NOTE}",
            html=False,
        )
        self.assertNotContains(
            response, f"Last error: {SLACK_NOT_IN_WORKSPACE_NOTE}",
        )
