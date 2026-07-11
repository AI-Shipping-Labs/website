"""Tests for the Account page (issue #70)."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import TierOverride, User
from accounts.models.user import SIGNUP_SOURCE_NEWSLETTER
from accounts.services import timezones
from accounts.services.timezones import build_timezone_options
from email_app.models import EmailLog
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
        """GET /account/ for logged-in user renders the account page."""
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/account.html")
        self.assertEqual(response.context["user"].email, "test@example.com")

    def test_uses_correct_template(self):
        user = User.objects.create_user(email="test@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertTemplateUsed(response, "accounts/account.html")

    def test_logged_out_redirect_preserves_next(self):
        """Issue #963: the email timezone link points at /account/. An
        anonymous click hits Django's login redirect carrying
        ?next=/account/ so the user lands on the page after logging in."""
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/?next=/account/")

    def test_email_fragment_lands_on_timezone_control(self):
        """Issue #963: the #display-preferences-section fragment used by the
        event-email timezone link resolves to a real element carrying the
        timezone preference control."""
        user = User.objects.create_user(email="frag@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertContains(response, 'id="display-preferences-section"')
        self.assertContains(response, 'id="timezone-preference-input"')

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

    def test_no_tier_level_badge(self):
        """Issue #581: the Level pill was removed; the tier name is the
        only tier identifier in the Membership section."""
        response = self.client.get("/account/")
        self.assertNotContains(response, 'id="tier-badge"')
        self.assertNotContains(response, "Level 0")

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

    def test_unverified_account_shows_latest_verification_send_time(self):
        EmailLog.objects.create(
            user=self.user,
            email_type="email_verification_signup",
            ses_message_id="ses-test-id",
        )

        response = self.client.get("/account/")

        self.assertEqual(
            response.context["latest_verification_email"].ses_message_id,
            "ses-test-id",
        )
        self.assertContains(response, 'data-testid="latest-verification-email"')
        self.assertContains(response, "Last sent")

    def test_verification_resend_button_disables_on_submit(self):
        response = self.client.get("/account/")

        self.assertContains(response, "data-verification-resend-form")
        self.assertContains(response, "data-verification-resend-button")
        self.assertContains(response, "data-verification-resend-label")


class AccountPageAdminRoleTest(TestCase):
    """Tests for staff/superuser role display on account/header UI."""

    def test_staff_user_shows_admin_role_without_replacing_billing_tier(self):
        user = User.objects.create_user(
            email="staff@example.com",
            is_staff=True,
            email_verified=True,
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="account-admin-role-badge"')
        self.assertContains(response, 'data-testid="header-admin-role-badge"')
        self.assertContains(response, 'data-testid="mobile-header-admin-role-badge"')
        self.assertContains(response, "Admin")
        self.assertContains(response, 'id="tier-name"')
        self.assertContains(response, "Free")
        # Issue #581: the Level pill was removed.
        self.assertNotContains(response, 'id="tier-badge"')
        self.assertNotContains(response, "Level 0")

    def test_superuser_shows_admin_role_without_replacing_paid_tier(self):
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(
            email="superuser@example.com",
            is_superuser=True,
            email_verified=True,
        )
        user.tier = main_tier
        user.save(update_fields=["tier"])
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="account-admin-role-badge"')
        self.assertContains(response, 'data-testid="header-admin-role-badge"')
        self.assertContains(response, "Admin")
        self.assertContains(response, "Main")
        # Issue #581: the Level pill was removed.
        self.assertNotContains(response, "Level 20")
        self.assertNotContains(response, 'id="tier-badge"')


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

    def test_no_tier_level_badge(self):
        """Issue #581: the Level pill was removed for paid users too."""
        response = self.client.get("/account/")
        self.assertNotContains(response, 'id="tier-badge"')
        self.assertNotContains(response, "Level 20")

    def test_shows_billing_period_end(self):
        """Paid users see formatted billing period end date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Billing Period Ends", content)

    def test_billing_period_end_formatted(self):
        """Billing period end is formatted as a member full date."""
        from datetime import datetime

        self.user.billing_period_end = timezone.make_aware(
            datetime(2026, 3, 15, 12, 0, 0)
        )
        self.user.save(update_fields=["billing_period_end"])
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("March 15, 2026", content)

    def test_paid_user_sees_customer_portal_management(self):
        """Paid users manage billing through the Customer Portal."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="manage-subscription-btn"', content)

    def test_paid_user_does_not_see_local_downgrade_button(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    def test_paid_user_does_not_see_local_cancel_button(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="cancel-btn"', content)

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

    def test_premium_user_does_not_see_local_downgrade_button(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    def test_premium_user_does_not_see_local_cancel_button(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="cancel-btn"', content)


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

    def test_basic_user_uses_customer_portal_not_upgrade_button(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="manage-subscription-btn"', content)
        self.assertNotIn('id="upgrade-btn"', content)

    def test_no_downgrade_button_for_basic(self):
        """Basic users do not see 'Downgrade' button (lowest paid tier)."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="downgrade-btn"', content)

    def test_basic_user_does_not_see_local_cancel_button(self):
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="cancel-btn"', content)


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
        self.assertContains(response, "May 15, 2026")

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


@override_settings(
    STRIPE_CUSTOMER_PORTAL_URL="https://billing.example.test/portal",
)
class AccountPageMembershipActionStateTest(TestCase):
    """Focused tests for plan-appropriate account actions (#401)."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get(slug="free")
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.main_tier = Tier.objects.get(slug="main")
        cls.premium_tier = Tier.objects.get(slug="premium")

    def _user(self, email, tier=None, subscription_id="", pending_tier=None):
        user = User.objects.create_user(email=email)
        user.tier = tier
        user.subscription_id = subscription_id
        user.pending_tier = pending_tier
        user.billing_period_end = timezone.make_aware(
            timezone.datetime(2026, 5, 29, 12, 0, 0)
        )
        user.save(
            update_fields=[
                "tier",
                "subscription_id",
                "pending_tier",
                "billing_period_end",
            ]
        )
        return user

    def _account_response(self, user):
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        return response

    def test_free_member_gets_upgrade_to_pricing(self):
        response = self._account_response(
            self._user("free-action@example.com", self.free_tier)
        )

        # Issue #581: the steady-state ``Current free plan`` frame was
        # suppressed; the upgrade CTA still points to /pricing.
        self.assertNotContains(response, "Current free plan")
        self.assertNotContains(response, 'id="account-plan-state"')
        self.assertContains(response, 'id="upgrade-btn"')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, 'id="manage-subscription-btn"')

    def test_premium_member_has_no_upgrade_cta(self):
        response = self._account_response(
            self._user("premium-action@example.com", self.premium_tier, "sub_premium")
        )

        # Issue #581: the steady-state ``Current plan`` frame was
        # suppressed; the action buttons still reflect the highest tier.
        self.assertNotContains(response, 'id="account-plan-state"')
        self.assertNotContains(response, 'id="upgrade-btn"')
        self.assertContains(response, 'id="manage-subscription-btn"')
        self.assertNotContains(response, 'id="downgrade-btn"')
        self.assertNotContains(response, 'id="cancel-btn"')

    def test_non_free_pending_tier_renders_no_dead_downgrade_ui(self):
        # Issue #968: nothing produces a non-free pending_tier anymore, so
        # the amber "scheduled change" downgrade notice is removed. Even if
        # such state existed, it must not render the dead UI.
        response = self._account_response(
            self._user(
                "pending-action@example.com",
                self.main_tier,
                "sub_pending",
                self.basic_tier,
            )
        )

        self.assertNotContains(response, 'id="pending-downgrade-notice"')
        self.assertNotContains(response, "Scheduled change")
        self.assertNotContains(response, "at period end")
        # The user keeps the Manage Subscription path to the portal.
        self.assertContains(response, 'id="manage-subscription-btn"')

    def test_pending_cancellation_directs_to_subscription_management(self):
        response = self._account_response(
            self._user(
                "canceling-action@example.com",
                self.basic_tier,
                "sub_canceling",
                self.free_tier,
            )
        )

        # Issue #581: the duplicate plan-state frame is suppressed; the
        # dedicated red pending-cancellation notice still carries the
        # current tier name and end date.
        self.assertNotContains(response, 'id="account-plan-state"')
        self.assertContains(response, 'id="pending-cancellation-notice"')
        self.assertContains(response, 'Basic')
        self.assertContains(response, 'May 29, 2026')
        self.assertNotContains(response, 'id="cancel-btn"')
        self.assertNotContains(response, 'id="upgrade-btn"')
        self.assertContains(response, 'id="manage-subscription-btn"')

    def test_temporary_override_hides_normal_upgrade_to_override_tier(self):
        user = self._user("override-action@example.com", self.basic_tier, "sub_basic")
        TierOverride.objects.create(
            user=user,
            original_tier=self.basic_tier,
            override_tier=self.premium_tier,
            expires_at=timezone.now() + timedelta(days=14),
        )

        response = self._account_response(user)

        self.assertContains(response, "Temporary Premium access")
        self.assertContains(
            response,
            "Base subscription. Temporary Premium access is active.",
        )
        self.assertNotContains(response, 'id="upgrade-btn"')
        self.assertContains(response, 'id="manage-subscription-btn"')

    def test_stale_subscription_uses_manage_subscription_not_upgrade(self):
        response = self._account_response(
            self._user("stale-action@example.com", None, "sub_stale")
        )

        self.assertContains(response, "Your subscription needs review.")
        self.assertNotContains(response, 'id="upgrade-btn"')
        self.assertContains(response, 'id="manage-subscription-btn"')


@override_settings(
    STRIPE_CUSTOMER_PORTAL_URL="https://billing.example.test/portal",
)
class AccountPageMembershipCardServingMembersTest(TestCase):
    """Focused Membership-card rendering states for issue #1207."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get(slug="free")
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.main_tier = Tier.objects.get(slug="main")
        cls.premium_tier = Tier.objects.get(slug="premium")

    def _user(
        self,
        email,
        tier,
        *,
        subscription_id="",
        billing_period_end=None,
        pending_tier=None,
        signup_source="signup",
        account_activated=True,
    ):
        user = User.objects.create_user(
            email=email,
            signup_source=signup_source,
            account_activated=account_activated,
            email_verified=True,
        )
        user.tier = tier
        user.subscription_id = subscription_id
        user.billing_period_end = billing_period_end
        user.pending_tier = pending_tier
        user.save(
            update_fields=[
                "tier",
                "subscription_id",
                "billing_period_end",
                "pending_tier",
                "signup_source",
                "account_activated",
                "email_verified",
            ]
        )
        return user

    def _account_response(self, user):
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        return response

    def test_free_member_keeps_upgrade_and_benefits_without_billing_ui(self):
        response = self._account_response(
            self._user("free-1207@example.com", self.free_tier)
        )

        self.assertContains(response, 'id="current-tier-benefits"')
        self.assertContains(response, "Newsletter updates")
        self.assertContains(response, 'id="upgrade-btn"')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, 'id="manage-subscription-btn"')
        self.assertNotContains(response, "Billing Period Ends")
        self.assertNotContains(response, "Temporary Access Expires")

    def test_basic_member_gets_benefits_billing_portal_and_main_upsell(self):
        user = self._user(
            "basic-1207@example.com",
            self.basic_tier,
            subscription_id="sub_basic_1207",
            billing_period_end=timezone.make_aware(
                timezone.datetime(2026, 3, 15, 12, 0, 0)
            ),
        )

        response = self._account_response(user)

        self.assertEqual(
            response.context["next_tier_upsell"]["target_slug"],
            "main",
        )
        self.assertContains(response, "Everything in Free")
        self.assertContains(response, "March 15, 2026")
        self.assertContains(response, 'id="manage-subscription-btn"')
        self.assertContains(response, "https://billing.example.test/portal")
        self.assertContains(response, 'id="next-tier-upsell"')
        self.assertContains(response, "community")
        self.assertContains(response, "live and group work")
        self.assertContains(response, "accountability")
        self.assertContains(response, "topic voting")
        self.assertContains(response, 'id="next-tier-upsell-btn"')
        self.assertContains(response, 'href="/pricing"')

    def test_main_member_gets_benefits_billing_portal_and_premium_upsell(self):
        user = self._user(
            "main-1207@example.com",
            self.main_tier,
            subscription_id="sub_main_1207",
            billing_period_end=timezone.make_aware(
                timezone.datetime(2026, 4, 1, 12, 0, 0)
            ),
        )

        response = self._account_response(user)

        self.assertEqual(
            response.context["next_tier_upsell"]["target_slug"], "premium"
        )
        self.assertContains(response, "Everything in Basic")
        self.assertContains(response, "April 1, 2026")
        self.assertContains(response, 'id="manage-subscription-btn"')
        self.assertContains(response, 'id="next-tier-upsell"')
        self.assertContains(response, "mini-courses")
        self.assertContains(response, "course-topic voting")
        self.assertContains(response, "LinkedIn")
        self.assertContains(response, "GitHub teardowns")

    def test_premium_member_gets_benefits_portal_and_no_upsell(self):
        response = self._account_response(
            self._user(
                "premium-1207@example.com",
                self.premium_tier,
                subscription_id="sub_premium_1207",
                billing_period_end=timezone.make_aware(
                    timezone.datetime(2026, 5, 1, 12, 0, 0)
                ),
            )
        )

        self.assertContains(response, "Everything in Main")
        self.assertContains(response, 'id="manage-subscription-btn"')
        self.assertContains(response, 'id="highest-tier-note"')
        self.assertContains(response, "highest tier")
        self.assertNotContains(response, 'id="next-tier-upsell"')
        self.assertNotContains(response, 'id="upgrade-btn"')

    def test_active_override_without_subscription_shows_temporary_expiry(self):
        user = self._user("override-main-1207@example.com", self.free_tier)
        TierOverride.objects.create(
            user=user,
            original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.make_aware(
                timezone.datetime(2027, 6, 5, 18, 30, 0)
            ),
        )

        response = self._account_response(user)

        self.assertEqual(response.context["effective_tier"].slug, "main")
        self.assertContains(response, "Main")
        self.assertContains(response, 'id="tier-override-provenance"')
        self.assertContains(response, "tier override from Free")
        self.assertContains(response, 'id="tier-override-notice"')
        self.assertContains(response, "Temporary Main access")
        self.assertContains(response, 'id="temporary-access-expiry"')
        self.assertContains(response, "Temporary Access Expires")
        self.assertContains(response, "05/06/2027")
        self.assertNotContains(response, "Billing Period Ends")
        self.assertNotContains(response, 'id="manage-subscription-btn"')
        self.assertContains(response, 'id="paid-plan-pricing-btn"')
        self.assertContains(response, "keep access after temporary access ends")

    def test_paid_member_without_subscription_gets_pricing_path_not_portal(self):
        response = self._account_response(
            self._user("comped-basic-1207@example.com", self.basic_tier)
        )

        self.assertContains(response, "Everything in Free")
        self.assertContains(response, 'id="paid-without-subscription-note"')
        self.assertContains(response, "No Stripe subscription is connected")
        self.assertContains(response, 'id="paid-plan-pricing-btn"')
        self.assertContains(response, 'href="/pricing"')
        self.assertNotContains(response, 'id="manage-subscription-btn"')

    def test_pending_cancellation_keeps_warning_and_portal_path(self):
        response = self._account_response(
            self._user(
                "cancel-main-1207@example.com",
                self.main_tier,
                subscription_id="sub_cancel_main_1207",
                billing_period_end=timezone.make_aware(
                    timezone.datetime(2026, 5, 15, 12, 0, 0)
                ),
                pending_tier=self.free_tier,
            )
        )

        self.assertContains(response, 'id="pending-cancellation-notice"')
        self.assertContains(response, "Main")
        self.assertContains(response, "May 15, 2026")
        self.assertContains(response, 'id="manage-subscription-btn"')
        self.assertNotContains(response, 'id="cancel-btn"')
        self.assertNotContains(response, 'id="downgrade-btn"')
        self.assertNotContains(response, 'id="upgrade-modal"')

    def test_newsletter_only_user_does_not_see_membership_card(self):
        response = self._account_response(
            self._user(
                "newsletter-only-1207@example.com",
                self.free_tier,
                signup_source=SIGNUP_SOURCE_NEWSLETTER,
                account_activated=False,
            )
        )

        self.assertContains(response, 'id="newsletter-only-cta"')
        self.assertNotContains(response, 'id="membership-section"')
        self.assertNotContains(response, 'id="current-tier-benefits"')
        self.assertNotContains(response, 'id="next-tier-upsell"')
        self.assertNotContains(response, "Billing Period Ends")
        self.assertNotContains(response, "Temporary Access Expires")
        self.assertNotContains(response, 'id="manage-subscription-btn"')


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

    # Issue #655: workshop_emails preference round-trips.

    def test_email_preferences_accepts_workshop_emails_field(self):
        """POST workshop_emails=False persists the flag and the response
        echoes back only the updated field."""
        original_unsubscribed = self.user.unsubscribed

        response = self.client.post(
            self.url,
            data=json.dumps({"workshop_emails": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["workshop_emails"])
        self.assertNotIn("newsletter", data)

        self.user.refresh_from_db()
        self.assertFalse(
            self.user.email_preferences.get("workshop_emails"),
        )
        # unsubscribed must NOT be touched when only workshop_emails was set.
        self.assertEqual(self.user.unsubscribed, original_unsubscribed)

    def test_email_preferences_accepts_combined_payload(self):
        """Combined newsletter + workshop_emails payload updates both
        fields in a single save."""
        with patch.object(
            type(self.user), "save", autospec=True, side_effect=type(self.user).save,
        ) as mock_save:
            response = self.client.post(
                self.url,
                data=json.dumps({
                    "newsletter": True,
                    "workshop_emails": False,
                }),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["newsletter"])
        self.assertFalse(data["workshop_emails"])

        self.user.refresh_from_db()
        self.assertFalse(self.user.unsubscribed)
        self.assertTrue(self.user.email_preferences.get("newsletter"))
        self.assertFalse(self.user.email_preferences.get("workshop_emails"))

        # One save call carrying both fields in update_fields.
        save_calls = [
            call for call in mock_save.call_args_list
            if call.kwargs.get("update_fields") is not None
        ]
        self.assertEqual(len(save_calls), 1, save_calls)
        update_fields = set(save_calls[0].kwargs["update_fields"])
        self.assertIn("email_preferences", update_fields)
        self.assertIn("unsubscribed", update_fields)

    def test_email_preferences_accepts_sprint_cadence_emails_field(self):
        """Sprint reminder email opt-out persists without changing global
        newsletter subscription state."""
        original_unsubscribed = self.user.unsubscribed

        response = self.client.post(
            self.url,
            data=json.dumps({"sprint_cadence_emails": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")
        self.assertFalse(data["sprint_cadence_emails"])
        self.assertNotIn("newsletter", data)

        self.user.refresh_from_db()
        self.assertFalse(
            self.user.email_preferences.get("sprint_cadence_emails"),
        )
        self.assertEqual(self.user.unsubscribed, original_unsubscribed)

    def test_email_preferences_workshop_emails_default_true_in_context(self):
        """A brand-new user with empty ``email_preferences`` defaults to
        ``workshop_emails_enabled=True`` in the account context."""
        fresh = User.objects.create_user(email="default@example.com")
        self.assertEqual(fresh.email_preferences, {})
        self.client.force_login(fresh)
        response = self.client.get("/account/")
        self.assertTrue(response.context["workshop_emails_enabled"])

    def test_email_preferences_rejects_empty_payload(self):
        response = self.client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_email_preferences_rejects_non_bool_workshop_emails(self):
        """A string value for workshop_emails is not a boolean and must
        be rejected -- it would silently coerce to truthy via the old
        ``data.get`` path."""
        response = self.client.post(
            self.url,
            data=json.dumps({"workshop_emails": "yes"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_email_preferences_rejects_non_bool_sprint_cadence_emails(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"sprint_cadence_emails": "yes"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)


class AccountPageWorkshopEmailsToggleTest(TestCase):
    """Issue #655: the Email Preferences card carries a workshop
    announcements toggle row alongside the existing newsletter row."""

    def test_account_page_renders_workshop_emails_toggle(self):
        user = User.objects.create_user(email="wstoggle@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")

        self.assertContains(response, 'id="workshop-emails-toggle"')
        self.assertContains(response, 'id="workshop-emails-status"')
        self.assertContains(response, "Workshop announcements")
        self.assertContains(
            response,
            "Receive an email when staff publish a new workshop you have access to.",
        )

    def test_default_workshop_emails_toggle_is_on(self):
        user = User.objects.create_user(email="wstoggle-on@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")

        self.assertTrue(response.context["workshop_emails_enabled"])
        self.assertContains(response, 'id="workshop-emails-status"')
        self.assertNotContains(
            response, "You will receive workshop announcement emails.",
        )

    def test_opted_out_workshop_emails_toggle_is_off(self):
        user = User.objects.create_user(email="wstoggle-off@example.com")
        user.email_preferences = {"workshop_emails": False}
        user.save(update_fields=["email_preferences"])
        self.client.force_login(user)
        response = self.client.get("/account/")

        self.assertFalse(response.context["workshop_emails_enabled"])
        self.assertNotContains(
            response, "You will not receive workshop announcement emails.",
        )


class AccountPageSprintCadenceEmailsToggleTest(TestCase):
    """Issue #1200: account page carries a sprint reminder email toggle."""

    def test_account_page_renders_sprint_cadence_toggle(self):
        user = User.objects.create_user(email="sprint-toggle@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")

        self.assertContains(response, 'id="sprint-cadence-emails-toggle"')
        self.assertContains(response, 'id="sprint-cadence-emails-status"')
        self.assertContains(response, "Sprint reminders")
        self.assertContains(
            response,
            "Receive weekly sprint plan and note reminder emails.",
        )

    def test_default_sprint_cadence_toggle_is_on(self):
        user = User.objects.create_user(email="sprint-toggle-on@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")

        self.assertTrue(response.context["sprint_cadence_emails_enabled"])
        self.assertContains(response, 'id="sprint-cadence-emails-status"')
        self.assertNotContains(response, "You will receive sprint reminder emails.")

    def test_opted_out_sprint_cadence_toggle_is_off(self):
        user = User.objects.create_user(email="sprint-toggle-off@example.com")
        user.email_preferences = {"sprint_cadence_emails": False}
        user.save(update_fields=["email_preferences"])
        self.client.force_login(user)
        response = self.client.get("/account/")

        self.assertFalse(response.context["sprint_cadence_emails_enabled"])
        self.assertNotContains(
            response, "You will not receive sprint reminder emails.",
        )


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

    def test_subscribed_status_hidden_initially(self):
        user = User.objects.create_user(email="sub@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="newsletter-status"', content)
        self.assertIn('aria-live="polite"', content)
        self.assertNotIn("You are subscribed to newsletters.", content)

    def test_unsubscribed_status_hidden_initially(self):
        user = User.objects.create_user(email="unsub@example.com")
        user.unsubscribed = True
        user.save(update_fields=["unsubscribed"])
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="newsletter-status"', content)
        self.assertNotIn("You are unsubscribed from newsletters.", content)

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


class AccountPagePolish1206Test(TestCase):
    def test_activated_member_sections_render_in_job_first_order_with_slack(self):
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(
            email="order-1206@example.com",
            tier=main_tier,
            email_verified=True,
        )
        self.client.force_login(user)

        with self.settings(SLACK_INVITE_URL="https://slack.example/invite"):
            response = self.client.get("/account/")

        content = response.content.decode()
        markers = [
            'data-lucide="crown"',
            'id="email-preferences-section"',
            'data-testid="slack-account-card"',
            'id="api-keys"',
            'id="display-preferences-section"',
            'id="change-password-section"',
            'id="profile-section"',
            'id="account-info-section"',
        ]
        positions = [content.index(marker) for marker in markers]
        self.assertEqual(positions, sorted(positions))

    def test_account_flash_message_classes_cover_light_and_dark_states(self):
        template = Path("templates/accounts/account.html").read_text()

        expected_classes = [
            "border-green-200 bg-green-50 text-green-800",
            "dark:border-green-500/30 dark:bg-green-500/15 dark:text-green-200",
            "border-amber-200 bg-amber-50 text-amber-900",
            "dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-100",
            "border-red-200 bg-red-50 text-red-800",
            "dark:border-red-500/30 dark:bg-red-500/15 dark:text-red-200",
            "border-blue-200 bg-blue-50 text-blue-800",
            "dark:border-blue-500/30 dark:bg-blue-500/15 dark:text-blue-200",
        ]
        for class_names in expected_classes:
            self.assertIn(class_names, template)

    def test_timezone_card_has_one_save_action_no_clear_or_current_caption(self):
        user = User.objects.create_user(
            email="tz-polish-1206@example.com",
            preferred_timezone="America/New_York",
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-testid="save-timezone-btn"', count=1)
        self.assertNotContains(response, 'data-testid="clear-timezone-btn"')
        self.assertNotContains(response, "Current timezone:")
        self.assertNotContains(response, "Saved timezone:")

        content = response.content.decode()
        status_marker = 'id="timezone-preference-status"'
        status_idx = content.index(status_marker)
        status_tag_end = content.index(">", status_idx)
        status_close = content.index("</p>", status_tag_end)
        status_text = content[status_tag_end + 1:status_close].strip()
        self.assertEqual(status_text, "Using saved timezone for event times.")
        self.assertNotIn("America/New_York", status_text)
        self.assertNotIn("GMT-04:00", status_text)


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

    def test_passive_backfill_persists_zone_when_empty(self):
        # Issue #961: passive path backfills an empty preferred_timezone.
        self.assertEqual(self.user.preferred_timezone, "")

        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Europe/Berlin", "passive": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["timezone"], "Europe/Berlin")
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "Europe/Berlin")

    def test_passive_backfill_does_not_overwrite_existing_zone(self):
        # Issue #961: passive path is a no-op when a value is already set.
        self.user.preferred_timezone = "America/New_York"
        self.user.save(update_fields=["preferred_timezone"])

        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Europe/Berlin", "passive": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["timezone"], "America/New_York")
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "America/New_York")

    def test_passive_backfill_rejects_invalid_zone(self):
        # Issue #961: invalid zones are still rejected with 400 on the
        # passive path; the empty value is left unchanged.
        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Not/AZone", "passive": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "")

    def test_manual_save_overwrites_existing_zone(self):
        # Issue #961: deliberate Account-settings saves (no passive flag)
        # must keep overwriting a non-empty value.
        self.user.preferred_timezone = "America/New_York"
        self.user.save(update_fields=["preferred_timezone"])

        response = self.client.post(
            self.url,
            data=json.dumps({"timezone": "Asia/Tokyo"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "Asia/Tokyo")


class AccountPageTimezonePreferenceDisplayTest(TestCase):
    def test_account_timezone_renders_as_select_not_datalist(self):
        user = User.objects.create_user(email="tz-display@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'id="display-preferences-section"')
        self.assertContains(response, "<select", html=False)
        self.assertContains(response, 'id="timezone-preference-input"')
        self.assertContains(response, 'name="timezone"')
        self.assertNotContains(response, '<datalist id="timezone-preference-options"')
        self.assertNotContains(response, 'list="timezone-preference-options"')
        self.assertContains(response, "GMT+02:00 Europe/Berlin")
        self.assertContains(response, "GMT-04:00 America/New_York")
        self.assertContains(response, "Used for event times when you are signed in.")

    def test_account_timezone_select_includes_browser_default_option(self):
        user = User.objects.create_user(email="tz-default@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, '<option value="">Use browser timezone</option>')

    def test_account_timezone_select_uses_app_select_class(self):
        user = User.objects.create_user(email="tz-class@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()

        marker = 'id="timezone-preference-input"'
        self.assertIn(marker, content)
        tag_start = content.rfind("<select", 0, content.index(marker))
        tag_end = content.index(">", content.index(marker))
        select_tag = content[tag_start:tag_end]
        self.assertIn("app-select", select_tag)

    def test_saved_timezone_label_is_selected(self):
        user = User.objects.create_user(
            email="tz-selected@example.com",
            preferred_timezone="Europe/Berlin",
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, '<option value="Europe/Berlin" selected>')
        self.assertContains(response, "Using saved timezone for event times.")
        self.assertNotContains(response, "Saved timezone:")
        self.assertNotContains(response, "Current timezone:")

    def test_no_saved_preference_marks_select_for_browser_detection(self):
        """Issue #582: the select needs a ``data-has-preference=false``
        marker so the inline detection script knows it may overwrite the
        empty value with the resolved browser timezone."""
        user = User.objects.create_user(email="tz-empty@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-has-preference="false"')
        # The detected-hint placeholder element ships hidden; the script
        # toggles it visible after detection succeeds.
        self.assertContains(
            response, 'data-testid="timezone-detected-hint"'
        )
        self.assertContains(response, 'id="timezone-detected-hint"')
        # The browser-default affordance must be an empty option, not a
        # rendered saved value.
        self.assertNotContains(response, 'value="Use browser timezone"')

    def test_saved_preference_marks_input_has_preference_true(self):
        """Issue #582: when a preference is saved, the marker flips to
        ``true`` so the inline script will not overwrite the formatted
        label with the raw IANA name from the browser."""
        user = User.objects.create_user(
            email="tz-marked@example.com",
            preferred_timezone="America/New_York",
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertContains(response, 'data-has-preference="true"')

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

    def test_context_no_longer_exposes_local_upgrade_downgrade_tiers(self):
        user = User.objects.create_user(email="ctx-up@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertNotIn("upgrade_tiers", response.context)
        self.assertNotIn("downgrade_tiers", response.context)


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


class AccountPageSupportIdDisplayTest(TestCase):
    """Tests for the support identifier on the account page."""

    def test_logged_in_user_sees_their_support_id(self):
        """Logged-in users see their numeric User.id as a support ID."""
        user = User.objects.create_user(email="userid@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)

        content = response.content.decode()
        self.assertIn("Support ID", content)
        self.assertIn("Quote this in support requests.", content)
        self.assertNotIn("User ID:", content)
        marker = 'id="support-id-value"'
        idx = content.find(marker)
        self.assertNotEqual(idx, -1, "support-id-value element must be present")
        tag_end = content.find(">", idx)
        close_idx = content.find("</dd>", tag_end)
        self.assertNotEqual(close_idx, -1)
        rendered = content[tag_end + 1:close_idx].strip()
        self.assertEqual(rendered, str(user.id))

    def test_support_id_value_uses_monospace_font(self):
        """The Support ID value is rendered in a monospace font for
        easy visual scanning / selection."""
        user = User.objects.create_user(email="mono@example.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        marker = 'id="support-id-value"'
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
        no Support ID is exposed."""
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)
