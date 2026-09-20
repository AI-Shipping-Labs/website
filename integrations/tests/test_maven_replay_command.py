"""Tests for the replay_maven_event management command (issue #960)."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from content.models import Cohort, CohortEnrollment, Course, CourseAccess
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from payments.models import TierOverride

User = get_user_model()


def _enable():
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"}
    )
    from integrations.config import clear_config_cache

    clear_config_cache()


class ReplayMavenEventTest(TestCase):
    def setUp(self):
        _enable()
        from integrations.config import clear_config_cache

        self.addCleanup(clear_config_cache)

    def test_dry_run_makes_no_writes(self):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "dryrun@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "Spring 2026",
            "--dry-run",
            stdout=out,
        )
        self.assertFalse(User.objects.filter(email="dryrun@test.com").exists())
        self.assertFalse(TierOverride.objects.filter(user__email="dryrun@test.com").exists())
        self.assertFalse(MavenEnrollmentEvent.objects.exists())
        output = out.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("Would", output)

    @patch("integrations.services.maven.send_package_mail")
    def test_real_run_then_idempotent(self, package_mail):
        # Issue #1659: already_processed now also requires ``enrollment`` to
        # be terminal, so this needs a resolvable maven_course_key/external_key.
        course = Course.objects.create(
            title="LLM Zoomcamp", slug="llm-zoomcamp-replay-idempotent",
            maven_course_key="llm zoomcamp",
        )
        Cohort.objects.create(
            course=course, external_key="spring 2026", name="Spring 2026",
            start_date="2026-01-01", end_date="2026-03-01",
        )
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "real@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "Spring 2026",
            stdout=out,
        )
        self.assertTrue(User.objects.filter(email="real@test.com").exists())
        self.assertIn("onboarded", out.getvalue())

        out2 = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "real@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "Spring 2026",
            stdout=out2,
        )
        self.assertIn("already_processed", out2.getvalue())
        self.assertEqual(
            TierOverride.objects.filter(
                user__email="real@test.com", is_active=True
            ).count(),
            1,
        )
        self.assertEqual(package_mail.call_count, 1)

    @patch("integrations.services.maven.send_package_mail")
    def test_backfill_replay_runs_the_enrollment_step_like_a_live_webhook(self, package_mail):
        # Issue #1659: cohort-4 members enrolled before this feature shipped
        # are backfilled through this exact replay path.
        course = Course.objects.create(
            title="Buildcamp", slug="buildcamp-replay-1659",
            maven_course_key="buildcamp",
        )
        cohort = Cohort.objects.create(
            course=course, external_key="cohort 4", name="Cohort 4",
            start_date="2026-09-21", end_date="2026-11-22",
        )
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "backfill-1659@test.com",
            "--course", "Buildcamp",
            "--cohort", "Cohort 4",
            stdout=out,
        )

        occurrence = MavenEnrollmentEvent.objects.get(email="backfill-1659@test.com")
        self.assertEqual(occurrence.enrollment_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertTrue(
            CourseAccess.objects.filter(
                user__email="backfill-1659@test.com", course=course,
            ).exists()
        )
        self.assertTrue(
            CohortEnrollment.objects.filter(
                cohort=cohort, user__email="backfill-1659@test.com",
            ).exists()
        )

    @patch("community.services.staff_notifications.notify_maven_cohort_removal")
    def test_removal_replay(self, notify):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.removed",
            "--email", "rem@test.com",
            "--course", "LLM Zoomcamp",
            "--cohort", "C1",
            stdout=out,
        )
        self.assertIn("removal_notified", out.getvalue())
        notify.assert_called_once()
