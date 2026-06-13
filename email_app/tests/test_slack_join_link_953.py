"""Issue #953: paid welcome / invite emails link to the gated
/community/slack redirect, never the raw SLACK_INVITE_URL.

- cofounder_welcome (Main) and premium_welcome (Premium) render the
  gated Join Slack link (absolute, built from the injected site_url).
- basic_welcome (Basic) contains no Slack link.
- community_invite uses the gated link, not the raw invite URL.
"""

from django.test import TestCase, override_settings

from accounts.models import User
from email_app.services.email_service import EmailService

RAW_INVITE = "https://join.slack.com/t/secret/shared_invite/leak"


@override_settings(SITE_BASE_URL="https://aishippinglabs.com")
class WelcomeSlackLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="welcome@test.com", first_name="Ada",
        )

    def _render(self, template_name):
        _, body_html = EmailService()._render_template(
            template_name, self.user, {"user_first_name": "Ada"},
        )
        return body_html

    @override_settings(SLACK_INVITE_URL=RAW_INVITE)
    def test_cofounder_welcome_has_gated_link(self):
        html = self._render("cofounder_welcome")
        self.assertIn(
            'href="https://aishippinglabs.com/community/slack"', html,
        )
        self.assertNotIn(RAW_INVITE, html)

    @override_settings(SLACK_INVITE_URL=RAW_INVITE)
    def test_premium_welcome_has_gated_link(self):
        html = self._render("premium_welcome")
        self.assertIn(
            'href="https://aishippinglabs.com/community/slack"', html,
        )
        self.assertNotIn(RAW_INVITE, html)

    @override_settings(SLACK_INVITE_URL=RAW_INVITE)
    def test_generic_welcome_has_gated_link(self):
        # Legacy generic `welcome` template (#954): no raw invite URL,
        # only the gated /community/slack redirect.
        html = self._render("welcome")
        self.assertIn(
            'href="https://aishippinglabs.com/community/slack"', html,
        )
        self.assertNotIn(RAW_INVITE, html)

    @override_settings(SLACK_INVITE_URL=RAW_INVITE)
    def test_basic_welcome_has_no_slack_link(self):
        html = self._render("basic_welcome")
        self.assertNotIn("/community/slack", html)
        self.assertNotIn(RAW_INVITE, html)


@override_settings(
    SITE_BASE_URL="https://aishippinglabs.com",
    SLACK_INVITE_URL=RAW_INVITE,
)
class CommunityInviteSlackLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="invite@test.com", first_name="Ada",
        )

    def test_community_invite_uses_gated_link(self):
        # Even if a legacy caller still passes slack_invite_url, the
        # template no longer references it — the gated link wins.
        _, body_html = EmailService()._render_template(
            "community_invite", self.user,
            {"slack_invite_url": RAW_INVITE},
        )
        self.assertIn(
            'href="https://aishippinglabs.com/community/slack"', body_html,
        )
        self.assertNotIn(RAW_INVITE, body_html)
