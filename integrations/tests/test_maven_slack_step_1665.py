"""Maven ``slack`` step: no Slack API calls, mirrors ``welcome_status`` (#1665).

15 of 17 buildcamp enrollees had ``slack_status=failed`` from
``users.lookupByEmail`` failing deterministically. Per the owner's decision,
the step stops calling the Slack API entirely and instead records that the
join link was delivered via the ``maven_welcome`` email. This module covers
the retargeted step logic directly; ``community/services/slack.py`` and its
use by the ordinary (non-Maven) community invite flow are exercised
elsewhere and untouched by this change.
"""

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from integrations.models import MavenEnrollmentEvent
from integrations.services.maven import (
    SLACK_JOIN_LINK_DELIVERED_NOTE,
    SLACK_JOIN_LINK_SUPPRESSED_NOTE,
    SLACK_JOIN_LINK_WELCOME_FAILED_NOTE,
    STEP_NAMES,
    retry_occurrence_step,
    run_occurrence_steps,
)

User = get_user_model()


class _MavenFixtureMixin:
    def _user(self, email="member-1665@example.com"):
        return User.objects.create_user(email=email, password="pw")

    def _occurrence(self, user, *, key, **fields):
        defaults = {
            "override_status": MavenEnrollmentEvent.STEP_SUCCEEDED,
            "enrollment_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "notification_status": MavenEnrollmentEvent.STEP_SKIPPED,
            "slack_status": MavenEnrollmentEvent.STEP_PENDING,
            "welcome_status": MavenEnrollmentEvent.STEP_PENDING,
        }
        defaults.update(fields)
        return MavenEnrollmentEvent.objects.create(
            dedupe_key=key, identity_hash=key, user=user,
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            event_type="user_cohort.enrolled",
            **defaults,
        )


class MavenSlackStepNeverCallsSlackTest(_MavenFixtureMixin, TestCase):
    """AC: the slack step makes zero Slack API calls."""

    def test_no_slack_service_is_constructed_or_called_when_the_step_runs(self):
        user = self._user("no-slack-call@example.com")
        occurrence = self._occurrence(
            user, key="no-slack-call",
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        with patch(
            "community.services.slack.get_community_service"
        ) as get_service, patch(
            "community.services.slack.SlackCommunityService.invite"
        ) as invite, patch(
            "community.services.slack.SlackCommunityService.lookup_user_by_email"
        ) as lookup, patch(
            "community.services.slack.SlackCommunityService.add_to_channels"
        ) as add_to_channels:
            result = retry_occurrence_step(occurrence, "slack")
        get_service.assert_not_called()
        invite.assert_not_called()
        lookup.assert_not_called()
        add_to_channels.assert_not_called()
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)


class MavenSlackStepMirrorsWelcomeTest(_MavenFixtureMixin, TestCase):
    def test_welcome_names_come_before_slack_in_the_occurrence_loop(self):
        self.assertLess(STEP_NAMES.index("welcome"), STEP_NAMES.index("slack"))

    def test_succeeded_welcome_yields_succeeded_slack_with_the_delivered_note(self):
        user = self._user("welcome-succeeded@example.com")
        occurrence = self._occurrence(
            user, key="welcome-succeeded",
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        result = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(occurrence.slack_error, SLACK_JOIN_LINK_DELIVERED_NOTE)

    def test_skipped_welcome_yields_skipped_slack_with_the_suppressed_note(self):
        user = self._user("welcome-skipped@example.com")
        occurrence = self._occurrence(
            user, key="welcome-skipped",
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        result = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertEqual(occurrence.slack_error, SLACK_JOIN_LINK_SUPPRESSED_NOTE)

    def test_failed_welcome_yields_failed_slack_with_a_retry_welcome_first_note(self):
        user = self._user("welcome-failed@example.com")
        occurrence = self._occurrence(
            user, key="welcome-failed",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
        )
        result = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertEqual(result.outcome, MavenEnrollmentEvent.STEP_FAILED)
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_FAILED)
        self.assertEqual(occurrence.slack_error, SLACK_JOIN_LINK_WELCOME_FAILED_NOTE)
        self.assertIn("Retry the welcome step, then retry slack", occurrence.slack_error)

    def test_run_occurrence_steps_resolves_slack_from_welcome_in_one_pass(self):
        user = self._user("one-pass@example.com")
        occurrence = self._occurrence(user, key="one-pass")
        with patch("integrations.services.maven._send_welcome"):
            run_occurrence_steps(occurrence)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.welcome_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)


class MavenSlackStepWelcomePendingGuardTest(_MavenFixtureMixin, TestCase):
    """AC: retrying slack before welcome resolves consumes no attempt."""

    def test_pending_welcome_declines_the_slack_attempt_without_changing_status(self):
        user = self._user("welcome-pending@example.com")
        occurrence = self._occurrence(
            user, key="welcome-pending",
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        result = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertFalse(result.attempted)
        self.assertEqual(result.reason, "welcome_pending")
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_PENDING)
        self.assertEqual(occurrence.slack_attempts, 0)

    def test_running_welcome_also_declines_the_slack_attempt(self):
        user = self._user("welcome-running@example.com")
        occurrence = self._occurrence(
            user, key="welcome-running",
            welcome_status=MavenEnrollmentEvent.STEP_RUNNING,
        )
        result = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertFalse(result.attempted)
        self.assertEqual(result.reason, "welcome_pending")
        self.assertEqual(occurrence.slack_attempts, 0)

    def test_forced_retry_still_declines_while_welcome_is_pending(self):
        # The Studio "Retry safely" action and the operator API both force
        # (bypass the 3-attempt ceiling); the welcome-pending guard must
        # still hold under force, per the issue's acceptance criteria.
        user = self._user("welcome-pending-forced@example.com")
        occurrence = self._occurrence(
            user, key="welcome-pending-forced",
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        result = retry_occurrence_step(occurrence, "slack")
        self.assertFalse(result.attempted)
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.slack_attempts, 0)

        # Once welcome resolves, retrying slack again resumes normally.
        occurrence.welcome_status = MavenEnrollmentEvent.STEP_SUCCEEDED
        occurrence.save(update_fields=["welcome_status"])
        second = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertTrue(second.attempted)
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
        self.assertEqual(occurrence.slack_attempts, 1)


class MavenAlreadyMemberSlackUntouchedTest(_MavenFixtureMixin, TestCase):
    def test_already_member_skipped_slack_is_never_re_evaluated(self):
        user = self._user("already-member@example.com")
        occurrence = self._occurrence(
            user, key="already-member",
            slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        result = retry_occurrence_step(occurrence, "slack")
        occurrence.refresh_from_db()
        self.assertFalse(result.attempted)
        self.assertEqual(result.reason, "not_retryable")
        self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SKIPPED)
        self.assertEqual(occurrence.slack_error, "")
        self.assertEqual(occurrence.slack_attempts, 0)


class RetryFailedMavenSlackStepsCommandTest(_MavenFixtureMixin, TestCase):
    def test_every_failed_row_with_succeeded_welcome_resolves_to_succeeded(self):
        user_a = self._user("legacy-a@example.com")
        user_b = self._user("legacy-b@example.com")
        occurrence_a = self._occurrence(
            user_a, key="legacy-a",
            slack_status=MavenEnrollmentEvent.STEP_FAILED,
            slack_attempts=3,
            slack_error="SlackAPIError",
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        occurrence_b = self._occurrence(
            user_b, key="legacy-b",
            slack_status=MavenEnrollmentEvent.STEP_FAILED,
            slack_attempts=3,
            slack_error="SlackAPIError",
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )
        # A control row that must be left alone (not slack_status=failed).
        untouched = self._occurrence(
            self._user("legacy-untouched@example.com"), key="legacy-untouched",
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
        )

        out = StringIO()
        call_command("retry_failed_maven_slack_steps", stdout=out)

        for occurrence in (occurrence_a, occurrence_b):
            occurrence.refresh_from_db()
            self.assertEqual(occurrence.slack_status, MavenEnrollmentEvent.STEP_SUCCEEDED)
            self.assertEqual(occurrence.slack_error, SLACK_JOIN_LINK_DELIVERED_NOTE)

        untouched.refresh_from_db()
        self.assertEqual(untouched.slack_attempts, 0)

        output = out.getvalue()
        self.assertIn(f"occurrence={occurrence_a.pk}", output)
        self.assertIn("legacy-a@example.com", output)
        self.assertIn("prior_status=failed", output)
        self.assertIn("resulting_status=succeeded", output)
        self.assertIn("Processed 2 Maven occurrence(s).", output)

    def test_no_failed_rows_prints_a_clean_message(self):
        out = StringIO()
        call_command("retry_failed_maven_slack_steps", stdout=out)
        self.assertIn("No Maven occurrences with slack_status=failed.", out.getvalue())
