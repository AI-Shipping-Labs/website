"""Tests for the CommunityService and SlackCommunityService.

All Slack API calls are mocked. Tests verify:
- invite/remove/reactivate logic
- Slack API call flow
- Audit log creation
- Email sending when user not found in Slack
- Error handling
- Task-level invite/remove/reactivate (moved from playwright_tests/test_community_slack.py)
- Email matcher task
- Subscription deletion triggers removal
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from community.models import CommunityAuditLog
from community.services import get_community_service
from community.services.base import CommunityService
from community.services.slack import SlackAPIError, SlackCommunityService
from payments.models import Tier

MOCK_CHANNELS = ["C001", "C002"]


class CommunityServiceInterfaceTest(TestCase):
    """Test that CommunityService is an abstract interface."""

    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            CommunityService()

    def test_get_community_service_returns_slack(self):
        service = get_community_service()
        self.assertIsInstance(service, SlackCommunityService)


class SlackCommunityServiceEnvironmentTest(TestCase):
    @override_settings(
        SLACK_ENVIRONMENT="development",
        SLACK_COMMUNITY_CHANNEL_IDS=["CPROD"],
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["CDEV"],
    )
    def test_default_channels_use_environment_resolver(self):
        service = SlackCommunityService(bot_token="xoxb-test")
        self.assertEqual(service.channel_ids, ["CDEV"])

    @override_settings(
        SLACK_ENVIRONMENT="development",
        SLACK_DEV_COMMUNITY_CHANNEL_IDS=["CDEV"],
    )
    def test_explicit_channel_ids_still_win(self):
        service = SlackCommunityService(
            bot_token="xoxb-test",
            channel_ids=["CEXPLICIT"],
        )
        self.assertEqual(service.channel_ids, ["CEXPLICIT"])


@override_settings(SLACK_ENABLED=True)
@tag('core')
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


@override_settings(SLACK_ENABLED=True)
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


@override_settings(SLACK_ENABLED=True)
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


@override_settings(SLACK_ENABLED=True)
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
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
@tag('core')
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
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
@tag('core')
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
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_COMMUNITY_CHANNEL_IDS=["C001", "C002"],
)
@tag('core')
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


# ---------------------------------------------------------------------------
# Task-level tests (moved from playwright_tests/test_community_slack.py)
# Scenarios 6-9 and 11 exercised backend tasks directly with no browser.
# ---------------------------------------------------------------------------


def _ensure_tiers():
    """Ensure the four membership tiers exist."""
    TIERS = [
        {"slug": "free", "name": "Free", "level": 0},
        {"slug": "basic", "name": "Basic", "level": 10},
        {"slug": "main", "name": "Main", "level": 20},
        {"slug": "premium", "name": "Premium", "level": 30},
    ]
    for t in TIERS:
        Tier.objects.get_or_create(slug=t["slug"], defaults=t)


def _create_user(email, tier_slug="free"):
    """Create a user with the given tier."""
    _ensure_tiers()
    user, _ = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password("testpass123")
    user.tier = Tier.objects.get(slug=tier_slug)
    user.email_verified = True
    user.save()
    return user


class CommunityInviteTaskTest(TestCase):
    """Scenario 6: New Main member receives community invite after checkout.

    Moved from playwright_tests/test_community_slack.py.
    """

    def setUp(self):
        _ensure_tiers()
        CommunityAuditLog.objects.all().delete()

    def test_checkout_completed_triggers_invite_and_audit_log(self):
        """community_invite_task calls service.invite and creates audit log."""
        from community.tasks.hooks import community_invite_task

        user = _create_user("new-main@test.com", tier_slug="main")

        mock_service = MagicMock()
        with patch(
            "community.tasks.hooks.get_community_service",
            return_value=mock_service,
        ):
            community_invite_task(user.pk)

        mock_service.invite.assert_called_once()
        call_user = mock_service.invite.call_args[0][0]
        self.assertEqual(call_user.pk, user.pk)

    def test_invite_via_service_creates_audit_log_with_slack_id(self):
        """Real service (mocked Slack API) creates audit log with slack_user_id."""
        user = _create_user("invite-audit@test.com", tier_slug="main")
        user.slack_user_id = ""
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.services.slack.SlackCommunityService._api_call"
        ) as mock_api:
            mock_api.return_value = {
                "ok": True,
                "user": {"id": "U67890SLACK"},
            }
            service = SlackCommunityService(
                bot_token="xoxb-test", channel_ids=["C001", "C002"]
            )
            service.invite(user)

        logs = CommunityAuditLog.objects.filter(user=user, action="invite")
        self.assertEqual(logs.count(), 1)
        details = json.loads(logs.first().details)
        self.assertEqual(details["slack_user_id"], "U67890SLACK")

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "U67890SLACK")

    def test_invite_sends_email_when_slack_user_not_found(self):
        """If user email not in Slack, an invite email is sent."""
        user = _create_user("no-slack@test.com", tier_slug="main")
        user.slack_user_id = ""
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.services.slack.SlackCommunityService._api_call"
        ) as mock_api:
            mock_api.side_effect = SlackAPIError(
                "users_not_found",
                method="users.lookupByEmail",
                error_code="users_not_found",
            )
            with patch("community.services.slack.send_mail") as mock_mail:
                service = SlackCommunityService(
                    bot_token="xoxb-test", channel_ids=["C001"],
                )
                service.invite(user)
                mock_mail.assert_called_once()

        logs = CommunityAuditLog.objects.filter(user=user, action="invite")
        self.assertEqual(logs.count(), 1)
        details = json.loads(logs.first().details)
        self.assertEqual(details["status"], "email_sent")
        self.assertEqual(details["reason"], "slack_user_not_found")


class CommunityDowngradeRemovalTaskTest(TestCase):
    """Scenario 7: Downgrade loses community access.

    Moved from playwright_tests/test_community_slack.py.
    """

    def setUp(self):
        _ensure_tiers()
        CommunityAuditLog.objects.all().delete()

    def test_scheduled_removal_removes_user_from_channels(self):
        """Scheduled removal calls service.remove for downgraded user."""
        from community.tasks.removal import scheduled_community_removal

        user = _create_user("downgrade@test.com", tier_slug="main")
        user.slack_user_id = "UDOWNGRADE"
        user.save(update_fields=["slack_user_id"])

        # Simulate downgrade to Basic
        basic_tier = Tier.objects.get(slug="basic")
        user.tier = basic_tier
        user.save(update_fields=["tier"])

        with patch(
            "community.tasks.removal.get_community_service"
        ) as mock_get_svc:
            mock_service = MagicMock()
            mock_get_svc.return_value = mock_service
            scheduled_community_removal(user.pk)

        mock_service.remove.assert_called_once()
        call_user = mock_service.remove.call_args[0][0]
        self.assertEqual(call_user.pk, user.pk)

    def test_scheduled_removal_skips_if_user_resubscribed(self):
        """If user re-subscribed before removal ran, skip removal."""
        from community.tasks.removal import scheduled_community_removal

        user = _create_user("resubbed@test.com", tier_slug="main")
        user.slack_user_id = "URESUBBED"
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.tasks.removal.get_community_service"
        ) as mock_get_svc:
            mock_service = MagicMock()
            mock_get_svc.return_value = mock_service
            scheduled_community_removal(user.pk)

        mock_service.remove.assert_not_called()


class CommunityReactivateTaskTest(TestCase):
    """Scenario 8: Re-subscribe regains community access.

    Moved from playwright_tests/test_community_slack.py.
    """

    def setUp(self):
        _ensure_tiers()
        CommunityAuditLog.objects.all().delete()

    def test_reactivate_re_adds_user_to_channels(self):
        """Reactivate creates audit log with correct channel data."""
        user = _create_user("reactivate@test.com", tier_slug="main")
        user.slack_user_id = "UREACTIVATE"
        user.save(update_fields=["slack_user_id"])

        with patch(
            "community.services.slack.SlackCommunityService._api_call"
        ) as mock_api:
            mock_api.return_value = {"ok": True}
            service = SlackCommunityService(
                bot_token="xoxb-test", channel_ids=["C001", "C002"]
            )
            service.reactivate(user)

        logs = CommunityAuditLog.objects.filter(
            user=user, action="reactivate"
        )
        self.assertEqual(logs.count(), 1)
        details = json.loads(logs.first().details)
        self.assertEqual(details["slack_user_id"], "UREACTIVATE")
        self.assertEqual(len(details["channels"]), 2)

    def test_reactivate_task_calls_service(self):
        """community_reactivate_task delegates to service.reactivate."""
        from community.tasks.hooks import community_reactivate_task

        user = _create_user("reactivate-task@test.com", tier_slug="main")

        with patch(
            "community.tasks.hooks.get_community_service"
        ) as mock_get_svc:
            mock_service = MagicMock()
            mock_get_svc.return_value = mock_service
            community_reactivate_task(user.pk)

        mock_service.reactivate.assert_called_once()
        call_user = mock_service.reactivate.call_args[0][0]
        self.assertEqual(call_user.pk, user.pk)


class CommunityEmailMatcherTaskTest(TestCase):
    """Scenario 9: Email matcher links a new Slack user.

    Moved from playwright_tests/test_community_slack.py.
    """

    def setUp(self):
        _ensure_tiers()
        CommunityAuditLog.objects.all().delete()

    def test_email_matcher_finds_and_links_user(self):
        """Email matcher finds user in Slack, stores ID, adds to channels."""
        from community.tasks.email_matcher import match_community_emails

        user = _create_user("matcher-test@test.com", tier_slug="main")
        user.slack_user_id = ""
        user.save(update_fields=["slack_user_id"])

        mock_service = MagicMock()
        mock_service.lookup_user_by_email.return_value = "UMATCHED123"
        mock_service.add_to_channels.return_value = [
            {"channel": "C001", "ok": True},
            {"channel": "C002", "ok": True},
        ]

        with patch(
            "community.tasks.email_matcher.get_community_service",
            return_value=mock_service,
        ):
            result = match_community_emails()

        self.assertGreaterEqual(result["matched"], 1)

        user.refresh_from_db()
        self.assertEqual(user.slack_user_id, "UMATCHED123")

        logs = CommunityAuditLog.objects.filter(user=user, action="link")
        self.assertEqual(logs.count(), 1)
        details = json.loads(logs.first().details)
        self.assertEqual(details["slack_user_id"], "UMATCHED123")
        self.assertEqual(details["source"], "email_matcher")
        self.assertEqual(len(details["channels"]), 2)


class SubscriptionDeletionRemovalTest(TestCase):
    """Scenario 11: Subscription deletion triggers community removal.

    Moved from playwright_tests/test_community_slack.py.
    """

    def setUp(self):
        _ensure_tiers()
        CommunityAuditLog.objects.all().delete()

    def test_subscription_deleted_reverts_tier_and_triggers_removal(self):
        """handle_subscription_deleted reverts tier and enqueues removal task."""
        user = _create_user("deleted-sub@test.com", tier_slug="main")
        user.stripe_customer_id = "cus_test_deletion"
        user.subscription_id = "sub_test_deletion"
        user.slack_user_id = "UDELETION"
        user.billing_period_end = timezone.now()
        user.save(update_fields=[
            "stripe_customer_id", "subscription_id",
            "slack_user_id", "billing_period_end",
        ])

        self.assertEqual(user.tier.slug, "main")

        with patch("jobs.tasks.async_task") as mock_async:
            from payments.services import handle_subscription_deleted
            handle_subscription_deleted({
                "id": "sub_test_deletion",
                "customer": "cus_test_deletion",
            })

        user.refresh_from_db()
        self.assertEqual(user.tier.slug, "free")
        self.assertEqual(user.subscription_id, "")

        mock_async.assert_called()
        call_args_list = mock_async.call_args_list
        community_call = None
        for c in call_args_list:
            task_name = c[0][0] if c[0] else ""
            if "community" in task_name and "remove" in task_name:
                community_call = c
                break
        self.assertIsNotNone(
            community_call,
            f"Expected community remove task to be enqueued. Calls: {call_args_list}",
        )
