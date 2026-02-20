"""Tests for the CommunityService and SlackCommunityService.

All Slack API calls are mocked. Tests verify:
- invite/remove/reactivate logic
- Slack API call flow
- Audit log creation
- Email sending when user not found in Slack
- Error handling
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from accounts.models import User
from community.models import CommunityAuditLog
from community.services import get_community_service
from community.services.base import CommunityService
from community.services.slack import SlackAPIError, SlackCommunityService


MOCK_CHANNELS = ["C001", "C002"]


class CommunityServiceInterfaceTest(TestCase):
    """Test that CommunityService is an abstract interface."""

    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            CommunityService()

    def test_get_community_service_returns_slack(self):
        service = get_community_service()
        self.assertIsInstance(service, SlackCommunityService)


class SlackAPICallTest(TestCase):
    """Tests for the Slack API call mechanism."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )

    @patch("community.services.slack.requests.post")
    def test_api_call_success(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "user": {"id": "U123"}}
        mock_post.return_value = mock_response

        result = self.service._api_call("users.lookupByEmail", email="test@test.com")
        self.assertTrue(result["ok"])

    @patch("community.services.slack.requests.post")
    def test_api_call_http_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_post.return_value = mock_response

        with self.assertRaises(SlackAPIError) as ctx:
            self.service._api_call("users.lookupByEmail", email="test@test.com")
        self.assertIn("HTTP error", str(ctx.exception))

    @patch("community.services.slack.requests.post")
    def test_api_call_slack_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}
        mock_post.return_value = mock_response

        with self.assertRaises(SlackAPIError) as ctx:
            self.service._api_call("conversations.invite", channel="C999", users="U123")
        self.assertEqual(ctx.exception.error_code, "channel_not_found")


class LookupUserByEmailTest(TestCase):
    """Tests for lookup_user_by_email."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )

    @patch("community.services.slack.requests.post")
    def test_found(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "ok": True,
            "user": {"id": "U123", "name": "testuser"},
        }
        mock_post.return_value = mock_response

        result = self.service.lookup_user_by_email("test@test.com")
        self.assertEqual(result, "U123")

    @patch("community.services.slack.requests.post")
    def test_not_found(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": False, "error": "users_not_found"}
        mock_post.return_value = mock_response

        result = self.service.lookup_user_by_email("missing@test.com")
        self.assertIsNone(result)


class AddToChannelsTest(TestCase):
    """Tests for add_to_channels."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )

    @patch("community.services.slack.requests.post")
    def test_adds_to_all_channels(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        results = self.service.add_to_channels("U123")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["ok"] for r in results))

    @patch("community.services.slack.requests.post")
    def test_already_in_channel_is_ok(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": False, "error": "already_in_channel"}
        mock_post.return_value = mock_response

        results = self.service.add_to_channels("U123")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["ok"] for r in results))
        self.assertTrue(all(r.get("already_in") for r in results))


class RemoveFromChannelsTest(TestCase):
    """Tests for remove_from_channels."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )

    @patch("community.services.slack.requests.post")
    def test_removes_from_all_channels(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        results = self.service.remove_from_channels("U123")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["ok"] for r in results))

    @patch("community.services.slack.requests.post")
    def test_not_in_channel_is_ok(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": False, "error": "not_in_channel"}
        mock_post.return_value = mock_response

        results = self.service.remove_from_channels("U123")
        self.assertTrue(all(r["ok"] for r in results))


@override_settings(
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
class InviteServiceTest(TestCase):
    """Tests for SlackCommunityService.invite()."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )
        self.user = User.objects.create_user(email="invite@test.com")

    @patch("community.services.slack.requests.post")
    def test_invite_with_existing_slack_id(self, mock_post):
        """If user has slack_user_id, adds to channels directly."""
        self.user.slack_user_id = "U789"
        self.user.save(update_fields=["slack_user_id"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        self.service.invite(self.user)

        # Should have called conversations.invite for each channel
        self.assertEqual(mock_post.call_count, 2)

        # Should create audit log
        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "invite")
        details = json.loads(log.details)
        self.assertEqual(details["slack_user_id"], "U789")

    @patch("community.services.slack.send_mail")
    @patch("community.services.slack.requests.post")
    def test_invite_lookup_by_email(self, mock_post, mock_mail):
        """If no slack_user_id, looks up by email and adds."""
        # First call: lookupByEmail returns user
        # Then 2 calls: conversations.invite for each channel
        responses = [
            # lookupByEmail
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={
                    "ok": True,
                    "user": {"id": "U_FOUND"},
                }),
            ),
            # conversations.invite channel 1
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={"ok": True}),
            ),
            # conversations.invite channel 2
            MagicMock(
                status_code=200,
                json=MagicMock(return_value={"ok": True}),
            ),
        ]
        mock_post.side_effect = responses

        self.service.invite(self.user)

        # slack_user_id should be saved
        self.user.refresh_from_db()
        self.assertEqual(self.user.slack_user_id, "U_FOUND")

        # Audit log should exist
        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "invite")

        # No email should be sent
        mock_mail.assert_not_called()

    @patch("community.services.slack.send_mail")
    @patch("community.services.slack.requests.post")
    def test_invite_not_found_sends_email(self, mock_post, mock_mail):
        """If user not found in Slack, sends invite email."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": False, "error": "users_not_found"}
        mock_post.return_value = mock_response

        self.service.invite(self.user)

        # Email should be sent
        mock_mail.assert_called_once()
        call_kwargs = mock_mail.call_args
        self.assertIn("Welcome to AI Shipping Labs", call_kwargs[1]["subject"])

        # Audit log should indicate email sent
        log = CommunityAuditLog.objects.get(user=self.user)
        details = json.loads(log.details)
        self.assertEqual(details["status"], "email_sent")


@override_settings(
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
class RemoveServiceTest(TestCase):
    """Tests for SlackCommunityService.remove()."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )
        self.user = User.objects.create_user(email="remove@test.com")

    @patch("community.services.slack.requests.post")
    def test_remove_with_slack_id(self, mock_post):
        """Remove calls conversations.kick for each channel."""
        self.user.slack_user_id = "U789"
        self.user.save(update_fields=["slack_user_id"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        self.service.remove(self.user)

        self.assertEqual(mock_post.call_count, 2)
        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "remove")

    def test_remove_without_slack_id_skips(self):
        """Remove with no slack_user_id logs skip and returns."""
        self.service.remove(self.user)

        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "remove")
        details = json.loads(log.details)
        self.assertEqual(details["status"], "skipped")


@override_settings(
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
class ReactivateServiceTest(TestCase):
    """Tests for SlackCommunityService.reactivate()."""

    def setUp(self):
        self.service = SlackCommunityService(
            bot_token="xoxb-test-token",
            channel_ids=MOCK_CHANNELS,
        )
        self.user = User.objects.create_user(email="reactivate@test.com")

    @patch("community.services.slack.requests.post")
    def test_reactivate_with_slack_id(self, mock_post):
        """Reactivate re-adds user to channels."""
        self.user.slack_user_id = "U789"
        self.user.save(update_fields=["slack_user_id"])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}
        mock_post.return_value = mock_response

        self.service.reactivate(self.user)

        self.assertEqual(mock_post.call_count, 2)
        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "reactivate")

    @patch("community.services.slack.send_mail")
    @patch("community.services.slack.requests.post")
    def test_reactivate_without_slack_id_sends_email(self, mock_post, mock_mail):
        """Reactivate without slack_user_id sends invite email."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": False, "error": "users_not_found"}
        mock_post.return_value = mock_response

        self.service.reactivate(self.user)

        mock_mail.assert_called_once()
        log = CommunityAuditLog.objects.get(user=self.user)
        self.assertEqual(log.action, "reactivate")
        details = json.loads(log.details)
        self.assertEqual(details["status"], "email_sent")
