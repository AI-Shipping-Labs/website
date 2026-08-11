"""Dashboard getting-started dismissal rendering tests.

Onboarding and Slack are cumulative checklist rows. They cannot be closed
individually, and legacy dismissal markers do not hide unfinished work. The
whole checklist gets its close control only after every applicable row is done.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag

from tests.fixtures import TierSetupMixin

User = get_user_model()

TEST_INVITE_URL = "https://join.slack.com/t/test/shared_invite/abc"


@override_settings(ONBOARDING_AI_ENABLED="false")
@tag("core")
class DashboardOnboardingDismissTest(TierSetupMixin, TestCase):
    """Incomplete onboarding stays visible without a close control."""

    def _login_basic(self, email="ob@test.com", dismissals=None):
        user = User.objects.create_user(
            email=email, password="pw", tier=self.basic_tier,
        )
        if dismissals is not None:
            user.dashboard_dismissals = dismissals
            user.save(update_fields=["dashboard_dismissals"])
        self.client.login(email=email, password="pw")
        return user

    def test_onboarding_row_renders_cta_without_dismiss_control(self):
        self._login_basic()
        response = self.client.get("/")
        self.assertContains(response, 'data-testid="onboarding-prompt"')
        self.assertNotContains(
            response, 'data-testid="free-activation-dismiss"',
        )
        self.assertContains(response, 'data-testid="onboarding-prompt-cta"')

    def test_legacy_onboarding_dismissal_does_not_hide_unfinished_row(self):
        self._login_basic(dismissals=["onboarding_prompt"])
        response = self.client.get("/")
        self.assertContains(response, 'data-testid="onboarding-prompt"')

    def test_context_flag_remains_true_with_legacy_dismissal(self):
        self._login_basic(dismissals=["onboarding_prompt"])
        response = self.client.get("/")
        self.assertTrue(response.context["show_onboarding_prompt"])

    def test_unrelated_dismissal_does_not_hide_banner(self):
        # A slack_join dismissal must not affect the onboarding nudge.
        self._login_basic(dismissals=["slack_join"])
        response = self.client.get("/")
        self.assertContains(response, 'data-testid="onboarding-prompt"')


@override_settings(ONBOARDING_AI_ENABLED="false")
@tag("core")
class DashboardSlackDismissTest(TierSetupMixin, TestCase):
    """Slack is an unfinished Main checklist row, not a separate card."""

    def _login_main(self, email="sl@test.com", dismissals=None):
        user = User.objects.create_user(
            email=email, password="pw", tier=self.main_tier,
        )
        if dismissals is not None:
            user.dashboard_dismissals = dismissals
            user.save(update_fields=["dashboard_dismissals"])
        self.client.login(email=email, password="pw")
        return user

    def test_dashboard_join_row_has_no_individual_dismiss_control(self):
        self._login_main()
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/")
        self.assertContains(response, 'data-testid="slack-account-card-join"')
        self.assertNotContains(
            response, 'data-testid="slack-account-card-dismiss"',
        )
        self.assertNotContains(
            response, 'data-testid="free-activation-dismiss"',
        )

    def test_legacy_slack_dismissal_does_not_hide_unfinished_row(self):
        self._login_main(dismissals=["slack_join"])
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/")
        self.assertTrue(response.context["show_slack_join"])
        self.assertContains(response, 'data-testid="slack-account-card-join"')
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
