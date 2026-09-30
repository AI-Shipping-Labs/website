"""Maven removal automation: override revoke, student email, real-outcome staff summary.

A ``user_cohort.removed`` occurrence's ``removal`` step makes every automatic
change first (course access, cohort enrollment, tags, the Maven-granted tier
override), emails the removed student, and only then sends staff a summary
built from what actually happened. ``reapply_removal`` re-runs it for
removals processed before this existed.
"""

import json
import re
from datetime import timedelta
from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from community.models import CommunityAuditLog
from community.services.staff_notifications import (
    _build_removal_slack_text,
    notify_maven_cohort_removal,
)
from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from email_app.testing import StubSESClient
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import (
    RemovalOutcome,
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

    def _occurrence(
        self, key, *, lifecycle, cohort_key="cohort-5", course_key=COURSE_KEY,
        course="AI Engineering Buildcamp", **fields,
    ):
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, user=self.user,
            email=self.user.email, course=course,
            cohort=cohort_key, course_key=course_key, cohort_key=cohort_key,
            lifecycle=lifecycle, event_type="user_cohort.removed",
            removal_status=MavenEnrollmentEvent.STEP_PENDING, **fields,
        )

    def _removed(self, key="removed-hash"):
        return self._occurrence(key, lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED)

    @staticmethod
    def _summary(notify):
        return notify.call_args.kwargs["outcome"]["summary_lines"]

    def _student_deliveries(self):
        return EmailDelivery.objects.filter(purpose="maven_removal")

    @patch(NOTIFY)
    def test_removal_revokes_maven_override_and_reports_real_outcomes(self, notify):
        self.user.tags = ["maven", "ai-buildcamp", "ai-buildcamp-cohort-5"]
        self.user.save(update_fields=["tags"])

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertFalse(self.maven_override.is_active)
        audit = CommunityAuditLog.objects.get(
            user=self.user, action="tier_override_revoked",
        )
        self.assertIn("actor=maven_removal", audit.details)
        self.assertFalse(CourseAccess.objects.filter(user=self.user).exists())

        self.assertEqual(
            self._summary(notify),
            [
                "Revoked access to AI Engineering Buildcamp.",
                "Removed from cohort cohort-5.",
                "Removed tags ai-buildcamp-cohort-5, ai-buildcamp.",
                "Revoked the main membership override.",
                "Sent them the removal email.",
            ],
        )
        self.assertEqual(self._student_deliveries().count(), 1)

    @patch(NOTIFY)
    def test_transfer_to_another_cohort_keeps_course_but_revokes_override(self, notify):
        self._occurrence(
            "still-active", lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            cohort_key="cohort-6",
        )

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertFalse(self.maven_override.is_active)
        self.assertTrue(CourseAccess.objects.filter(user=self.user).exists())
        self.assertEqual(
            self._summary(notify),
            [
                "Kept course access: still in cohort cohort-6.",
                "Removed from cohort cohort-5.",
                "Revoked the main membership override.",
                "Didn't email them: still in cohort cohort-6.",
            ],
        )
        self.assertFalse(self._student_deliveries().exists())

    @patch(NOTIFY)
    def test_active_in_a_different_maven_course_keeps_override(self, notify):
        self._occurrence(
            "other-course", lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            course_key="other-course", cohort_key="1", course="Other Course",
        )

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertTrue(self.maven_override.is_active)
        self.assertFalse(CourseAccess.objects.filter(user=self.user).exists())
        self.assertIn(
            "Kept main access: still enrolled in Other Course on Maven.",
            self._summary(notify),
        )
        _subject, html = _drain(self._student_deliveries().get())
        self.assertIn("so your access to the course has ended.", html)

    @patch(NOTIFY)
    def test_stale_old_key_row_for_the_same_cohort_keeps_nothing(self, notify):
        self._occurrence(
            "stale-old-keys", lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            course_key="AI Engineering Buildcamp", cohort_key="cohort-5/11",
        )

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertFalse(self.maven_override.is_active)
        self.assertFalse(CourseAccess.objects.filter(user=self.user).exists())
        self.assertEqual(
            self._summary(notify),
            [
                "Revoked access to AI Engineering Buildcamp.",
                "Removed from cohort cohort-5.",
                "Revoked the main membership override.",
                "Sent them the removal email.",
            ],
        )

    @patch(NOTIFY)
    def test_alumnus_of_an_earlier_cohort_keeps_override(self, notify):
        cohort_2 = Cohort.objects.create(
            course=self.course, external_key="cohort-2", name="Cohort 2",
            start_date="2025-09-01", end_date="2025-11-01",
        )
        CohortEnrollment.objects.create(cohort=cohort_2, user=self.user)
        self.user.tags = [
            "maven", "ai-buildcamp", "ai-buildcamp-cohort-2", "ai-buildcamp-cohort-5",
        ]
        self.user.save(update_fields=["tags"])

        retry_occurrence_step(self._removed(), "removal")

        self.maven_override.refresh_from_db()
        self.assertTrue(self.maven_override.is_active)
        self.assertFalse(
            CohortEnrollment.objects.filter(cohort=self.cohort, user=self.user).exists()
        )
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.tags, ["maven", "ai-buildcamp", "ai-buildcamp-cohort-2"],
        )
        summary = self._summary(notify)
        self.assertIn("Removed tag ai-buildcamp-cohort-5.", summary)
        self.assertIn(
            "Kept main access: also in cohort 2 of AI Engineering Buildcamp.",
            summary,
        )

    @patch(NOTIFY)
    def test_staff_granted_override_is_kept_and_named(self, notify):
        staff = User.objects.create_user(email="staff@example.com", is_staff=True)
        manual = self._override(source="staff", granted_by=staff)

        retry_occurrence_step(self._removed(), "removal")

        manual.refresh_from_db()
        self.maven_override.refresh_from_db()
        self.assertTrue(manual.is_active)
        self.assertFalse(self.maven_override.is_active)
        summary = self._summary(notify)
        self.assertIn("Revoked the main membership override.", summary)
        self.assertIn("Kept main access: the override was granted manually.", summary)

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
        summary = self._summary(notify)
        self.assertIn("Kept main access: MAVEN_REMOVAL_REVOKES_OVERRIDE is off.", summary)
        self.assertIn("Didn't email them: MAVEN_REMOVAL_STUDENT_EMAIL is off.", summary)

    @patch(NOTIFY)
    def test_permanent_bounce_suppresses_student_email(self, notify):
        self.user.bounce_state = "permanent"
        self.user.save(update_fields=["bounce_state"])

        retry_occurrence_step(self._removed(), "removal")

        self.assertFalse(self._student_deliveries().exists())
        self.assertIn("Didn't email them: permanent bounce.", self._summary(notify))

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
        self.assertNotIn("Sent them the removal email.", self._summary(notify))

    @patch(NOTIFY)
    def test_repeated_reapply_is_idempotent(self, notify):
        occurrence = self._removed()
        reapply_removal(occurrence, send_student_email=True)
        reapply_removal(occurrence, send_student_email=True)

        self.assertEqual(
            CommunityAuditLog.objects.filter(action="tier_override_revoked").count(), 1,
        )
        self.assertEqual(self._student_deliveries().count(), 1)
        self.assertEqual(self._summary(notify), ["Sent them the removal email."])

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

        self.assertEqual(subject, "Your access to AI Engineering Buildcamp has ended")
        self.assertIn(
            "You're no longer enrolled in AI Engineering Buildcamp, so your access",
            html.replace("&#x27;", "'").replace("&#39;", "'"),
        )
        self.assertIn("/account/#privacy-data-section", html)
        self.assertIn("Request account deletion", html)
        self.assertIn("team@aishippinglabs.com", html)

    def test_staff_email_lists_only_what_changed(self):
        configure(STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com")

        retry_occurrence_step(self._removed(), "removal")

        subject, html = _drain(
            EmailDelivery.objects.get(purpose="maven_cohort_removal_notification"),
        )
        self.assertEqual(
            subject, "Maven removal: Ada (AI Engineering Buildcamp, cohort cohort-5)",
        )
        items = re.findall(r"<li>(.*?)</li>", html)
        self.assertEqual(
            items,
            [
                "Revoked access to AI Engineering Buildcamp.",
                "Removed from cohort cohort-5.",
                "Revoked the main membership override.",
                "Sent them the removal email.",
            ],
        )
        self.assertNotIn("Stripe", html)


class MavenRemovalStaffSummaryTest(TestCase):
    def test_empty_outcome_says_nothing_needed_changing(self):
        self.assertEqual(
            RemovalOutcome().summary_lines(),
            ["Nothing needed changing: they had no access left."],
        )

    def test_slack_summary_bullets_the_summary_lines(self):
        text = _build_removal_slack_text({
            "user_known": True,
            "removed_user_name": "Ada",
            "removed_user_email": "ada@example.com",
            "removed_user_id": "5",
            "studio_user_url": "https://aishippinglabs.com/studio/users/5/",
            "cohort": "5",
            "course": "Buildcamp",
            "summary_lines": ["Revoked the main membership override."],
        })

        self.assertEqual(
            text.splitlines(),
            [
                "*Maven removal:* Ada (ada@example.com) was removed from cohort 5 "
                "of Buildcamp on Maven.",
                "• Revoked the main membership override.",
                "<https://aishippinglabs.com/studio/users/5/|Open in Studio>",
            ],
        )

    def test_unknown_user_staff_email_says_nothing_to_do(self):
        configure(STAFF_SIGNUP_NOTIFY_EMAIL="staff@example.com")
        notify_maven_cohort_removal(
            None, "5", "Buildcamp", email="ghost@example.com",
        )

        subject, html = _drain(
            EmailDelivery.objects.get(purpose="maven_cohort_removal_notification"),
        )
        self.assertEqual(subject, "Maven removal: ghost@example.com (Buildcamp, cohort 5)")
        self.assertIn(
            "ghost@example.com was removed from cohort 5 of Buildcamp on Maven. "
            "No account uses this email, so there was nothing to do.",
            html,
        )
        self.assertNotIn("<li>", html)


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
