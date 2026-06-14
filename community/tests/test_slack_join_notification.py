"""Tests for the staff "known user joined Slack" heads-up (issue #959).

Two layers:

1. ``notify_slack_join`` directly — toggle gating, recipient resolution,
   Slack post gating, content, error-swallowing.
2. The ``refresh_slack_membership`` trigger — the genuine-transition rule
   plus the CRITICAL backfill-safety guards (first-ever check and existing
   member re-check must NOT notify).
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import User
from community.services import staff_notifications
from community.tasks.slack_membership import refresh_slack_membership
from payments.models import Tier


def _tier(level):
    return Tier.objects.get(level=level)


def _cfg(**overrides):
    """Build a get_config side_effect with sensible defaults."""
    defaults = {
        "STAFF_SIGNUP_NOTIFY_EMAIL": "founders@aishippinglabs.test",
        "STAFF_SIGNUP_NOTIFY_CHANNEL_ID": "C0SLACKJOIN",
        "SLACK_BOT_TOKEN": "xoxb-test-token",
        "SITE_BASE_URL": "https://example.test",
    }
    defaults.update(overrides)

    def _get(key, default=""):
        if key in defaults:
            return defaults[key]
        return default if default is not None else ""

    return _get


def _enabled(**flags):
    """Build an is_enabled side_effect. Defaults: toggle on, Slack on."""
    defaults = {
        "STAFF_SLACK_JOIN_NOTIFY_ENABLED": True,
        "SLACK_ENABLED": True,
    }
    defaults.update(flags)

    def _is(key):
        return bool(defaults.get(key, False))

    return _is


@tag("core")
class NotifySlackJoinHelperTest(TestCase):
    """Direct tests of ``notify_slack_join`` (no task plumbing)."""

    STAFF_EMAIL = "founders@aishippinglabs.test"
    SLACK_CHANNEL = "C0SLACKJOIN"

    def setUp(self):
        self.user = User.objects.create_user(
            email="joiner@test.com",
            first_name="Alex",
            last_name="Grigorev",
            tier=_tier(20),
        )

    def test_happy_path_sends_email_and_slack(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(),
        ), patch(
            "community.services.staff_notifications.site_base_url",
            return_value="https://example.test",
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_slack_join(self.user)

        mock_email.assert_called_once()
        self.assertEqual(mock_email.call_args.args[0], self.STAFF_EMAIL)
        ctx = mock_email.call_args.args[1]
        self.assertEqual(ctx["user_email"], "joiner@test.com")
        self.assertEqual(ctx["user_full_name"], "Alex Grigorev")
        self.assertEqual(ctx["user_id"], self.user.pk)
        self.assertEqual(ctx["tier_name"], _tier(20).name)
        self.assertEqual(
            ctx["studio_user_url"],
            f"https://example.test/studio/users/{self.user.pk}/",
        )

        mock_slack.assert_called_once()
        slack_payload = mock_slack.call_args.kwargs["json"]
        self.assertEqual(slack_payload["channel"], self.SLACK_CHANNEL)
        self.assertIn("say hi", slack_payload["text"].lower())
        self.assertIn("joiner@test.com", slack_payload["text"])
        self.assertIn(
            f"https://example.test/studio/users/{self.user.pk}/",
            slack_payload["text"],
        )

    def test_toggle_off_suppresses_everything(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(STAFF_SLACK_JOIN_NOTIFY_ENABLED=False),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            staff_notifications.notify_slack_join(self.user)

        mock_email.assert_not_called()
        mock_slack.assert_not_called()

    def test_blank_staff_email_skips_email_but_runs_slack(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(STAFF_SIGNUP_NOTIFY_EMAIL=""),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            staff_notifications.notify_slack_join(self.user)

        mock_email.assert_not_called()
        mock_slack.assert_called_once()

    def test_slack_disabled_skips_slack_but_sends_email(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(SLACK_ENABLED=False),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            staff_notifications.notify_slack_join(self.user)

        mock_email.assert_called_once()
        mock_slack.assert_not_called()

    def test_blank_channel_skips_slack_but_sends_email(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(STAFF_SIGNUP_NOTIFY_CHANNEL_ID=""),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            staff_notifications.notify_slack_join(self.user)

        mock_email.assert_called_once()
        mock_slack.assert_not_called()

    def test_empty_bot_token_skips_slack_post(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(SLACK_BOT_TOKEN=""),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            staff_notifications.notify_slack_join(self.user)

        mock_email.assert_called_once()
        mock_slack.assert_not_called()

    def test_email_failure_does_not_break_or_block_slack(self):
        with patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications,
            "_send_slack_join_notification",
            side_effect=RuntimeError("SES down"),
        ):
            mock_slack.return_value.json.return_value = {"ok": True}
            mock_slack.return_value.status_code = 200

            # Must not raise despite the email failure.
            staff_notifications.notify_slack_join(self.user)

        # Slack still fired even though the email path threw.
        mock_slack.assert_called_once()

    def test_renders_real_email_template(self):
        """The transactional template renders all required fields."""
        from types import SimpleNamespace

        from email_app.services.email_service import EmailService

        with patch(
            "community.services.staff_notifications.site_base_url",
            return_value="https://example.test",
        ):
            ctx = staff_notifications._build_slack_join_context(self.user)
        # Surrogate staff recipient identical to the real send path.
        recipient = SimpleNamespace(
            email=self.STAFF_EMAIL,
            first_name="",
            email_verified=True,
            unsubscribed=False,
            pk=0,
        )

        with patch(
            "email_app.services.email_service.site_base_url",
            return_value="https://example.test",
        ):
            (
                subject,
                body_html,
                _footer,
            ) = EmailService()._render_template_with_footer(
                "slack_join_notification", recipient, ctx,
            )

        self.assertIn("say hi", subject.lower())
        self.assertIn("joiner@test.com", subject)
        # The joining user's identity, not the staff mailbox, is the subject.
        self.assertIn("joiner@test.com", body_html)
        self.assertIn("Alex Grigorev", body_html)
        self.assertIn(str(self.user.pk), body_html)
        self.assertIn(_tier(20).name, body_html)
        self.assertIn("say hi", body_html.lower())
        self.assertIn(
            f"https://example.test/studio/users/{self.user.pk}/", body_html
        )

    def test_slug_classified_transactional(self):
        from email_app.services.email_classification import (
            EMAIL_KIND_TRANSACTIONAL,
            classify_email_type,
        )

        self.assertEqual(
            classify_email_type("slack_join_notification"),
            EMAIL_KIND_TRANSACTIONAL,
        )


@tag("core")
class RefreshSlackMembershipJoinTriggerTest(TestCase):
    """Tests for the join-notification trigger inside refresh_slack_membership."""

    def _make_user(self, email, **extra):
        extra.setdefault("tier", _tier(20))
        return User.objects.create_user(email=email, **extra)

    def _run(self, outcome=("member", "U_X"), enabled=True):
        """Run the refresh with a mocked service + patched notifier.

        Returns (result_dict, mock_notify).
        """
        svc = MagicMock()
        svc.check_workspace_membership.return_value = outcome
        with patch(
            "community.tasks.slack_membership.get_community_service",
            return_value=svc,
        ), patch(
            "community.tasks.slack_membership.notify_slack_join"
        ) as mock_notify:
            result = refresh_slack_membership(sleep_seconds=0)
        return result, mock_notify

    def test_genuine_join_notifies(self):
        """Prior non-member (slack_checked_at set) now member -> notify."""
        past = timezone.now() - timezone.timedelta(days=30)
        user = self._make_user(
            "genuine@test.com", slack_member=False, slack_checked_at=past,
        )

        result, mock_notify = self._run(outcome=("member", "U_X"))

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        mock_notify.assert_called_once_with(user)
        self.assertEqual(result["members"], 1)

    def test_first_ever_check_member_does_not_notify(self):
        """BACKFILL SAFETY: slack_checked_at NULL -> member seeds, no notify."""
        user = self._make_user(
            "firstcheck@test.com", slack_member=False,
        )
        self.assertIsNone(user.slack_checked_at)

        _result, mock_notify = self._run(outcome=("member", "U_X"))

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        self.assertIsNotNone(user.slack_checked_at)
        mock_notify.assert_not_called()

    def test_existing_member_recheck_does_not_notify(self):
        """BACKFILL SAFETY: already a member, re-checked -> no notify."""
        past = timezone.now() - timezone.timedelta(days=30)
        user = self._make_user(
            "existing@test.com", slack_member=True, slack_checked_at=past,
        )

        _result, mock_notify = self._run(outcome=("member", "U_X"))

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        mock_notify.assert_not_called()

    def test_toggle_off_no_email_no_slack_but_membership_updates(self):
        """Toggle off: membership flips but no notification side effects."""
        past = timezone.now() - timezone.timedelta(days=30)
        user = self._make_user(
            "toggleoff@test.com", slack_member=False, slack_checked_at=past,
        )

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ("member", "U_X")
        with patch(
            "community.tasks.slack_membership.get_community_service",
            return_value=svc,
        ), patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(STAFF_SLACK_JOIN_NOTIFY_ENABLED=False),
        ), patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack, patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email:
            refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        mock_email.assert_not_called()
        mock_slack.assert_not_called()

    def test_no_staff_mailbox_completes_without_email(self):
        """Genuine join, toggle on, no recipient + no channel: no crash."""
        past = timezone.now() - timezone.timedelta(days=30)
        self._make_user(
            "nomailbox@test.com", slack_member=False, slack_checked_at=past,
        )

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ("member", "U_X")
        with patch(
            "community.tasks.slack_membership.get_community_service",
            return_value=svc,
        ), patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(
                STAFF_SIGNUP_NOTIFY_EMAIL="",
                STAFF_SIGNUP_NOTIFY_CHANNEL_ID="",
            ),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(),
        ), patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email, patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            result = refresh_slack_membership(sleep_seconds=0)

        mock_email.assert_not_called()
        mock_slack.assert_not_called()
        self.assertEqual(result["members"], 1)

    def test_slack_disabled_sends_email_not_slack_from_task(self):
        """Genuine join end-to-end: email sends, no chat.postMessage."""
        past = timezone.now() - timezone.timedelta(days=30)
        self._make_user(
            "slackoff@test.com", slack_member=False, slack_checked_at=past,
        )

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ("member", "U_X")
        with patch(
            "community.tasks.slack_membership.get_community_service",
            return_value=svc,
        ), patch(
            "community.services.staff_notifications.get_config",
            side_effect=_cfg(),
        ), patch(
            "community.services.staff_notifications.is_enabled",
            side_effect=_enabled(SLACK_ENABLED=False),
        ), patch.object(
            staff_notifications, "_send_slack_join_notification"
        ) as mock_email, patch(
            "community.services.staff_notifications.requests.post"
        ) as mock_slack:
            refresh_slack_membership(sleep_seconds=0)

        mock_email.assert_called_once()
        mock_slack.assert_not_called()

    def test_notifier_failure_never_breaks_refresh(self):
        """An exploding notifier is logged + swallowed; membership updates."""
        past = timezone.now() - timezone.timedelta(days=30)
        user = self._make_user(
            "boom@test.com", slack_member=False, slack_checked_at=past,
        )

        svc = MagicMock()
        svc.check_workspace_membership.return_value = ("member", "U_X")
        with patch(
            "community.tasks.slack_membership.get_community_service",
            return_value=svc,
        ), patch(
            "community.tasks.slack_membership.notify_slack_join",
            side_effect=RuntimeError("notifier exploded"),
        ):
            result = refresh_slack_membership(sleep_seconds=0)

        user.refresh_from_db()
        self.assertTrue(user.slack_member)
        self.assertEqual(result["members"], 1)
        self.assertIn("transitions", result)

    def test_leave_then_rejoin_notifies_again(self):
        """A user flipped to non-member who rejoins notifies again."""
        past = timezone.now() - timezone.timedelta(days=30)
        user = self._make_user(
            "rejoin@test.com", slack_member=False, slack_checked_at=past,
        )

        _result, mock_notify = self._run(outcome=("member", "U_X"))
        mock_notify.assert_called_once_with(user)

    def test_not_member_outcome_never_notifies(self):
        """The join notifier only fires on the member branch."""
        user = self._make_user(
            "leaver@test.com", slack_member=True,
            slack_checked_at=timezone.now() - timezone.timedelta(days=30),
        )

        _result, mock_notify = self._run(outcome=("not_member", None))

        user.refresh_from_db()
        self.assertFalse(user.slack_member)
        mock_notify.assert_not_called()
