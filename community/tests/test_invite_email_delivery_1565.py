"""Slack invite email is actually delivered through SES (issue #1565).

Before this change ``_send_invite_email`` called ``django.core.mail.send_mail``
with ``fail_silently=True``. ``EMAIL_BACKEND`` is unset, so it resolved to SMTP
on ``localhost:25``, which is refused in ECS: every invite email the platform
believed it had sent was discarded. These tests pin the SES path and the
honest audit status for the shared, non-Maven callers.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from accounts.models import User
from community.models import CommunityAuditLog
from community.services.staff_notifications import _build_removal_context
from community.tasks.hooks import community_invite_task
from email_app.services.email_service import EmailService, EmailServiceError
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from payments.models import Tier
from tests.fixtures import set_membership

SLACK_SETTINGS = dict(
    SLACK_ENABLED=True,
    SLACK_BOT_TOKEN="xoxb-test",
    SLACK_ENVIRONMENT="development",
    SLACK_DEV_COMMUNITY_CHANNEL_IDS=["C001"],
    SLACK_INVITE_URL="https://join.slack.com/test",
)


@override_settings(**SLACK_SETTINGS)
class PaidCheckoutInviteEmailTest(TestCase):
    """A paying Main member who is not in Slack actually receives the invite."""

    def setUp(self):
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self.user = User.objects.create_user(email="paid@example.com", password="x", first_name="Ada")
        set_membership(self.user, tier=Tier.objects.get(slug="main"))

    @patch(
        "community.services.slack.SlackCommunityService.lookup_user_by_email",
        return_value=None,
    )
    @patch.object(EmailService, "_send_ses", return_value="ses-message-id")
    def test_invite_task_sends_community_invite_with_the_gated_link(
        self, send_ses, _lookup,
    ):
        with self.assertLogs("community.services.slack", level="INFO") as logs:
            community_invite_task(self.user.pk)

        self.assertEqual(send_ses.call_count, 1)
        self.assertEqual(send_ses.call_args.kwargs["email_type"], "community_invite")
        self.assertEqual(send_ses.call_args.args[0], "paid@example.com")
        html = send_ses.call_args.args[2]
        self.assertIn("https://aishippinglabs.com/community/slack", html)
        self.assertNotIn("https://join.slack.com/test", html)

        details = CommunityAuditLog.objects.get(user=self.user, action="invite").details
        self.assertIn('"status": "email_sent"', details)
        rendered_logs = "\n".join(logs.output)
        self.assertIn(
            f"action=invite outcome=email_sent user_id={self.user.pk}",
            rendered_logs,
        )
        self.assertNotIn(self.user.email, rendered_logs)

    @patch(
        "community.services.slack.SlackCommunityService.lookup_user_by_email",
        return_value=None,
    )
    @patch.object(
        EmailService,
        "send",
        side_effect=EmailServiceError("SES rejected paid@example.com U_PRIVATE"),
    )
    def test_a_failed_send_is_logged_and_audited_as_email_failed(
        self, _send, _lookup,
    ):
        with self.assertLogs("community.services.slack", level="ERROR") as logs:
            community_invite_task(self.user.pk)

        rendered_logs = "\n".join(logs.output)
        self.assertIn("error_class=EmailServiceError", rendered_logs)
        self.assertIn(f"user_id={self.user.pk}", rendered_logs)
        self.assertNotIn(self.user.email, rendered_logs)
        self.assertNotIn("U_PRIVATE", rendered_logs)
        details = CommunityAuditLog.objects.get(user=self.user, action="invite").details
        self.assertIn('"status": "email_failed"', details)
        self.assertNotIn("email_sent", details)


class RemovalContextTrailingSlashTest(TestCase):
    """A trailing-slash SITE_BASE_URL no longer yields //studio/users/ (#1565)."""

    def setUp(self):
        IntegrationSetting.objects.update_or_create(
            key="SITE_BASE_URL",
            defaults={"value": "https://aishippinglabs.com/"},
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_studio_user_url_has_exactly_one_studio_users_segment(self):
        user = User.objects.create_user(email="removed@example.com", password="x")

        context = _build_removal_context(user, "Cohort 1", "Buildcamp", user.email)

        self.assertEqual(
            context["studio_user_url"],
            f"https://aishippinglabs.com/studio/users/{user.pk}/",
        )
        self.assertNotIn("//studio", context["studio_user_url"])
        self.assertEqual(context["studio_user_url"].count("/studio/users/"), 1)
