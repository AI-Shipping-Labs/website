"""Tests for community background tasks.

Tests cover:
- email_matcher: finds users without slack_user_id and links them
- scheduled_community_removal: removes users if they no longer qualify
- hooks: thin wrappers that delegate to CommunityService
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from accounts.models import User
from community.models import CommunityAuditLog
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

        result = match_community_emails()

        self.assertEqual(result["errors"], 1)


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
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001"],
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
