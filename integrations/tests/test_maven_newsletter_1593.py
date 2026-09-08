"""The Maven newsletter is genuine opt-in (issue #1593).

Two claims are load-bearing here and they pull in opposite directions, which
is why both need tests.

The email says "if you want to hear from us, verify your email". Verification
and subscription are separate fields, so if the link only verified, the email
would promise something the system never delivers. Hence
``/api/verify-and-subscribe`` does both in one click.

The converse must NOT hold. Setting a password or signing in with OAuth are
authentication events, not consent, and neither may subscribe anyone.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.utils.tokens import (
    generate_password_reset_token,
    generate_user_action_token,
)
from email_app.services.campaign_audience import eligible_campaign_recipients
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import _welcome_context
from payments.models import Tier

User = get_user_model()

SECRET = "maven-secret-1593"
SLACK_ADDED = (MavenEnrollmentEvent.STEP_SUCCEEDED, "")


def enable_maven():
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_ENROLLMENT_ENABLED", defaults={"value": "true"},
    )
    IntegrationSetting.objects.update_or_create(
        key="MAVEN_WEBHOOK_SHARED_SECRET", defaults={"value": SECRET},
    )
    from integrations.config import clear_config_cache

    clear_config_cache()


class MavenWebhookMixin(TestCase):
    def setUp(self):
        enable_maven()
        Tier.objects.get(slug="main")

    def enroll(self, email, **extra):
        payload = {"event": "user_cohort.enrolled", "email": email}
        payload.update(extra)
        return self.client.post(
            "/api/webhooks/maven",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_MAVEN_SECRET=SECRET,
        )


@patch(
    "integrations.services.maven._invite_to_slack",
    lambda user, actions: (actions.append("slack"), SLACK_ADDED)[1],
)
@patch("integrations.services.maven.EmailService")
class VerifyAndSubscribeOptInTest(MavenWebhookMixin):
    def test_one_click_both_verifies_and_subscribes(self, email_service):
        """The email's promise and the endpoint's effect have to match."""
        self.enroll("opt-in-1593@example.com")
        user = User.objects.get(email="opt-in-1593@example.com")
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_verified)

        response = self.client.get(
            _welcome_context(user, "Course")["newsletter_opt_in_url"]
        )

        self.assertContains(response, "You&#x27;re subscribed")
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_preferences["newsletter"])
        # The scoped course-email preference is a separate decision.
        self.assertTrue(user.email_preferences["maven_emails"])

    def test_landing_page_says_what_happened_and_offers_the_way_out(
        self, email_service,
    ):
        self.enroll("opt-in-page-1593@example.com")
        user = User.objects.get(email="opt-in-page-1593@example.com")

        response = self.client.get(
            _welcome_context(user, "Course")["newsletter_opt_in_url"]
        )

        self.assertContains(response, "You&#x27;re subscribed")
        self.assertContains(response, "verified")
        self.assertContains(response, "subscribed to the AI Shipping Labs")
        # Consent that is hard to withdraw is not consent: the unsubscribe is
        # on the same page, and needs no sign-in (they may have no password).
        self.assertContains(response, "/api/unsubscribe?token=")
        self.assertContains(response, "no sign-in needed")

    def test_opting_in_twice_is_idempotent_and_does_not_look_broken(
        self, email_service,
    ):
        self.enroll("twice-1593@example.com")
        user = User.objects.get(email="twice-1593@example.com")
        url = _welcome_context(user, "Course")["newsletter_opt_in_url"]

        self.client.get(url)
        response = self.client.get(url)

        # Still the confirmation, never an error page or a stack trace.
        self.assertContains(response, "You&#x27;re subscribed")
        self.assertNotContains(response, "Subscribe failed")
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)

    def test_opting_in_then_out_leaves_them_out(self, email_service):
        self.enroll("in-then-out-1593@example.com")
        user = User.objects.get(email="in-then-out-1593@example.com")
        context = _welcome_context(user, "Course")

        self.client.get(context["newsletter_opt_in_url"])
        self.client.get(
            "/api/unsubscribe?token="
            + generate_user_action_token(user.pk, "unsubscribe")
        )

        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_preferences["newsletter"])
        # Verification is not undone by leaving the newsletter.
        self.assertTrue(user.email_verified)

    def test_opting_in_grants_no_access_and_touches_nothing_else(
        self, email_service,
    ):
        self.enroll("scope-1593@example.com")
        user = User.objects.get(email="scope-1593@example.com")
        from content.access import get_user_level

        before_level = get_user_level(user)
        before_slack = user.slack_member
        before_tier = user.tier_id

        self.client.get(_welcome_context(user, "Course")["newsletter_opt_in_url"])

        user.refresh_from_db()
        self.assertEqual(get_user_level(user), before_level)
        self.assertEqual(user.slack_member, before_slack)
        self.assertEqual(user.tier_id, before_tier)


class VerifyAndSubscribeTokenScopeTest(TestCase):
    """Authentication is not consent, and no other token may subscribe."""

    def test_a_plain_verify_email_token_is_rejected(self):
        user = User.objects.create_user(
            email="wrong-action-1593@example.com", unsubscribed=True,
        )
        token = generate_user_action_token(user.pk, "verify_email")

        response = self.client.get(f"/api/verify-and-subscribe?token={token}")

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Subscribe failed", status_code=400)
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_verified)

    def test_a_tampered_token_fails_safely_and_changes_nothing(self):
        user = User.objects.create_user(
            email="tampered-1593@example.com", unsubscribed=True,
        )

        response = self.client.get("/api/verify-and-subscribe?token=not-a-token")

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Subscribe failed", status_code=400)
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_verified)

    def test_an_expired_opt_in_token_fails_safely_and_changes_nothing(self):
        user = User.objects.create_user(
            email="expired-1593@example.com",
            unsubscribed=True,
            email_preferences={"newsletter": False, "maven_emails": True},
        )
        token = generate_user_action_token(
            user.pk,
            "verify_and_subscribe",
            expiry_hours=-1,
        )

        response = self.client.get(f"/api/verify-and-subscribe?token={token}")

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "expired", status_code=400)
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_verified)
        self.assertFalse(user.email_preferences["newsletter"])

    def test_a_missing_token_fails_safely(self):
        response = self.client.get("/api/verify-and-subscribe")

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "incomplete", status_code=400)

    def test_an_unknown_user_fails_safely(self):
        token = generate_user_action_token(999999, "verify_and_subscribe")

        response = self.client.get(f"/api/verify-and-subscribe?token={token}")

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "User not found", status_code=400)

    def test_an_expired_link_says_so_and_offers_the_route_back(self):
        """A late click is the normal case, so it must not dead-end.

        Issue #1593: someone who clicked "verify your email" asked for
        something. Telling them only that the link is broken answers a
        deliberate act with nothing.
        """
        user = User.objects.create_user(
            email="expired-1593@example.com", unsubscribed=True,
        )
        token = generate_user_action_token(
            user.pk, "verify_and_subscribe", expiry_hours=-1,
        )

        response = self.client.get(f"/api/verify-and-subscribe?token={token}")

        self.assertContains(response, "has expired", status_code=400)
        self.assertContains(response, "account", status_code=400)
        self.assertContains(response, 'href="/account/"', status_code=400)
        # Expired means expired: nothing is subscribed or verified.
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_verified)

    def test_a_link_inside_its_window_still_works(self):
        """The 30-day window is real, not a token that quietly never expires."""
        user = User.objects.create_user(
            email="within-window-1593@example.com", unsubscribed=True,
        )
        token = generate_user_action_token(
            user.pk, "verify_and_subscribe", expiry_hours=24 * 29,
        )

        response = self.client.get(f"/api/verify-and-subscribe?token={token}")

        self.assertContains(response, "You&#x27;re subscribed")
        user.refresh_from_db()
        self.assertFalse(user.unsubscribed)
        self.assertTrue(user.email_verified)

    def test_every_failure_state_offers_the_same_route_back(self):
        for label, url in (
            ("missing", "/api/verify-and-subscribe"),
            ("tampered", "/api/verify-and-subscribe?token=not-a-token"),
        ):
            with self.subTest(label):
                response = self.client.get(url)
                self.assertContains(
                    response, 'href="/account/"', status_code=400,
                )
                self.assertContains(
                    response, "Go to email preferences", status_code=400,
                )

    def test_the_failure_page_title_matches_its_heading(self):
        """A tab reading "Unsubscribe" over a "Subscribe failed" card is wrong."""
        response = self.client.get("/api/verify-and-subscribe?token=not-a-token")

        self.assertContains(
            response, "<title>Subscribe failed", status_code=400, html=False,
        )

    def test_verifying_through_the_ordinary_endpoint_subscribes_nobody(self):
        """The /api/verify-email sibling must never touch the newsletter."""
        user = User.objects.create_user(
            email="verify-only-1593@example.com",
            unsubscribed=True,
            email_preferences={"newsletter": False, "maven_emails": True},
        )
        token = generate_user_action_token(user.pk, "verify_email")

        self.client.get(f"/api/verify-email?token={token}")

        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_preferences["newsletter"])

    def test_setting_a_password_subscribes_nobody(self):
        """Authentication is not consent, whatever it does to verification."""
        user = User.objects.create_user(
            email="password-1593@example.com",
            unsubscribed=True,
            email_preferences={"newsletter": False, "maven_emails": True},
        )
        token = generate_password_reset_token(user, expiry_hours=24)

        response = self.client.post(
            "/api/password-reset",
            data=json.dumps(
                {"token": token, "new_password": "Corr3ct-Horse-Batt3ry!"},
            ),
            content_type="application/json",
        )

        self.assertEqual(response.json()["status"], "ok")
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)
        self.assertFalse(user.email_preferences["newsletter"])


@patch(
    "integrations.services.maven._invite_to_slack",
    lambda user, actions: (actions.append("slack"), SLACK_ADDED)[1],
)
@patch("integrations.services.maven.EmailService")
class MavenCampaignAudienceTest(MavenWebhookMixin):
    def test_an_enrollee_who_never_opts_in_reaches_no_campaign_audience(
        self, email_service,
    ):
        self.enroll("no-optin-1593@example.com")
        user = User.objects.get(email="no-optin-1593@example.com")

        self.assertNotIn(user, eligible_campaign_recipients())
        self.assertNotIn(
            user, eligible_campaign_recipients(audience_verification="everyone"),
        )

    def test_opting_in_makes_them_reachable_including_verified_only(
        self, email_service,
    ):
        """The opt-in verifies too, so it clears both filters at once."""
        self.enroll("optin-audience-1593@example.com")
        user = User.objects.get(email="optin-audience-1593@example.com")

        self.client.get(_welcome_context(user, "Course")["newsletter_opt_in_url"])

        self.assertIn(user, eligible_campaign_recipients())
        self.assertIn(
            user, eligible_campaign_recipients(audience_verification="everyone"),
        )
