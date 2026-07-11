"""Dashboard card dismissal rendering tests (issue #1129).

Covers the server-side rendering side of the three dismissable/stale
cards on the authenticated dashboard (``templates/content/dashboard.html``):

- Part 1: the onboarding nudge renders a dismiss control and is hidden
  once ``onboarding_prompt`` is in the member's ``dashboard_dismissals``.
- Part 2: the Join-Slack card renders a dismiss control ONLY on the
  dashboard (never on ``/account/``) and is hidden once ``slack_join`` is
  dismissed; the ``/account/`` Slack surface is unaffected.

The client-side POST-then-remove flow is exercised by Playwright.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from tests.fixtures import TierSetupMixin

User = get_user_model()

TEST_INVITE_URL = "https://join.slack.com/t/test/shared_invite/abc"


@override_settings(ONBOARDING_AI_ENABLED="false")
@tag("core")
class DashboardOnboardingDismissTest(TierSetupMixin, TestCase):
    """Part 1: onboarding nudge dismiss control + persisted hide."""

    def _login_basic(self, email="ob@test.com", dismissals=None):
        user = User.objects.create_user(
            email=email, password="pw", tier=self.basic_tier,
        )
        if dismissals is not None:
            user.dashboard_dismissals = dismissals
            user.save(update_fields=["dashboard_dismissals"])
        self.client.login(email=email, password="pw")
        return user

    def test_banner_renders_dismiss_control_and_cta(self):
        self._login_basic()
        response = self.client.get("/")
        self.assertContains(response, 'data-testid="onboarding-prompt"')
        self.assertContains(
            response, 'data-testid="onboarding-prompt-dismiss"',
        )
        self.assertContains(response, 'aria-label="Dismiss"')
        # The Start onboarding CTA stays present and unchanged.
        self.assertContains(response, 'data-testid="onboarding-prompt-cta"')

    def test_banner_hidden_when_dismissed(self):
        self._login_basic(dismissals=["onboarding_prompt"])
        response = self.client.get("/")
        self.assertNotContains(response, 'data-testid="onboarding-prompt"')
        self.assertNotContains(
            response, 'data-testid="onboarding-prompt-dismiss"',
        )

    def test_context_flag_false_when_dismissed(self):
        self._login_basic(dismissals=["onboarding_prompt"])
        response = self.client.get("/")
        self.assertFalse(response.context["show_onboarding_prompt"])

    def test_unrelated_dismissal_does_not_hide_banner(self):
        # A slack_join dismissal must not affect the onboarding nudge.
        self._login_basic(dismissals=["slack_join"])
        response = self.client.get("/")
        self.assertContains(response, 'data-testid="onboarding-prompt"')


@override_settings(ONBOARDING_AI_ENABLED="false")
@tag("core")
class DashboardSlackDismissTest(TierSetupMixin, TestCase):
    """Part 2: Slack Join card dismiss is dashboard-scoped."""

    def _login_main(self, email="sl@test.com", dismissals=None):
        user = User.objects.create_user(
            email=email, password="pw", tier=self.main_tier,
        )
        if dismissals is not None:
            user.dashboard_dismissals = dismissals
            user.save(update_fields=["dashboard_dismissals"])
        self.client.login(email=email, password="pw")
        return user

    def test_dashboard_join_card_has_dismiss_control(self):
        self._login_main()
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/")
        self.assertContains(response, 'data-testid="slack-account-card-join"')
        self.assertContains(
            response, 'data-testid="slack-account-card-dismiss"',
        )
        self.assertContains(response, 'absolute right-3 top-3')
        self.assertTrue(response.context["slack_card_dismissable"])

    def test_dashboard_join_card_hidden_when_dismissed(self):
        self._login_main(dismissals=["slack_join"])
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/")
        self.assertFalse(response.context["show_slack_join"])
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )
        self.assertNotContains(
            response, 'data-testid="slack-account-card-dismiss"',
        )

    def test_account_join_card_has_no_dismiss_control(self):
        # The same partial on /account/ must never render a dismiss button.
        self._login_main("acct@test.com")
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertContains(response, 'data-testid="slack-account-card-join"')
        self.assertNotContains(
            response, 'data-testid="slack-account-card-dismiss"',
        )

    def test_account_join_card_unaffected_by_dashboard_dismissal(self):
        # A dashboard slack_join dismissal must not hide the /account/ card.
        self._login_main("both@test.com", dismissals=["slack_join"])
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertTrue(response.context["show_slack_join"])
        self.assertContains(response, 'data-testid="slack-account-card-join"')
        self.assertNotContains(
            response, 'data-testid="slack-account-card-dismiss"',
        )

    def test_connected_state_never_has_dismiss_control(self):
        user = self._login_main("conn@test.com")
        user.slack_member = True
        user.slack_user_id = "U0CONN123"
        user.save(update_fields=["slack_member", "slack_user_id"])
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertNotContains(
            response, 'data-testid="slack-account-card-dismiss"',
        )
