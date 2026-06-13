"""Tests for the gated /community/slack redirect endpoint (issue #953).

An eligible signed-in Main/Premium member (or active TierOverride /
staff) is 302-redirected to ``SLACK_INVITE_URL`` and the click is
recorded on the CRM timeline. Free / Basic / expired / anonymous
clickers never see the real invite URL — they get a 200 deny page
(anonymous is bounced to login first).
"""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride, User
from analytics.models import UserActivity
from tests.fixtures import TierSetupMixin

INVITE_URL = "https://join.slack.com/t/test/shared_invite/abc123"


@override_settings(SLACK_INVITE_URL=INVITE_URL)
class SlackJoinRedirectEligibleTest(TierSetupMixin, TestCase):
    """Eligible members are redirected and the click is recorded."""

    def _login(self, email, tier):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = tier
        user.save(update_fields=["tier"])
        self.client.login(email=email, password="pw")
        return user

    def test_main_member_redirected_and_click_recorded(self):
        user = self._login("main@test.com", self.main_tier)
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], INVITE_URL)
        rows = UserActivity.objects.filter(
            user=user, event_type=UserActivity.EVENT_SLACK_JOIN,
        )
        self.assertEqual(rows.count(), 1)

    def test_premium_member_redirected(self):
        self._login("premium@test.com", self.premium_tier)
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], INVITE_URL)

    def test_active_override_grants_access(self):
        # Free base tier + active Main override resolves to level 20.
        user = self._login("override@test.com", self.free_tier)
        TierOverride.objects.create(
            user=user,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=7),
            is_active=True,
        )
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], INVITE_URL)

    def test_staff_user_is_eligible(self):
        user = User.objects.create_user(email="staff@test.com", password="pw")
        user.is_staff = True
        user.tier = self.free_tier
        user.save(update_fields=["is_staff", "tier"])
        self.client.login(email="staff@test.com", password="pw")
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 302)


@override_settings(SLACK_INVITE_URL=INVITE_URL)
class SlackJoinRedirectDeniedTest(TierSetupMixin, TestCase):
    """Free / Basic / expired members are denied and the URL never leaks."""

    def _login(self, email, tier):
        user = User.objects.create_user(email=email, password="pw")
        user.tier = tier
        user.save(update_fields=["tier"])
        self.client.login(email=email, password="pw")
        return user

    def test_basic_member_denied_no_url_leak(self):
        user = self._login("basic@test.com", self.basic_tier)
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="slack-join-denied-upgrade"')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, INVITE_URL)
        self.assertFalse(
            UserActivity.objects.filter(
                user=user, event_type=UserActivity.EVENT_SLACK_JOIN,
            ).exists()
        )

    def test_free_member_denied_no_url_leak(self):
        self._login("free@test.com", self.free_tier)
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, INVITE_URL)

    def test_expired_override_member_denied(self):
        # Main override that already expired -> resolves to the free base.
        user = self._login("expired@test.com", self.free_tier)
        TierOverride.objects.create(
            user=user,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() - timedelta(days=1),
            is_active=True,
        )
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, INVITE_URL)


class SlackJoinRedirectAnonymousTest(TestCase):
    """Anonymous requests are bounced to login; the URL is never reachable."""

    @override_settings(SLACK_INVITE_URL=INVITE_URL)
    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])
        self.assertIn("next=/community/slack", response["Location"])
        self.assertNotIn(INVITE_URL, response["Location"])
        self.assertFalse(
            UserActivity.objects.filter(
                event_type=UserActivity.EVENT_SLACK_JOIN,
            ).exists()
        )


class SlackJoinRedirectUnsetUrlTest(TierSetupMixin, TestCase):
    """Eligible member but SLACK_INVITE_URL unset -> deny page, no 302."""

    @override_settings(SLACK_INVITE_URL="")
    def test_eligible_member_blank_url_shows_unavailable(self):
        user = User.objects.create_user(email="main2@test.com", password="pw")
        user.tier = self.main_tier
        user.save(update_fields=["tier"])
        self.client.login(email="main2@test.com", password="pw")
        response = self.client.get("/community/slack")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not available right now")
        self.assertFalse(
            UserActivity.objects.filter(
                user=user, event_type=UserActivity.EVENT_SLACK_JOIN,
            ).exists()
        )
