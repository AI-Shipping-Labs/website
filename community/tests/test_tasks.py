"""Tests for community background tasks.

Tests cover:
- email_matcher: finds users without slack_user_id and links them
- scheduled_community_removal: removes users if they no longer qualify
- hooks: thin wrappers that delegate to CommunityService
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings, tag

from accounts.models import User
from community.models import CommunityAuditLog
from community.services.slack import SlackAPIError
from community.tasks.email_matcher import match_community_emails
from community.tasks.hooks import (
    community_invite_task,
    community_reactivate_task,
    community_remove_task,
)
from community.tasks.removal import scheduled_community_removal
from payments.models import Tier


@override_settings(
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001"],
)
@tag('core')
class EmailMatcherTest(TestCase):
    """Tests for the email matcher background job."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.free_tier = Tier.objects.get(slug="free")
        self.basic_tier = Tier.objects.get(slug="basic")

    @patch("community.tasks.email_matcher.get_community_service")
    def test_matches_user_by_email(self, mock_get_service):
        """User with Main tier and no slack_user_id gets linked."""
        user = User.objects.create_user(email="match@test.com")
        user.tier = self.main_tier
        user.save(update_fields=["tier"])

        mock_service = MagicMock()
        mock_service.lookup_user_by_email.return_value = "U_MATCHED"
        mock_service.add_to_channels.return_value = [{"channel": "C001", "ok": True}]
        mock_get_service.return_value = mock_service

        result = match_community_emails()

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U_MATCHED")
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["not_found"], 0)

        # Audit log should be created with "link" action
        log = CommunityAuditLog.objects.get(user=user)
        self.assertEqual(log.action, "link")
        details = json.loads(log.details)
        self.assertEqual(details["source"], "email_matcher")

    @patch("community.tasks.email_matcher.get_community_service")
    def test_skips_users_with_slack_id(self, mock_get_service):
        """Users who already have slack_user_id are not checked."""
        user = User.objects.create_user(email="already@test.com")
        user.tier = self.main_tier
        user.slack_user_id = "U_EXISTING"
        user.save(update_fields=["tier", "slack_user_id"])

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        result = match_community_emails()

        mock_service.lookup_user_by_email.assert_not_called()
        self.assertEqual(result["total_checked"], 0)

    @patch("community.tasks.email_matcher.get_community_service")
    def test_skips_users_below_community_tier(self, mock_get_service):
        """Users with Basic or Free tier are not checked."""
        user = User.objects.create_user(email="basic@test.com")
        user.tier = self.basic_tier
        user.save(update_fields=["tier"])

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        result = match_community_emails()

        mock_service.lookup_user_by_email.assert_not_called()
        self.assertEqual(result["total_checked"], 0)

    @patch("community.tasks.email_matcher.get_community_service")
    def test_not_found_counted(self, mock_get_service):
        """Users not found in Slack are counted in not_found."""
        user = User.objects.create_user(email="nofind@test.com")
        user.tier = self.main_tier
        user.save(update_fields=["tier"])

        mock_service = MagicMock()
        mock_service.lookup_user_by_email.return_value = None
        mock_get_service.return_value = mock_service

        result = match_community_emails()

        self.assertEqual(result["not_found"], 1)
        self.assertEqual(result["matched"], 0)

    @patch("community.tasks.email_matcher.get_community_service")
    def test_error_counted(self, mock_get_service):
        """Errors during lookup are counted in errors."""
        user = User.objects.create_user(email="error@test.com")
        user.tier = self.main_tier
        user.save(update_fields=["tier"])

        mock_service = MagicMock()
        mock_service.lookup_user_by_email.side_effect = Exception("API timeout")
        mock_get_service.return_value = mock_service

        with self.assertLogs("community.tasks.email_matcher", level="ERROR") as logs:
            result = match_community_emails()

        self.assertEqual(result["errors"], 1)
        self.assertIn(
            "Email matcher: error processing user error@test.com",
            logs.output[0],
        )


@override_settings(
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001"],
)
class ScheduledRemovalTest(TestCase):
    """Tests for the scheduled community removal task."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.free_tier = Tier.objects.get(slug="free")

    @patch("community.tasks.removal.get_community_service")
    def test_removes_user_below_community_tier(self, mock_get_service):
        """User with free tier gets removed from community."""
        user = User.objects.create_user(email="remove_sched@test.com")
        user.tier = self.free_tier
        user.slack_user_id = "U123"
        user.save(update_fields=["tier", "slack_user_id"])

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        scheduled_community_removal(user.pk)

        mock_service.remove.assert_called_once_with(user)

    @patch("community.tasks.removal.get_community_service")
    def test_skips_if_user_resubscribed(self, mock_get_service):
        """If user re-subscribed to Main+, removal is skipped."""
        user = User.objects.create_user(email="resub@test.com")
        user.tier = self.main_tier
        user.slack_user_id = "U123"
        user.save(update_fields=["tier", "slack_user_id"])

        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        scheduled_community_removal(user.pk)

        mock_service.remove.assert_not_called()

    @patch("community.tasks.removal.get_community_service")
    def test_handles_missing_user(self, mock_get_service):
        """Non-existent user ID is handled gracefully."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        # Should not raise
        scheduled_community_removal(99999)

        mock_service.remove.assert_not_called()


@override_settings(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_ENVIRONMENT="development",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001"],
    SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
)
class HookTasksTest(TestCase):
    """Tests for the hook wrapper tasks."""

    def setUp(self):
        self.user = User.objects.create_user(email="hook@test.com")

    @patch("community.tasks.hooks.get_community_service")
    def test_invite_task(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        community_invite_task(self.user.pk)

        mock_service.invite.assert_called_once()
        called_user = mock_service.invite.call_args[0][0]
        self.assertEqual(called_user.pk, self.user.pk)

    @patch("community.tasks.hooks.get_community_service")
    def test_reactivate_task(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        community_reactivate_task(self.user.pk)

        mock_service.reactivate.assert_called_once()

    @patch("community.tasks.hooks.get_community_service")
    def test_remove_task(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        community_remove_task(self.user.pk)

        mock_service.remove.assert_called_once()

    @patch("community.tasks.hooks.get_community_service")
    def test_invite_task_missing_user(self, mock_get_service):
        """Missing user does not call service."""
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        community_invite_task(99999)

        mock_service.invite.assert_not_called()


class HookTasksSlackConfigSkipTest(TestCase):
    """Slack hook tasks should no-op cleanly when Slack is not configured."""

    def setUp(self):
        self.user = User.objects.create_user(email="slack-skip@test.com")
        self.user.slack_user_id = "U123"
        self.user.save(update_fields=["slack_user_id"])

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    def test_invite_task_warns_and_skips_when_slack_disabled(
        self, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_invite_task(self.user.pk)

        mock_get_service.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn("community_invite_task skipped", logs.output[0])
        self.assertIn("SLACK_ENABLED=true", logs.output[0])
        self.assertIn("restart web and worker processes", logs.output[0])

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    def test_reactivate_task_warns_and_skips_when_slack_disabled(
        self, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_reactivate_task(self.user.pk)

        mock_get_service.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn("community_reactivate_task skipped", logs.output[0])
        self.assertIn("SLACK_ENABLED=true", logs.output[0])

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    def test_remove_task_warns_and_skips_when_slack_disabled(
        self, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_remove_task(self.user.pk)

        mock_get_service.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn("community_remove_task skipped", logs.output[0])
        self.assertIn("SLACK_ENABLED=true", logs.output[0])

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    def test_invite_task_warns_and_skips_when_token_missing(
        self, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_invite_task(self.user.pk)

        mock_get_service.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn("SLACK_BOT_TOKEN", logs.output[0])
        self.assertIn("restart web and worker processes", logs.output[0])

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="   ",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    def test_invite_task_warns_and_skips_when_token_is_whitespace(
        self, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_invite_task(self.user.pk)

        mock_get_service.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn("SLACK_BOT_TOKEN", logs.output[0])

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=[],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    def test_invite_task_warns_and_skips_when_channels_missing(
        self, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_invite_task(self.user.pk)

        mock_get_service.assert_not_called()
        mock_post.assert_not_called()
        self.assertIn("SLACK_DEV_COMMUNITY_CHANNEL_IDS", logs.output[0])
        self.assertIn("SLACK_ENVIRONMENT=development", logs.output[0])

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
    )
    @patch("community.tasks.hooks.get_community_service")
    def test_enabled_real_slack_errors_still_surface(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.invite.side_effect = SlackAPIError(
            "Slack API error: invalid_auth",
            method="users.lookupByEmail",
            error_code="invalid_auth",
        )
        mock_get_service.return_value = mock_service

        with self.assertRaises(SlackAPIError):
            community_invite_task(self.user.pk)


class HookTasksInviteEmailFallbackTest(TestCase):
    """Slack-disabled hooks should still send the invite email when SLACK_INVITE_URL is set.

    Regression coverage for issue #639: the email fallback inside
    SlackCommunityService.invite / .reactivate only needs SLACK_INVITE_URL,
    not the Slack API, so gating it behind SLACK_ENABLED was wrong.
    """

    def setUp(self):
        self.user = User.objects.create_user(email="fallback@test.com")

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="https://join.slack.com/test",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    @patch("community.services.slack.send_mail")
    def test_invite_task_sends_email_when_slack_disabled_and_invite_url_set(
        self, mock_send_mail, mock_post, mock_get_service
    ):
        community_invite_task(self.user.pk)

        mock_send_mail.assert_called_once()
        kwargs = mock_send_mail.call_args.kwargs
        self.assertIn(self.user.email, kwargs["recipient_list"])
        self.assertIn("https://join.slack.com/test", kwargs["message"])

        mock_post.assert_not_called()
        mock_get_service.assert_not_called()

        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "invite")
        details = json.loads(log.details)
        self.assertEqual(details["status"], "email_sent")
        self.assertEqual(details["reason"], "slack_api_disabled")

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    @patch("community.services.slack.send_mail")
    def test_invite_task_skips_cleanly_when_slack_disabled_and_invite_url_empty(
        self, mock_send_mail, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_invite_task(self.user.pk)

        mock_send_mail.assert_not_called()
        mock_post.assert_not_called()
        mock_get_service.assert_not_called()
        self.assertFalse(CommunityAuditLog.objects.filter(user=self.user).exists())
        self.assertIn("community_invite_task skipped", logs.output[0])

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="https://join.slack.com/test",
    )
    @patch("community.tasks.hooks.get_community_service")
    def test_invite_task_runs_full_slack_flow_when_enabled(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        community_invite_task(self.user.pk)

        mock_service.invite.assert_called_once()
        called_user = mock_service.invite.call_args[0][0]
        self.assertEqual(called_user.pk, self.user.pk)

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="https://join.slack.com/test",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    @patch("community.services.slack.send_mail")
    def test_reactivate_task_sends_email_when_slack_disabled_and_invite_url_set(
        self, mock_send_mail, mock_post, mock_get_service
    ):
        community_reactivate_task(self.user.pk)

        mock_send_mail.assert_called_once()
        kwargs = mock_send_mail.call_args.kwargs
        self.assertIn(self.user.email, kwargs["recipient_list"])
        self.assertIn("https://join.slack.com/test", kwargs["message"])

        mock_post.assert_not_called()
        mock_get_service.assert_not_called()

        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "reactivate")
        details = json.loads(log.details)
        self.assertEqual(details["status"], "email_sent")
        self.assertEqual(details["reason"], "slack_api_disabled")

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    @patch("community.services.slack.send_mail")
    def test_reactivate_task_skips_cleanly_when_slack_disabled_and_invite_url_empty(
        self, mock_send_mail, mock_post, mock_get_service
    ):
        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_reactivate_task(self.user.pk)

        mock_send_mail.assert_not_called()
        mock_post.assert_not_called()
        mock_get_service.assert_not_called()
        self.assertFalse(CommunityAuditLog.objects.filter(user=self.user).exists())
        self.assertIn("community_reactivate_task skipped", logs.output[0])

    @override_settings(
        SLACK_ENABLED=False,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="https://join.slack.com/test",
    )
    @patch("community.tasks.hooks.get_community_service")
    @patch("community.services.slack.requests.post")
    @patch("community.services.slack.send_mail")
    def test_remove_task_skip_behavior_unchanged_when_slack_disabled(
        self, mock_send_mail, mock_post, mock_get_service
    ):
        self.user.slack_user_id = "U123"
        self.user.save(update_fields=["slack_user_id"])

        with self.assertLogs("community.tasks.hooks", level="WARNING") as logs:
            community_remove_task(self.user.pk)

        mock_send_mail.assert_not_called()
        mock_post.assert_not_called()
        mock_get_service.assert_not_called()
        self.assertFalse(CommunityAuditLog.objects.filter(user=self.user).exists())
        self.assertIn("community_remove_task skipped", logs.output[0])

    @override_settings(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
        SLACK_INVITE_URL="https://join.slack.com/test",
    )
    @patch("community.tasks.hooks.get_community_service")
    def test_enabled_real_slack_errors_still_surface(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.invite.side_effect = SlackAPIError(
            "Slack API error: invalid_auth",
            method="users.lookupByEmail",
            error_code="invalid_auth",
        )
        mock_get_service.return_value = mock_service

        with self.assertRaises(SlackAPIError):
            community_invite_task(self.user.pk)
