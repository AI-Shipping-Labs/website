"""Tests for the Account page (issue #70)."""

import json

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from payments.models import Tier


class AccountPageAccessTest(TestCase):
    """Tests for account page access control."""

    def test_logged_out_redirects_to_login(self):
        """GET /account/ while logged out returns redirect to login page."""
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_logged_in_returns_200(self):
        """GET /account/ for logged-in user returns 200."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertTemplateUsed(response, "accounts/account.html")

    def test_url_name_resolves(self):
        url = reverse("account")
        self.assertEqual(url, "/account/")


class AccountPageFreeUserTest(TestCase):
    """Tests for account page display for free-tier users."""

    def setUp(self):
        self.user = User.objects.create_user(email="free@example.com")
        self.client.force_login(self.user)

    def test_shows_tier_name(self):
        """Free users see 'Free' as their tier name."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Free", content)

    def test_shows_tier_level_badge(self):
        """Free users see their tier level badge."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Level 0", content)

    def test_shows_upgrade_button(self):
        """Free users see 'Upgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Upgrade", content)

    def test_upgrade_links_to_pricing(self):
        """Free users' Upgrade button links to /pricing."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('/pricing', content)

    def test_no_downgrade_button(self):
        """Free users do not see 'Downgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    def test_no_cancel_button(self):
        """Free users do not see 'Cancel Subscription' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="cancel-btn"', content)

    def test_no_billing_period_end(self):
        """Free users do not see billing period end date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn("Billing Period Ends", content)

    def test_context_is_free(self):
        """Context has is_free=True for free users."""
        response = self.client.get("/account/")
        self.assertTrue(response.context["is_free"])

    def test_context_has_no_subscription(self):
        """Context has has_subscription=False for free users."""
        response = self.client.get("/account/")
        self.assertFalse(response.context["has_subscription"])


class AccountPagePaidUserTest(TestCase):
    """Tests for account page display for paid-tier users."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.user = User.objects.create_user(email="paid@example.com")
        self.user.tier = self.main_tier
        self.user.subscription_id = "sub_test123"
        self.user.billing_period_end = timezone.now()
        self.user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(self.user)

    def test_shows_tier_name(self):
        """Paid users see their tier name."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Main", content)

    def test_shows_tier_level_badge(self):
        """Paid users see their tier level badge."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Level 20", content)

    def test_shows_billing_period_end(self):
        """Paid users see formatted billing period end date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Billing Period Ends", content)

    def test_billing_period_end_formatted(self):
        """Billing period end is formatted as 'Month Day, Year'."""
        from datetime import datetime

        self.user.billing_period_end = timezone.make_aware(
            datetime(2026, 3, 15, 12, 0, 0)
        )
        self.user.save(update_fields=["billing_period_end"])
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("March 15, 2026", content)

    def test_shows_upgrade_button_for_non_premium(self):
        """Main-tier users see 'Upgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="upgrade-btn"', content)

    def test_shows_downgrade_button(self):
        """Main-tier users see 'Downgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="downgrade-btn"', content)

    def test_shows_cancel_button(self):
        """Paid users see 'Cancel Subscription' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-btn"', content)

    def test_context_not_free(self):
        """Context has is_free=False for paid users."""
        response = self.client.get("/account/")
        self.assertFalse(response.context["is_free"])

    def test_context_has_subscription(self):
        """Context has has_subscription=True for paid users."""
        response = self.client.get("/account/")
        self.assertTrue(response.context["has_subscription"])


class AccountPagePremiumUserTest(TestCase):
    """Tests for account page display for premium-tier users."""

    def setUp(self):
        self.premium_tier = Tier.objects.get(slug="premium")
        self.user = User.objects.create_user(email="premium@example.com")
        self.user.tier = self.premium_tier
        self.user.subscription_id = "sub_premium123"
        self.user.billing_period_end = timezone.now()
        self.user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(self.user)

    def test_no_upgrade_button_for_premium(self):
        """Premium users do not see 'Upgrade' button (already at highest tier)."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="upgrade-btn"', content)

    def test_shows_downgrade_button(self):
        """Premium users see 'Downgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="downgrade-btn"', content)

    def test_shows_cancel_button(self):
        """Premium users see 'Cancel Subscription' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-btn"', content)


class AccountPageBasicUserTest(TestCase):
    """Tests for account page display for basic-tier users."""

    def setUp(self):
        self.basic_tier = Tier.objects.get(slug="basic")
        self.user = User.objects.create_user(email="basic@example.com")
        self.user.tier = self.basic_tier
        self.user.subscription_id = "sub_basic123"
        self.user.billing_period_end = timezone.now()
        self.user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(self.user)

    def test_shows_upgrade_button(self):
        """Basic users see 'Upgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="upgrade-btn"', content)

    def test_no_downgrade_button_for_basic(self):
        """Basic users do not see 'Downgrade' button (lowest paid tier)."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    def test_shows_cancel_button(self):
        """Basic users see 'Cancel Subscription' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-btn"', content)


class AccountPagePendingDowngradeTest(TestCase):
    """Tests for account page when a downgrade is pending."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.basic_tier = Tier.objects.get(slug="basic")
        self.user = User.objects.create_user(email="downgrade@example.com")
        self.user.tier = self.main_tier
        self.user.pending_tier = self.basic_tier
        self.user.subscription_id = "sub_test456"
        self.user.billing_period_end = timezone.make_aware(
            timezone.datetime(2026, 4, 1, 12, 0, 0)
        )
        self.user.save(
            update_fields=[
                "tier",
                "pending_tier",
                "subscription_id",
                "billing_period_end",
            ]
        )
        self.client.force_login(self.user)

    def test_shows_pending_downgrade_notice(self):
        """Page shows pending downgrade notice with tier name and date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="pending-downgrade-notice"', content)
        self.assertIn("Basic", content)
        self.assertIn("April 1, 2026", content)

    def test_pending_downgrade_message_format(self):
        """Notice says 'Your plan will change to {tier} on {date}'."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Your plan will change to", content)

    def test_context_is_pending_downgrade(self):
        response = self.client.get("/account/")
        self.assertTrue(response.context["is_pending_downgrade"])
        self.assertFalse(response.context["is_pending_cancellation"])


class AccountPagePendingCancellationTest(TestCase):
    """Tests for account page when a cancellation is pending."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.free_tier = Tier.objects.get(slug="free")
        self.user = User.objects.create_user(email="cancel@example.com")
        self.user.tier = self.main_tier
        self.user.pending_tier = self.free_tier
        self.user.subscription_id = "sub_cancel789"
        self.user.billing_period_end = timezone.make_aware(
            timezone.datetime(2026, 5, 15, 12, 0, 0)
        )
        self.user.save(
            update_fields=[
                "tier",
                "pending_tier",
                "subscription_id",
                "billing_period_end",
            ]
        )
        self.client.force_login(self.user)

    def test_shows_pending_cancellation_notice(self):
        """Page shows pending cancellation notice."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="pending-cancellation-notice"', content)

    def test_cancellation_message_format(self):
        """Notice says 'Your {tier} access ends on {date}'."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Main", content)
        self.assertIn("access ends on", content)
        self.assertIn("May 15, 2026", content)

    def test_no_cancel_button_when_already_cancelled(self):
        """No cancel button when cancellation is already pending."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="cancel-btn"', content)

    def test_no_downgrade_button_when_cancelled(self):
        """No downgrade button when cancellation is pending."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    def test_no_upgrade_button_when_cancelled(self):
        """No upgrade button when cancellation is pending."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="upgrade-btn"', content)

    def test_context_is_pending_cancellation(self):
        response = self.client.get("/account/")
        self.assertTrue(response.context["is_pending_cancellation"])
        self.assertFalse(response.context["is_pending_downgrade"])


class EmailPreferencesAPITest(TestCase):
    """Tests for the email preferences API endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(email="prefs@example.com")
        self.client.force_login(self.user)
        self.url = "/account/api/email-preferences"

    def test_logged_out_redirects(self):
        """Logged-out user gets redirect."""
        self.client.logout()
        response = self.client.post(
            self.url,
            data=json.dumps({"newsletter": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        """GET is not allowed on this endpoint."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_subscribe_to_newsletter(self):
        """POST with newsletter=True subscribes the user."""
        self.user.unsubscribed = True
        self.user.save(update_fields=["unsubscribed"])

        response = self.client.post(
            self.url,
            data=json.dumps({"newsletter": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["newsletter"])

        self.user.refresh_from_db()
        self.assertFalse(self.user.unsubscribed)
        self.assertTrue(self.user.email_preferences.get("newsletter"))

    def test_unsubscribe_from_newsletter(self):
        """POST with newsletter=False unsubscribes the user."""
        response = self.client.post(
            self.url,
            data=json.dumps({"newsletter": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["newsletter"])

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertFalse(self.user.email_preferences.get("newsletter"))

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_newsletter_field_returns_400(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"other": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_url_name_resolves(self):
        url = reverse("email_preferences")
        self.assertEqual(url, "/account/api/email-preferences")


class CancelSubscriptionAPITest(TestCase):
    """Tests for the account cancel subscription API endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(email="cancel-api@example.com")
        self.url = "/account/api/cancel"
        self.client.force_login(self.user)

    def test_logged_out_redirects(self):
        """Logged-out user gets redirect."""
        self.client.logout()
        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        """GET is not allowed on this endpoint."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_no_subscription_returns_400(self):
        """User without subscription_id gets a 400 error."""
        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("error", data)

    def test_url_name_resolves(self):
        url = reverse("account_cancel_subscription")
        self.assertEqual(url, "/account/api/cancel")


class AccountPageEmailPreferencesDisplayTest(TestCase):
    """Tests for email preferences display on account page."""

    def test_shows_email_preferences_section(self):
        user = User.objects.create_user(email="email@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Email Preferences", content)
        self.assertIn('id="email-preferences-section"', content)

    def test_shows_newsletter_toggle(self):
        user = User.objects.create_user(email="toggle@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="newsletter-toggle"', content)

    def test_subscribed_status_shown(self):
        user = User.objects.create_user(email="sub@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("You are subscribed to newsletters.", content)

    def test_unsubscribed_status_shown(self):
        user = User.objects.create_user(email="unsub@example.com")
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("You are unsubscribed from newsletters.", content)

    def test_newsletter_context_subscribed(self):
        user = User.objects.create_user(email="ctx@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertTrue(response.context["newsletter_subscribed"])

    def test_newsletter_context_unsubscribed(self):
        user = User.objects.create_user(email="ctx2@example.com")
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertFalse(response.context["newsletter_subscribed"])


class AccountPageContextDataTest(TestCase):
    """Tests for the context data provided to the template."""

    def test_context_has_tier(self):
        user = User.objects.create_user(email="ctx-tier@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertIn("tier", response.context)

    def test_context_has_upgrade_tiers_for_free_user(self):
        user = User.objects.create_user(email="ctx-up@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        upgrade_tiers = response.context["upgrade_tiers"]
        # Free user should see basic, main, premium as upgrade options
        slugs = [t.slug for t in upgrade_tiers]
        self.assertIn("basic", slugs)
        self.assertIn("main", slugs)
        self.assertIn("premium", slugs)

    def test_context_has_upgrade_tiers_for_main_user(self):
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="ctx-up2@example.com")
        user.tier = main_tier
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        upgrade_tiers = response.context["upgrade_tiers"]
        slugs = [t.slug for t in upgrade_tiers]
        self.assertIn("premium", slugs)
        self.assertNotIn("basic", slugs)
        self.assertNotIn("main", slugs)

    def test_context_has_downgrade_tiers_for_main_user(self):
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="ctx-down@example.com")
        user.tier = main_tier
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        downgrade_tiers = response.context["downgrade_tiers"]
        slugs = [t.slug for t in downgrade_tiers]
        self.assertIn("basic", slugs)
        self.assertNotIn("main", slugs)
        self.assertNotIn("premium", slugs)

    def test_context_empty_downgrade_tiers_for_basic(self):
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="ctx-basic@example.com")
        user.tier = basic_tier
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(len(response.context["downgrade_tiers"]), 0)

    def test_context_empty_upgrade_tiers_for_premium(self):
        premium_tier = Tier.objects.get(slug="premium")
        user = User.objects.create_user(email="ctx-premium@example.com")
        user.tier = premium_tier
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(len(response.context["upgrade_tiers"]), 0)

    def test_context_has_downgrade_tiers_for_premium_user(self):
        premium_tier = Tier.objects.get(slug="premium")
        user = User.objects.create_user(email="ctx-pdown@example.com")
        user.tier = premium_tier
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        downgrade_tiers = response.context["downgrade_tiers"]
        slugs = [t.slug for t in downgrade_tiers]
        self.assertIn("basic", slugs)
        self.assertIn("main", slugs)
        self.assertNotIn("premium", slugs)


class AccountPageHeaderFooterTest(TestCase):
    """Tests that account page includes header and footer."""

    def setUp(self):
        self.user = User.objects.create_user(email="hf@example.com")
        self.client.force_login(self.user)

    def test_includes_header(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("AI Shipping Labs", content)

    def test_includes_footer(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("</footer>", content)

    def test_extends_base_template(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("tailwindcss", content)

    def test_page_title(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Account", content)
