"""Tests for the Account page (issue #70)."""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from accounts.services import timezones
from accounts.services.timezones import build_timezone_options
from payments.models import Tier


@tag('core')
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

class AccountPageFreeUserTest(TestCase):
    """Tests for account page display for free-tier users."""

    def setUp(self):
        self.user = User.objects.create_user(email="free@example.com")
        self.client.force_login(self.user)

    def test_shows_tier_name(self):
        """Free users see 'Free' as their tier name in the tier-name element."""
        response = self.client.get("/account/")
        # Verify via context that the user is on the free tier
        self.assertTrue(response.context["is_free"])
        # Verify the tier-name span is rendered
        self.assertContains(response, 'id="tier-name"')

    def test_shows_tier_level_badge(self):
        """Free users see their tier level badge."""
        response = self.client.get("/account/")
        self.assertContains(response, 'id="tier-badge"')
        self.assertContains(response, "Level 0")

    def test_shows_upgrade_button(self):
        """Free users see 'Upgrade' button linking to /pricing."""
        response = self.client.get("/account/")
        self.assertContains(response, 'id="upgrade-btn"')

    def test_upgrade_links_to_pricing(self):
        """Free users' Upgrade button links to /pricing."""
        response = self.client.get("/account/")
        self.assertContains(response, 'href="/pricing"')

    def test_no_downgrade_button(self):
        """Free users do not see 'Downgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    # test_no_cancel_button removed -- duplicate of
    # FreeUserNoCancelButtonTest in test_cancel_confirmation.py

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
        """Paid users see their tier name via context."""
        response = self.client.get("/account/")
        self.assertEqual(response.context["tier"].slug, "main")
        self.assertEqual(response.context["tier"].name, "Main")
        self.assertContains(response, 'id="tier-name"')

    def test_shows_tier_level_badge(self):
        """Paid users see their tier level badge."""
        response = self.client.get("/account/")
        self.assertContains(response, 'id="tier-badge"')
        self.assertContains(response, "Level 20")

    def test_shows_billing_period_end(self):
        """Paid users see formatted billing period end date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Billing Period Ends", content)

    def test_billing_period_end_formatted(self):
        """Billing period end is formatted as dd/mm/yyyy."""
        from datetime import datetime

        self.user.billing_period_end = timezone.make_aware(
            datetime(2026, 3, 15, 12, 0, 0)
        )
        self.user.save(update_fields=["billing_period_end"])
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("15/03/2026", content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_shows_upgrade_button_for_non_premium(self):
        """Main-tier users see 'Upgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="upgrade-btn"', content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_shows_downgrade_button(self):
        """Main-tier users see 'Downgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="downgrade-btn"', content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
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

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_no_upgrade_button_for_premium(self):
        """Premium users do not see 'Upgrade' button (already at highest tier)."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="upgrade-btn"', content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_shows_downgrade_button(self):
        """Premium users see 'Downgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="downgrade-btn"', content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
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

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_shows_upgrade_button(self):
        """Basic users see 'Upgrade' button."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="upgrade-btn"', content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
    def test_no_downgrade_button_for_basic(self):
        """Basic users do not see 'Downgrade' button (lowest paid tier)."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    @override_settings(STRIPE_CHECKOUT_ENABLED=True)
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
        self.assertContains(response, 'id="pending-downgrade-notice"')
        # Verify via context that pending_tier is Basic
        self.assertEqual(response.context["pending_tier"].slug, "basic")
        self.assertContains(response, "01/04/2026")

    def test_pending_downgrade_message_format(self):
        """Notice says 'Your plan will change to {tier} on {date}'."""
        response = self.client.get("/account/")
        self.assertContains(response, "Your plan will change to")

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
        # Verify the current tier is Main via context
        self.assertEqual(response.context["tier"].slug, "main")
        self.assertContains(response, "access ends on")
        self.assertContains(response, "15/05/2026")

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


@tag('core')
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

@tag('core')
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

    @patch("payments.services.cancel_subscription")
    def test_cancel_sets_billing_period_end(self, mock_cancel):
        """Cancel API saves billing_period_end from Stripe response."""
        main_tier = Tier.objects.get(slug="main")
        self.user.tier = main_tier
        self.user.subscription_id = "sub_cancel_test"
        self.user.save(update_fields=["tier", "subscription_id"])

        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800  # 2026-03-25 00:00:00 UTC
        mock_cancel.return_value = mock_sub

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.billing_period_end)
        self.assertEqual(self.user.billing_period_end.year, 2026)
        self.assertEqual(self.user.billing_period_end.month, 3)
        self.assertEqual(self.user.billing_period_end.day, 25)

    @patch("payments.services.cancel_subscription")
    def test_cancel_sets_pending_tier_to_free(self, mock_cancel):
        """Cancel API sets pending_tier to free tier."""
        main_tier = Tier.objects.get(slug="main")
        self.user.tier = main_tier
        self.user.subscription_id = "sub_cancel_pending"
        self.user.save(update_fields=["tier", "subscription_id"])

        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800
        mock_cancel.return_value = mock_sub

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.pending_tier)
        self.assertEqual(self.user.pending_tier.slug, "free")

    # `test_cancel_without_period_end_does_not_crash` removed under
    # `_docs/testing-guidelines.md` Rule 7 (do not test impossible API states).
    # The Stripe API guarantees `current_period_end` on a Subscription object;
    # a `MagicMock(spec=[])` representing a Subscription with no attributes is
    # not a state Stripe can produce. The defensive `getattr(..., None)` in
    # `accounts/views/account.py::cancel_subscription_view` is harmless extra
    # caution but not a real bug surface that needs a regression test.

    @patch("payments.services.cancel_subscription")
    def test_cancel_then_page_shows_date(self, mock_cancel):
        """After cancellation, account page displays the billing end date."""
        main_tier = Tier.objects.get(slug="main")
        self.user.tier = main_tier
        self.user.subscription_id = "sub_cancel_show"
        self.user.save(update_fields=["tier", "subscription_id"])

        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800  # 2026-03-25 00:00:00 UTC
        mock_cancel.return_value = mock_sub

        # Cancel the subscription
        self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )

        # Load the account page
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("access ends on", content)
        self.assertIn("25/03/2026", content)


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


class TimezonePreferenceAPITest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="timezone@example.com")
        self.client.force_login(self.user)
        self.url = "/account/api/timezone-preference"

    def test_logged_out_redirects(self):
        self.client.logout()
        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Europe/Berlin"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)

    def test_get_not_allowed(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_saves_valid_timezone(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Europe/Berlin"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["timezone"], "Europe/Berlin")
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "Europe/Berlin")

    def test_clears_timezone(self):
        self.user.preferred_timezone = "America/New_York"
        self.user.save(update_fields=["preferred_timezone"])

        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": ""}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "")

    def test_invalid_timezone_does_not_overwrite_existing_preference(self):
        self.user.preferred_timezone = "Europe/Berlin"
        self.user.save(update_fields=["preferred_timezone"])

        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Invalid/Zone"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "Europe/Berlin")

    def test_missing_timezone_returns_400(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"other": "Europe/Berlin"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_json_returns_400(self):
        response = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_non_string_timezone_returns_400(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": 123}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "timezone must be a string")


class AccountPageTimezonePreferenceDisplayTest(TestCase):
    def test_shows_typeable_timezone_control_with_offset_labels(self):
        user = User.objects.create_user(email="tz-display@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'id="display-preferences-section"')
        self.assertContains(response, 'id="timezone-preference-input"')
        self.assertContains(response, 'list="timezone-preference-options"')
        self.assertContains(response, "GMT+02:00 Europe/Berlin")
        self.assertContains(response, "GMT-04:00 America/New_York")
        self.assertContains(response, "Used for event times when you are signed in.")

    def test_saved_timezone_label_is_selected(self):
        user = User.objects.create_user(
            email="tz-selected@example.com",
            preferred_timezone="Europe/Berlin",
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'value="GMT+02:00 Europe/Berlin"')
        self.assertContains(response, "Current timezone: GMT+02:00 Europe/Berlin")

    def test_timezone_options_are_sorted_by_offset_then_name(self):
        options = build_timezone_options()
        sort_keys = [(option.offset_minutes, option.value) for option in options]

        self.assertEqual(sort_keys, sorted(sort_keys))


class TimezoneOptionServiceTest(TestCase):
    def test_invalid_timezone_values_are_rejected(self):
        self.assertFalse(timezones.is_valid_timezone(""))
        self.assertFalse(timezones.is_valid_timezone("Invalid/Zone"))

    def test_get_timezone_label_returns_name_when_offset_is_unavailable(self):
        fake_local = MagicMock()
        fake_local.utcoffset.return_value = None
        fake_now = MagicMock()
        fake_now.astimezone.return_value = fake_local
        fake_utc_now = MagicMock()
        fake_utc_now.astimezone.return_value = fake_now

        with (
            patch("accounts.services.timezones.is_valid_timezone", return_value=True),
            patch("accounts.services.timezones.ZoneInfo", return_value=object()),
            patch("accounts.services.timezones.timezone.now", return_value=fake_utc_now),
        ):
            self.assertEqual(timezones.get_timezone_label("Etc/Unknown"), "Etc/Unknown")

    def test_build_timezone_options_skips_zones_without_offsets(self):
        fake_local = MagicMock()
        fake_local.utcoffset.return_value = None
        fake_now = MagicMock()
        fake_now.astimezone.return_value = fake_local
        fake_utc_now = MagicMock()
        fake_utc_now.astimezone.return_value = fake_now

        with (
            patch("accounts.services.timezones.available_timezones", return_value={"UTC"}),
            patch("accounts.services.timezones.ZoneInfo", return_value=object()),
            patch("accounts.services.timezones.timezone.now", return_value=fake_utc_now),
        ):
            self.assertEqual(timezones.build_timezone_options(), [])


class AccountPageNewsletterToggleContrastTest(TestCase):
    """Toggle dot must use a theme token that contrasts with the track in
    both states / both themes (issue #237)."""

    def _dot_html(self, content):
        # Extract the <span ... id="newsletter-toggle-dot"> tag attributes.
        marker = 'id="newsletter-toggle-dot"'
        idx = content.find(marker)
        self.assertNotEqual(idx, -1, "newsletter-toggle-dot span missing")
        start = content.rfind("<span", 0, idx)
        end = content.find(">", idx)
        return content[start:end + 1]

    def test_dot_uses_accent_foreground_when_subscribed(self):
        """Subscribed (track is bg-accent) → dot must be bg-accent-foreground
        so the value contrast stays high in dark mode (was bg-foreground = white
        on bright lime, near-invisible)."""
        user = User.objects.create_user(email="sub-dot@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        dot = self._dot_html(response.content.decode())
        self.assertIn("bg-accent-foreground", dot)
        self.assertNotIn("bg-foreground ", dot)
        self.assertNotIn('bg-foreground"', dot)

    def test_dot_uses_foreground_when_unsubscribed(self):
        """Unsubscribed (track is bg-secondary) → dot stays bg-foreground for
        strong contrast against the muted track."""
        user = User.objects.create_user(email="unsub-dot@example.com")
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        dot = self._dot_html(response.content.decode())
        self.assertIn("bg-foreground", dot)
        self.assertNotIn("bg-accent-foreground", dot)

    def test_status_text_uses_foreground_not_muted(self):
        """Status text below the toggle must be readable in dark mode — it
        previously used text-muted-foreground which dimmed it to invisibility."""
        user = User.objects.create_user(email="status@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        marker = 'id="newsletter-status"'
        idx = content.find(marker)
        self.assertNotEqual(idx, -1)
        tag_start = content.rfind("<", 0, idx)
        tag_end = content.find(">", idx)
        status_tag = content[tag_start:tag_end + 1]
        self.assertIn("text-foreground", status_tag)
        self.assertNotIn("text-muted-foreground", status_tag)

    def test_touch_target_wrapper_preserved(self):
        """The .touch-target-toggle wrapper guarantees a 44px tap area on
        mobile — must not regress when restyling the toggle."""
        user = User.objects.create_user(email="touch@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        # Wrapper must immediately precede the toggle button.
        toggle_idx = content.find('id="newsletter-toggle"')
        self.assertNotEqual(toggle_idx, -1)
        wrapper_idx = content.rfind('class="touch-target-toggle"', 0, toggle_idx)
        self.assertNotEqual(
            wrapper_idx, -1, "touch-target-toggle wrapper must wrap the toggle"
        )


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
        self.assertContains(response, "<header")
        self.assertContains(response, "AI Shipping Labs")

    def test_includes_footer(self):
        response = self.client.get("/account/")
        self.assertContains(response, "</footer>")

    def test_extends_base_template(self):
        response = self.client.get("/account/")
        self.assertContains(response, "tailwindcss")

    def test_page_title(self):
        response = self.client.get("/account/")
        self.assertContains(response, "<title>Account")


class AccountPageUserIdDisplayTest(TestCase):
    """Tests for the User ID display on the account page (issue #367)."""

    def test_logged_in_user_sees_their_user_id(self):
        """Logged-in user sees their numeric User.id rendered with the
        label 'User ID:' on the account page."""
        user = User.objects.create_user(email="userid@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        # The 'User ID:' label is present
        self.assertIn("User ID:", content)
        # The numeric id is rendered inside the user-id-value element
        marker = 'id="user-id-value"'
        idx = content.find(marker)
        self.assertNotEqual(idx, -1, "user-id-value element must be present")
        tag_end = content.find(">", idx)
        close_idx = content.find("</dd>", tag_end)
        self.assertNotEqual(close_idx, -1)
        rendered = content[tag_end + 1:close_idx].strip()
        self.assertEqual(rendered, str(user.id))

    def test_user_id_value_uses_monospace_font(self):
        """The User ID value is rendered in a monospace font for
        easy visual scanning / selection."""
        user = User.objects.create_user(email="mono@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        marker = 'id="user-id-value"'
        idx = content.find(marker)
        self.assertNotEqual(idx, -1)
        tag_start = content.rfind("<", 0, idx)
        tag_end = content.find(">", idx)
        value_tag = content[tag_start:tag_end + 1]
        self.assertIn("font-mono", value_tag)

    def test_account_info_section_present(self):
        """The new 'Account info' section is rendered on the page."""
        user = User.objects.create_user(email="info@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="account-info-section"', content)
        self.assertIn("Account info", content)

    def test_anonymous_visitor_redirected_to_login(self):
        """Anonymous visitors hitting /account/ are redirected to login —
        no User ID is exposed."""
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
