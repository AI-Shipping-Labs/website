"""Tests for the Slack join / connected card on /account/ (issue #700).

Mirrors the dashboard's two-state card so paid users who live on
/account/ rather than /dashboard can find the Slack invite and confirm
their linked Slack identity.

Gating matches the dashboard exactly: raw ``user.tier.level >=
LEVEL_MAIN`` — admin tier overrides do NOT grant Slack access — and
``SLACK_INVITE_URL`` must be set for the join CTA to appear.
"""

import os
import re

from django.test import TestCase

from accounts.models import User
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
        # The CTA must point at the configured invite URL — locked to the
        # join anchor so an unrelated link with the same href would still
        # fail correctly if the CTA disappeared.
        anchor_match = re.search(
            r'<a[^>]*data-testid="slack-account-card-join"[^>]*>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match, "Join Slack anchor must render")
        attrs = anchor_match.group(0)
        self.assertIn(f'href="{TEST_INVITE_URL}"', attrs)
        self.assertIn('target="_blank"', attrs)
        self.assertIn('rel="noopener"', attrs)

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
        self.assertContains(connected_response, "Connected to Slack")
        self.assertContains(connected_response, "U0FLIP123")


class AccountSlackConnectedStateTest(
    _SlackTeamIdSettingMixin, TierSetupMixin, TestCase,
):
    """Main+ users with ``slack_member=True`` see the Connected state."""

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

    def test_connected_state_shows_slack_id_as_plain_text_without_team_id(self):
        self._login_connected()
        # SLACK_TEAM_ID is unset (see _reset_team_id in setUp).
        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="slack-account-card"')
        self.assertContains(response, "Connected to Slack")
        self.assertContains(response, 'data-testid="slack-account-card-id-row"')
        # Plain monospace span — no profile link anchor.
        self.assertContains(
            response, 'data-testid="slack-account-card-id-value"',
        )
        self.assertContains(response, "U07AB12CDEF")
        self.assertNotContains(
            response, 'data-testid="slack-account-card-profile-link"',
        )
        self.assertNotContains(response, "app.slack.com/client/")
        # The join CTA must NOT render alongside the connected card.
        self.assertNotContains(
            response, 'data-testid="slack-account-card-join"',
        )

    def test_connected_state_shows_profile_link_when_team_id_set(self):
        self._set_team_id("T01TEAM123")
        self._login_connected(slack_user_id="U07AB12CDEF")

        response = self.client.get("/account/")
        self.assertContains(
            response, 'data-testid="slack-account-card-profile-link"',
        )
        anchor_match = re.search(
            r'<a[^>]*data-testid="slack-account-card-profile-link"[^>]*>'
            r'([^<]*)</a>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(
            anchor_match, "Slack profile-link anchor must render",
        )
        attrs = anchor_match.group(0)
        self.assertIn(
            'href="https://app.slack.com/client/T01TEAM123/U07AB12CDEF"',
            attrs,
        )
        self.assertIn('target="_blank"', attrs)
        self.assertIn('rel="noopener"', attrs)
        # The visible link text is the Slack ID itself.
        self.assertEqual(anchor_match.group(1).strip(), "U07AB12CDEF")

    def test_connected_card_appears_after_membership_card(self):
        # Pin the ordering so a future refactor of the card stack cannot
        # silently drop the partial above the Email Preferences block.
        self._login_connected()
        response = self.client.get("/account/")
        content = response.content.decode()
        membership_idx = content.find("Membership")
        slack_idx = content.find('data-testid="slack-account-card"')
        email_prefs_idx = content.find('id="email-preferences-section"')
        self.assertNotEqual(membership_idx, -1)
        self.assertNotEqual(slack_idx, -1)
        self.assertNotEqual(email_prefs_idx, -1)
        self.assertLess(membership_idx, slack_idx)
        self.assertLess(slack_idx, email_prefs_idx)


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
        self.assertEqual(ctx["slack_invite_url"], TEST_INVITE_URL)
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
