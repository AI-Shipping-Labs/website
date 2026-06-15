"""Issue #971: dashboard Join Slack card uses effective (override-aware) tier.

Policy (Option A): an active, non-expired ``TierOverride`` grants
Slack/community access. The dashboard Join Slack card must resolve via
``content.access.get_user_level`` — the same predicate as the join
redirect (community/views.py) and the membership-sync job
(slack_membership.main_plus_q) — not the raw base ``user.tier.level``.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from tests.fixtures import TierSetupMixin

User = get_user_model()

TEST_INVITE_URL = "https://join.slack.com/t/test/shared_invite/abc"


class DashboardSlackOverrideCardTest(TierSetupMixin, TestCase):
    """Free-base members with an active Main override see the Join card."""

    def _make_user(self, email, tier, slack_member=False):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = tier
        user.slack_member = slack_member
        user.save(update_fields=["tier", "slack_member"])
        self.client.login(email=email, password="pw")
        return user

    def _make_override(self, user, override_tier, **kwargs):
        defaults = {
            "original_tier": user.tier,
            "override_tier": override_tier,
            "expires_at": timezone.now() + timedelta(days=14),
            "is_active": True,
        }
        defaults.update(kwargs)
        return TierOverride.objects.create(user=user, **defaults)

    def _get_dashboard(self):
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            return self.client.get("/")

    # --- active Main override grants the card ---

    def test_free_base_active_main_override_not_joined_shows_join(self):
        user = self._make_user("comped@test.com", self.free_tier)
        self._make_override(user, self.main_tier)
        ctx = self._get_dashboard().context
        self.assertTrue(ctx["show_slack_join"])
        self.assertFalse(ctx["slack_connected"])

    def test_free_base_active_main_override_joined_shows_connected(self):
        user = self._make_user(
            "comped-joined@test.com", self.free_tier, slack_member=True,
        )
        self._make_override(user, self.main_tier)
        ctx = self._get_dashboard().context
        self.assertFalse(ctx["show_slack_join"])
        self.assertTrue(ctx["slack_connected"])

    # --- inactive / expired / below-Main overrides do NOT grant ---

    def test_free_base_expired_main_override_no_card(self):
        user = self._make_user("expired@test.com", self.free_tier)
        self._make_override(
            user,
            self.main_tier,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        ctx = self._get_dashboard().context
        self.assertFalse(ctx["show_slack_join"])
        self.assertFalse(ctx["slack_connected"])

    def test_free_base_deactivated_main_override_no_card(self):
        user = self._make_user("deactivated@test.com", self.free_tier)
        self._make_override(user, self.main_tier, is_active=False)
        ctx = self._get_dashboard().context
        self.assertFalse(ctx["show_slack_join"])
        self.assertFalse(ctx["slack_connected"])

    def test_free_base_active_basic_override_no_card(self):
        # Effective level (Basic) is below Main — no community access.
        user = self._make_user("basicoverride@test.com", self.free_tier)
        self._make_override(user, self.basic_tier)
        ctx = self._get_dashboard().context
        self.assertFalse(ctx["show_slack_join"])
        self.assertFalse(ctx["slack_connected"])

    # --- regressions: behaviour unchanged for non-override users ---

    def test_paid_main_base_no_override_still_shows_join(self):
        self._make_user("main@test.com", self.main_tier)
        ctx = self._get_dashboard().context
        self.assertTrue(ctx["show_slack_join"])

    def test_free_base_no_override_no_card(self):
        self._make_user("free@test.com", self.free_tier)
        ctx = self._get_dashboard().context
        self.assertFalse(ctx["show_slack_join"])
        self.assertFalse(ctx["slack_connected"])
