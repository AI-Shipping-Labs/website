"""Studio surfaces why an enrollee never reached Slack (issue #1565, #1665).

Support has to be able to read the outcome off the occurrence page alone:
a step that could not run reads ``skipped`` with a reason, never
``succeeded``, and the reason is not mislabelled as an error. The same is
true of a step that DID succeed with a note attached — a self-healed
``slack`` row (issue #1665) must never render its confirmation under a
"Last error" label, which is exactly the page an operator reads to confirm
production remediation.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import (
    SLACK_JOIN_LINK_DELIVERED_NOTE,
    SLACK_JOIN_LINK_SUPPRESSED_NOTE,
)

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
        # Issue #1665: ``slack`` mirrors ``welcome_status`` — a
        # preference-suppressed welcome (skipped) means no join link was
        # delivered, so slack is also recorded skipped with that reason.
        cls.event = MavenEnrollmentEvent.objects.create(
            dedupe_key="dedupe-1565",
            identity_hash="identity-1565",
            user=cls.enrollee,
            email=cls.enrollee.email,
            course="Buildcamp",
            cohort="Cohort 1",
            event_type="user_cohort.enrolled",
            slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
            slack_error=SLACK_JOIN_LINK_SUPPRESSED_NOTE,
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_skipped_slack_step_shows_the_reason_not_an_error_label(self):
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )

        note = next(s for s in response.context["steps"] if s["name"] == "slack")
        self.assertEqual(note["status"], MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertContains(response, "Join link not delivered")
        self.assertContains(response, "maven_emails preference")
        self.assertContains(
            response,
            f"Reason: {escape(SLACK_JOIN_LINK_SUPPRESSED_NOTE)}",
            html=False,
        )
        self.assertNotContains(
            response, f"Last error: {escape(SLACK_JOIN_LINK_SUPPRESSED_NOTE)}",
        )


class MavenOccurrenceSucceededNoteTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-1665@example.com", password="x", is_staff=True,
        )
        cls.enrollee = User.objects.create_user(
            email="enrollee-1665@example.com", password="x",
        )
        # A self-healed row (issue #1665): welcome succeeded, so slack
        # mirrors it and carries a success note, not an error.
        cls.event = MavenEnrollmentEvent.objects.create(
            dedupe_key="dedupe-1665-succeeded",
            identity_hash="identity-1665-succeeded",
            user=cls.enrollee,
            email=cls.enrollee.email,
            course="Buildcamp",
            cohort="Cohort 1",
            event_type="user_cohort.enrolled",
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            slack_error=SLACK_JOIN_LINK_DELIVERED_NOTE,
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_succeeded_slack_step_shows_a_note_not_an_error_label(self):
        response = self.client.get(
            reverse("studio_maven_event_detail", args=[self.event.pk])
        )

        note = next(s for s in response.context["steps"] if s["name"] == "slack")
        self.assertEqual(note["status"], MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertContains(response, "Join link delivered")
        self.assertContains(
            response,
            f"Note: {escape(SLACK_JOIN_LINK_DELIVERED_NOTE)}",
            html=False,
        )
        self.assertNotContains(
            response, f"Last error: {escape(SLACK_JOIN_LINK_DELIVERED_NOTE)}",
        )
