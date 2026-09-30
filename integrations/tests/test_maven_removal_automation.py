"""Maven removal automation: override revoke, student email, real-outcome staff summary.

A ``user_cohort.removed`` occurrence's ``removal`` step makes every automatic
change first (course access, cohort enrollment, tags, the Maven-granted tier
override), emails the removed student, and only then sends staff a summary
built from what actually happened. ``reapply_removal`` re-runs it for
removals processed before this existed.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from community.models import CommunityAuditLog
from community.services.staff_notifications import _build_removal_slack_text
from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from email_app.testing import StubSESClient
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import (
    reapply_removal,
    retry_occurrence_step,
)
from payments.models import Tier, TierOverride

User = get_user_model()

COURSE_KEY = "from-rag-to-agents"
NOTIFY = "community.services.staff_notifications.notify_maven_cohort_removal"


def configure(**values):
    for key, value in values.items():
        IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})
    clear_config_cache()


def _drain(delivery):
    """Deliver one delivery through the real worker; return (subject, html)."""
    stub = StubSESClient()
    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    simple = stub.calls[0]["Content"]["Simple"]
    return simple["Subject"]["Data"], simple["Body"]["Html"]["Data"]


class MavenRemovalAutomationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main = Tier.objects.get(slug="main")
        cls.course = Course.objects.create(
            title="AI Engineering Buildcamp", slug="buildcamp-removal",
            maven_course_key=COURSE_KEY,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, external_key="cohort-5", name="Cohort 5",
            start_date="2026-09-21", end_date="2026-11-22",
        )

    def setUp(self):
        self.addCleanup(clear_config_cache)
        self.user = User.objects.create_user(
            email="refunded@example.com", first_name="Ada",
        )
        CourseAccess.objects.create(
            user=self.user, course=self.course, access_type="granted",
        )
        CohortEnrollment.objects.create(cohort=self.cohort, user=self.user)
        self.maven_override = self._override(source="maven:removed-hash")

    def _override(self, *, source, granted_by=None):
        return TierOverride.objects.create(
            user=self.user, original_tier=self.user.membership.tier,
            override_tier=self.main,
            expires_at=timezone.now() + timedelta(days=1800),
            is_active=True, source=source, granted_by=granted_by,
        )

    def _occurrence(self, key, *, lifecycle, cohort_key="cohort-5", **fields):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, user=self.user,
            email=self.user.email, course="AI Engineering Buildcamp",
            cohort=cohort_key, course_key=COURSE_KEY, cohort_key=cohort_key,
            lifecycle=lifecycle, event_type="user_cohort.removed",
            removal_status=MavenEnrollmentEvent.STEP_PENDING, **fields,
        )

    def _removed(self, key="removed-hash"):
        return self._occurrence(key, lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED)

    def _student_deliveries(self):
        return EmailDelivery.objects.filter(purpose="maven_removal")

    @patch(NOTIFY)
    def test_removal_revokes_maven_override_and_reports_real_outcomes(self, notify):
        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertFalse(self.maven_override.is_active)
        audit = CommunityAuditLog.objects.get(
            user=self.user, action="tier_override_revoked",
        )
        self.assertIn("actor=maven_removal", audit.details)
        self.assertFalse(CourseAccess.objects.filter(user=self.user).exists())

        outcome = notify.call_args.kwargs["outcome"]
        self.assertTrue(outcome["override_result"].startswith("Main access revoked"))
        self.assertEqual(
            outcome["course_access_result"],
            "Revoked access to AI Engineering Buildcamp.",
        )
        self.assertEqual(outcome["cohort_enrollment_result"], "Removed from cohort cohort-5.")
        self.assertEqual(outcome["student_email_result"], "Sent the maven_removal email.")
        self.assertEqual(self._student_deliveries().count(), 1)

    @patch(NOTIFY)
    def test_override_and_email_kept_while_another_cohort_is_active(self, notify):
        self._occurrence(
            "still-active", lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            cohort_key="cohort-6",
        )

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertTrue(self.maven_override.is_active)
        outcome = notify.call_args.kwargs["outcome"]
        self.assertEqual(
            outcome["override_result"],
            "Kept: still active in Maven cohort cohort-6 (AI Engineering Buildcamp).",
        )
        self.assertTrue(outcome["student_email_result"].startswith("Not sent: still active"))
        self.assertFalse(self._student_deliveries().exists())

    @patch(NOTIFY)
    def test_staff_granted_override_is_kept_and_named(self, notify):
        staff = User.objects.create_user(email="staff@example.com", is_staff=True)
        manual = self._override(source="staff", granted_by=staff)

        retry_occurrence_step(self._removed(), "removal")

        manual.refresh_from_db()
        self.maven_override.refresh_from_db()
        self.assertTrue(manual.is_active)
        self.assertFalse(self.maven_override.is_active)
        self.assertIn(
            f"Kept: manual override #{manual.pk}",
            notify.call_args.kwargs["outcome"]["override_result"],
        )

    @patch(NOTIFY)
    def test_toggles_off_keep_override_and_skip_student_email(self, notify):
        configure(
            MAVEN_REMOVAL_REVOKES_OVERRIDE="false",
            MAVEN_REMOVAL_STUDENT_EMAIL="false",
        )

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertTrue(self.maven_override.is_active)
        self.assertFalse(self._student_deliveries().exists())
        outcome = notify.call_args.kwargs["outcome"]
        self.assertEqual(outcome["override_result"], "Kept: MAVEN_REMOVAL_REVOKES_OVERRIDE is off.")
        self.assertEqual(outcome["student_email_result"], "Not sent: MAVEN_REMOVAL_STUDENT_EMAIL is off.")

    @patch(NOTIFY)
    def test_permanent_bounce_suppresses_student_email(self, notify):
        self.user.bounce_state = "permanent"
        self.user.save(update_fields=["bounce_state"])

        retry_occurrence_step(self._removed(), "removal")

        self.assertFalse(self._student_deliveries().exists())
        self.assertEqual(
            notify.call_args.kwargs["outcome"]["student_email_result"],
            "Not sent: permanent bounce.",
        )

    @patch(NOTIFY)
    def test_reapply_after_legacy_removal_revokes_without_student_email(self, notify):
        occurrence = self._removed()
        occurrence.removal_status = MavenEnrollmentEvent.STEP_SUCCEEDED
        occurrence.save(update_fields=["removal_status"])

        result, actions = reapply_removal(occurrence)

        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.maven_override.refresh_from_db()
        self.assertFalse(self.maven_override.is_active)
        self.assertFalse(self._student_deliveries().exists())
        self.assertEqual(
            notify.call_args.kwargs["outcome"]["student_email_result"],
            "Not sent: re-applied without the send-student-email flag.",
        )

    @patch(NOTIFY)
    def test_repeated_reapply_is_idempotent(self, notify):
        occurrence = self._removed()
        reapply_removal(occurrence, send_student_email=True)
        reapply_removal(occurrence, send_student_email=True)

        self.assertEqual(
            CommunityAuditLog.objects.filter(action="tier_override_revoked").count(), 1,
        )
        self.assertEqual(self._student_deliveries().count(), 1)
        self.assertEqual(
            notify.call_args.kwargs["outcome"]["override_result"],
            "No active tier override to revoke.",
        )

    def test_reapply_rejects_an_active_occurrence(self):
        occurrence = self._occurrence(
            "active-one", lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
        )
        with self.assertRaises(ValueError):
            reapply_removal(occurrence)

    @patch(NOTIFY)
    def test_student_email_names_course_and_deletion_steps(self, notify):
        retry_occurrence_step(self._removed(), "removal")

        subject, html = _drain(self._student_deliveries().get())

        self.assertEqual(subject, "Your AI Engineering Buildcamp access has ended")
        self.assertIn("Your AI Shipping Labs account still exists", html)
        self.assertIn("/account/#privacy-data-section", html)
        self.assertIn("Request account deletion", html)
        self.assertIn("team@aishippinglabs.com", html)

    def test_staff_email_lists_what_was_done(self):
        configure(STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com")

        retry_occurrence_step(self._removed(), "removal")

        subject, html = _drain(
            EmailDelivery.objects.get(purpose="maven_cohort_removal_notification"),
        )
        self.assertEqual(subject, "Maven cohort removal — what was changed")
        self.assertIn("Tier override: Main access revoked", html)
        self.assertIn("Student email: Sent the maven_removal email.", html)
        self.assertNotIn("the decision is yours", html)


class MavenRemovalStaffSummaryTest(TestCase):
    def test_slack_summary_lists_outcomes_without_decision_wording(self):
        text = _build_removal_slack_text({
            "user_known": True,
            "removed_user_name": "Ada",
            "removed_user_email": "ada@example.com",
            "removed_user_id": "5",
            "studio_user_url": "https://aishippinglabs.com/studio/users/5/",
            "cohort": "Cohort 5",
            "course": "Buildcamp",
            "override_result": "Main access revoked: Maven override #7.",
            "slack_result": "Unchanged: Slack membership is never changed automatically.",
        })

        self.assertIn("*Tier override:* Main access revoked: Maven override #7.", text)
        self.assertIn("*Slack:* Unchanged", text)
        self.assertNotIn("You may want to suspend", text)


class MavenRemovalReapplyApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="ops@example.com", is_staff=True)
        cls.token = Token.objects.create(user=cls.staff, name="ops")
        cls.member = User.objects.create_user(email="gone@example.com")
        cls.main = Tier.objects.get(slug="main")

    def _occurrence(self, lifecycle):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=f"api-{lifecycle}", identity_hash=f"api-{lifecycle}",
            user=self.member, email=self.member.email, course_key=COURSE_KEY,
            cohort_key="cohort-5", lifecycle=lifecycle,
            removal_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

    def _post(self, occurrence, body=None, *, auth=True):
        headers = {"HTTP_AUTHORIZATION": f"Token {self.token.key}"} if auth else {}
        return self.client.post(
            f"/api/integrations/maven/occurrences/{occurrence.pk}/removal/reapply",
            data=json.dumps(body) if body is not None else "",
            content_type="application/json",
            **headers,
        )

    @patch(NOTIFY)
    def test_reapply_revokes_and_reports_actions(self, notify):
        override = TierOverride.objects.create(
            user=self.member, override_tier=self.main, is_active=True,
            expires_at=timezone.now() + timedelta(days=30), source="maven:api",
        )

        response = self._post(self._occurrence("removed"))

        body = response.json()
        self.assertFalse(body["reapply"]["send_student_email"])
        self.assertEqual(body["reapply"]["outcome"], "succeeded")
        override.refresh_from_db()
        self.assertFalse(override.is_active)
        self.assertFalse(EmailDelivery.objects.filter(purpose="maven_removal").exists())

    def test_active_occurrence_is_rejected(self):
        response = self._post(self._occurrence("active"))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "maven_occurrence_not_removed")

    def test_requires_staff_token(self):
        response = self._post(self._occurrence("removed"), auth=False)

        self.assertEqual(response.status_code, 401)
