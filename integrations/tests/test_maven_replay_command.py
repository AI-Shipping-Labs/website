"""Tests for the replay_maven_event management command (issue #960)."""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from accounts.models import TierOverride
from integrations.models import IntegrationSetting, MavenEnrollmentEvent

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
            "--dry-run",
            stdout=out,
        )
        self.assertFalse(User.objects.filter(email="dryrun@test.com").exists())
        self.assertFalse(TierOverride.objects.filter(user__email="dryrun@test.com").exists())
        self.assertFalse(MavenEnrollmentEvent.objects.exists())
        output = out.getvalue()
        self.assertIn("DRY RUN", output)
        self.assertIn("Would", output)

    @patch("integrations.services.maven._invite_to_slack", lambda u, a: None)
    @patch("integrations.services.maven.EmailService")
    def test_real_run_then_idempotent(self, email_service):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "real@test.com",
            stdout=out,
        )
        self.assertTrue(User.objects.filter(email="real@test.com").exists())
        self.assertIn("onboarded", out.getvalue())

        out2 = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.enrolled",
            "--email", "real@test.com",
            stdout=out2,
        )
        self.assertIn("already_processed", out2.getvalue())
        self.assertEqual(
            TierOverride.objects.filter(
                user__email="real@test.com", is_active=True
            ).count(),
            1,
        )
        self.assertEqual(email_service.return_value.send.call_count, 1)

    @patch("community.services.staff_notifications.notify_maven_cohort_removal")
    def test_removal_replay(self, notify):
        out = StringIO()
        call_command(
            "replay_maven_event",
            "--event", "user_cohort.removed",
            "--email", "rem@test.com",
            "--cohort", "C1",
            stdout=out,
        )
        self.assertIn("removal_notified", out.getvalue())
        notify.assert_called_once()
