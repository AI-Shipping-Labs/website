"""Tests for the Slack join / connected card on /account/ (issue #700).

Mirrors the dashboard's two-state card so paid users who live on
/account/ rather than /dashboard can find the Slack invite and confirm
their linked Slack identity.

Gating matches the dashboard exactly: effective (override-aware) level
``get_user_level(user) >= LEVEL_MAIN`` (issue #971 — an active
``TierOverride`` grants Slack access) — and ``SLACK_INVITE_URL`` must be
set for the join CTA to appear.
"""

import os
import re
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride, User
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from tests.fixtures import TierSetupMixin

SLACK_TEAM_ID_KEY = "SLACK_TEAM_ID"
TEST_INVITE_URL = "https://join.slack.com/t/test/shared_invite/abc"


class _SlackTeamIdSettingMixin:
    """Reset ``SLACK_TEAM_ID`` between tests.

    Same pattern as ``studio/tests/test_user_slack_id.py`` — the
    integration config caches the resolved value, and env vars / DB rows
    can leak across tests if we do not deliberately tear down here.
    """

    def _reset_team_id(self):
        IntegrationSetting.objects.filter(key=SLACK_TEAM_ID_KEY).delete()
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self._saved_env = os.environ.pop(SLACK_TEAM_ID_KEY, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved_env is not None:
            os.environ[SLACK_TEAM_ID_KEY] = self._saved_env
        else:
            os.environ.pop(SLACK_TEAM_ID_KEY, None)

    def _set_team_id(self, value):
        IntegrationSetting.objects.update_or_create(
            key=SLACK_TEAM_ID_KEY,
            defaults={
                "value": value,
                "is_secret": False,
                "group": "slack",
                "description": "",
            },
        )
        clear_config_cache()


class AccountSlackCardGatingTest(
    _SlackTeamIdSettingMixin, TierSetupMixin, TestCase,
):
    """Free / Basic users see no card; Main+ without invite URL see no card."""

    def setUp(self):
        self._reset_team_id()

    def _login_with_tier(self, email, tier):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = tier
        user.save(update_fields=["tier"])
        self.client.login(email=email, password="pw")
        return user

    def test_free_user_renders_no_slack_card(self):
        self._login_with_tier("free@test.com", self.free_tier)
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="slack-account-card"')
        # And no leftover copy either.
        self.assertNotContains(response, "Join our Slack community")
        self.assertNotContains(response, "Connected to Slack")

    def test_basic_user_renders_no_slack_card(self):
        self._login_with_tier("basic@test.com", self.basic_tier)
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertNotContains(response, 'data-testid="slack-account-card"')

    def test_main_user_without_invite_url_renders_no_slack_card(self):
        # Matches the dashboard contract: if no invite URL is set and the
        # user is not yet a member, hide the card entirely instead of
        # rendering a dead button.
        user = self._login_with_tier("noinvite@test.com", self.main_tier)
        user.slack_member = False
        user.save(update_fields=["slack_member"])
        with self.settings(SLACK_INVITE_URL=""):
            response = self.client.get("/account/")
        self.assertNotContains(response, 'data-testid="slack-account-card"')


class AccountSlackJoinCtaTest(
    _SlackTeamIdSettingMixin, TierSetupMixin, TestCase,
):
    """Main+ users with ``slack_member=False`` see the Join Slack CTA."""

    def setUp(self):
        self._reset_team_id()

    def _login_main(self, email="main@test.com"):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = self.main_tier
        user.save(update_fields=["tier"])
        self.client.login(email=email, password="pw")
        return user

    def test_main_user_sees_join_card_with_invite_url(self):
        self._login_main()
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="slack-account-card"')
        self.assertContains(response, 'data-testid="slack-account-card-join"')
        self.assertContains(response, "Join our Slack community")
        # Issue #953: the CTA must point at the gated /community/slack
        # redirect, NEVER the raw invite URL — locked to the join anchor
        # so an unrelated link would still fail correctly if the CTA
        # disappeared.
        anchor_match = re.search(
            r'<a[^>]*data-testid="slack-account-card-join"[^>]*>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match, "Join Slack anchor must render")
        attrs = anchor_match.group(0)
        self.assertIn('href="/community/slack"', attrs)
        self.assertIn('rel="noopener"', attrs)
        # The raw invite URL must not leak anywhere on the rendered page.
        self.assertNotContains(response, TEST_INVITE_URL)

    def test_premium_user_also_sees_join_card(self):
        user = User.objects.create_user(email="prem@test.com", password="pw")
        user.tier = self.premium_tier
        user.save(update_fields=["tier"])
        self.client.login(email="prem@test.com", password="pw")
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertContains(response, 'data-testid="slack-account-card-join"')

    def test_join_cta_disappears_after_membership_probe_flips_flag(self):
        # Mirrors the Playwright scenario "Member who joins Slack sees
        # the state update on /account/ next page load". Simulates the
        # 30-minute probe by toggling ``slack_member`` directly.
        #
        # Issue #730 dropped the connected-state card on /account/, so
        # once the probe flips the user to ``slack_member=True`` the
        # Slack slot must render nothing at all (no join CTA AND no
        # connected confirmation panel).
        user = self._login_main("flip@test.com")
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            join_response = self.client.get("/account/")
        self.assertContains(
            join_response, 'data-testid="slack-account-card-join"',
        )

        user.slack_member = True
        user.slack_user_id = "U0FLIP123"
        user.save(update_fields=["slack_member", "slack_user_id"])

        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            connected_response = self.client.get("/account/")
        self.assertNotContains(
            connected_response, 'data-testid="slack-account-card-join"',
        )
        # After #730 the connected-state panel is gated out on /account/.
        self.assertNotContains(
            connected_response, 'data-testid="slack-account-card"',
        )
        self.assertNotContains(connected_response, "Connected to Slack")
        self.assertNotContains(connected_response, "U0FLIP123")

    def test_join_cta_renders_after_email_preferences_before_api_keys(self):
        # Issue #1206: activated members scan Membership first, then Email
        # Preferences, then the Slack card when eligible, then API keys.
        self._login_main("position@test.com")
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        membership_idx = content.index('data-lucide="crown"')
        email_prefs_idx = content.index('id="email-preferences-section"')
        join_idx = content.index('data-testid="slack-account-card-join"')
        api_idx = content.index('id="api-keys"')

        self.assertLess(
            membership_idx, email_prefs_idx,
            "Membership card must render above Email Preferences",
        )
        self.assertLess(
            email_prefs_idx, join_idx,
            "Email Preferences must render above the Join Slack CTA",
        )
        self.assertLess(
            join_idx, api_idx,
            "Join Slack CTA must render above API keys",
        )


class AccountSlackConnectedStateTest(
    _SlackTeamIdSettingMixin, TierSetupMixin, TestCase,
):
    """Issue #730: connected Main+ users see NO Slack card on /account/.

    The connected-state panel ("Connected to Slack" + Slack ID) duplicates
    information the user already has inside Slack itself. After #730 the
    /account/ template gates the partial behind ``{% if not
    slack_connected %}``, so a Main+ user with ``slack_member=True`` must
    see no Slack-card markup at all. The view context (``slack_user_id``,
    ``slack_profile_url``) is still populated so Studio and other
    surfaces keep working — only the /account/ render is suppressed.
    """

    def setUp(self):
        self._reset_team_id()

    def _login_connected(
        self, email="joined@test.com", slack_user_id="U07AB12CDEF",
    ):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = self.main_tier
        user.slack_member = True
        user.slack_user_id = slack_user_id
        user.save(update_fields=["tier", "slack_member", "slack_user_id"])
        self.client.login(email=email, password="pw")
        return user

    def test_connected_user_sees_no_slack_card_without_team_id(self):
        self._login_connected()
        # SLACK_TEAM_ID is unset (see _reset_team_id in setUp).
        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        # The entire Slack card section is gated out on /account/ for
        # connected users — none of the partial's markup should leak.
        self.assertNotContains(response, 'data-testid="slack-account-card"')
        self.assertNotContains(response, "Connected to Slack")
        self.assertNotContains(
            response,
            "You are a member of the AI Shipping Labs community workspace.",
        )
        self.assertNotContains(
            response, 'data-testid="slack-account-card-id-row"',
        )
        self.assertNotContains(
            response, 'data-testid="slack-account-card-id-value"',
        )
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )
        # And the bare Slack ID must not leak as plain text on /account/.
        self.assertNotContains(response, "U07AB12CDEF")

    def test_connected_user_sees_no_slack_card_with_team_id_set(self):
        # Even when SLACK_TEAM_ID is set (which would normally render an
        # "Open in Slack" anchor inside the connected-state panel), the
        # whole card is still suppressed on /account/ after #730.
        self._set_team_id("T01TEAM123")
        self._login_connected(slack_user_id="U07AB12CDEF")

        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="slack-account-card"')
        self.assertNotContains(
            response, 'data-testid="slack-account-card-profile-link"',
        )
        # The deep-link URL the partial would have rendered must not be
        # on the page at all.
        self.assertNotContains(
            response, "app.slack.com/client/T01TEAM123/U07AB12CDEF",
        )
        self.assertNotContains(response, "Connected to Slack")


class AccountSlackCardAnonymousTest(TestCase):
    """Anonymous /account/ hits redirect to login (no regression)."""

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get("/account/")
        # Existing behaviour: @login_required bounces to allauth login.
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])


class SlackProfileUrlContextKeyTest(
    _SlackTeamIdSettingMixin, TierSetupMixin, TestCase,
):
    """The view exposes the five context keys required by the partial."""

    def setUp(self):
        self._reset_team_id()

    def test_context_keys_populated_for_main_member(self):
        self._set_team_id("T01TEAM123")
        user = User.objects.create_user(email="ctx@test.com", password="pw")
        user.tier = self.main_tier
        user.slack_member = True
        user.slack_user_id = "U0CONTEXT1"
        user.save(update_fields=["tier", "slack_member", "slack_user_id"])
        self.client.login(email="ctx@test.com", password="pw")

        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")

        ctx = response.context
        self.assertFalse(ctx["show_slack_join"])
        self.assertTrue(ctx["slack_connected"])
        # Issue #953: context exposes the gated redirect path, not the raw
        # invite URL.
        self.assertEqual(ctx["slack_join_url"], "/community/slack")
        self.assertNotIn("slack_invite_url", ctx)
        self.assertEqual(ctx["slack_user_id"], "U0CONTEXT1")
        self.assertEqual(
            ctx["slack_profile_url"],
            "https://app.slack.com/client/T01TEAM123/U0CONTEXT1",
        )

    def test_context_keys_for_free_user_disable_card(self):
        user = User.objects.create_user(email="freectx@test.com", password="pw")
        user.tier = self.free_tier
        user.save(update_fields=["tier"])
        self.client.login(email="freectx@test.com", password="pw")

        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            response = self.client.get("/account/")

        ctx = response.context
        self.assertFalse(ctx["show_slack_join"])
        self.assertFalse(ctx["slack_connected"])
        self.assertEqual(ctx["slack_user_id"], "")
        self.assertEqual(ctx["slack_profile_url"], "")


class AccountSlackCardOverrideTest(
    _SlackTeamIdSettingMixin, TierSetupMixin, TestCase,
):
    """Issue #971: the account Join Slack card uses effective tier.

    An active, non-expired ``TierOverride`` grants Slack/community access
    via ``content.access.get_user_level`` — the same rule as the dashboard
    and the join redirect. Expired / deactivated / below-Main overrides do
    not, and non-override users are unchanged.
    """

    def setUp(self):
        self._reset_team_id()

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

    def _get_account(self):
        with self.settings(SLACK_INVITE_URL=TEST_INVITE_URL):
            return self.client.get("/account/")

    def test_free_base_active_main_override_not_joined_shows_card(self):
        user = self._make_user("comped@test.com", self.free_tier)
        self._make_override(user, self.main_tier)
        response = self._get_account()
        self.assertTrue(response.context["show_slack_join"])
        self.assertContains(
            response, 'data-testid="slack-account-card-join"',
        )

    def test_free_base_active_main_override_joined_is_connected(self):
        user = self._make_user(
            "comped-joined@test.com", self.free_tier, slack_member=True,
        )
        self._make_override(user, self.main_tier)
        ctx = self._get_account().context
        self.assertFalse(ctx["show_slack_join"])
        self.assertTrue(ctx["slack_connected"])

    def test_free_base_expired_main_override_no_card(self):
        user = self._make_user("expired@test.com", self.free_tier)
        self._make_override(
            user,
            self.main_tier,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = self._get_account()
        self.assertFalse(response.context["show_slack_join"])
        self.assertFalse(response.context["slack_connected"])
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )

    def test_free_base_deactivated_main_override_no_card(self):
        user = self._make_user("deactivated@test.com", self.free_tier)
        self._make_override(user, self.main_tier, is_active=False)
        response = self._get_account()
        self.assertFalse(response.context["show_slack_join"])
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )

    def test_free_base_active_basic_override_no_card(self):
        user = self._make_user("basicoverride@test.com", self.free_tier)
        self._make_override(user, self.basic_tier)
        response = self._get_account()
        self.assertFalse(response.context["show_slack_join"])
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )

    def test_paid_main_base_no_override_still_shows_card(self):
        self._make_user("main@test.com", self.main_tier)
        response = self._get_account()
        self.assertTrue(response.context["show_slack_join"])

    def test_free_base_no_override_no_card(self):
        self._make_user("free@test.com", self.free_tier)
        response = self._get_account()
        self.assertFalse(response.context["show_slack_join"])
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )
