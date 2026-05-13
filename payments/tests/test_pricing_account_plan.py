from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier


@override_settings(
    STRIPE_CHECKOUT_ENABLED=True,
    STRIPE_CUSTOMER_PORTAL_URL="https://billing.example.test/portal",
)
class PricingAccountPlanStateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.free = Tier.objects.get(slug="free")
        cls.basic = Tier.objects.get(slug="basic")
        cls.main = Tier.objects.get(slug="main")
        cls.premium = Tier.objects.get(slug="premium")

    def _user(self, email, tier=None, subscription_id="", pending_tier=None):
        user = self.User.objects.create_user(email=email, password="testpass123")
        user.tier = tier if tier is not None else self.free
        user.subscription_id = subscription_id
        user.pending_tier = pending_tier
        user.save(update_fields=["tier", "subscription_id", "pending_tier"])
        return user

    def _pricing_states(self, user=None):
        if user is not None:
            self.client.force_login(user)
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        return {
            item["tier"].slug: item["state"]
            for item in response.context["tiers_data"]
        }, response

    def test_anonymous_visitors_keep_public_join_actions(self):
        states, response = self._pricing_states()

        self.assertEqual(states["free"]["action_label"], "Get the newsletter")
        for slug in ("basic", "main", "premium"):
            self.assertEqual(states[slug]["action_label"], "Join")
            self.assertEqual(states[slug]["action_kind"], "checkout")
        self.assertContains(response, 'data-link-monthly')
        self.assertContains(response, 'data-link-annual')

    def test_free_member_sees_free_current_and_paid_upgrades(self):
        user = self._user("free-pricing@test.com", self.free)
        states, response = self._pricing_states(user)

        self.assertEqual(states["free"]["badge"], "Current free plan")
        self.assertEqual(states["free"]["action_kind"], "disabled")
        for slug in ("basic", "main", "premium"):
            self.assertEqual(states[slug]["action_label"], "Upgrade")
            self.assertEqual(states[slug]["action_kind"], "checkout")
        self.assertContains(response, "Current free plan")

    def test_basic_member_sees_current_basic_and_higher_upgrades(self):
        user = self._user("basic-pricing@test.com", self.basic, "sub_basic")
        states, _response = self._pricing_states(user)

        self.assertEqual(states["free"]["badge"], "Included")
        self.assertEqual(states["basic"]["badge"], "Current plan")
        self.assertEqual(states["basic"]["action_kind"], "disabled")
        self.assertEqual(states["main"]["action_label"], "Manage Subscription")
        self.assertEqual(states["main"]["action_kind"], "portal")
        self.assertEqual(states["premium"]["action_label"], "Manage Subscription")
        self.assertEqual(states["premium"]["action_kind"], "portal")

    def test_main_member_sees_lower_tier_as_downgrade_not_join(self):
        user = self._user("main-pricing@test.com", self.main, "sub_main")
        states, _response = self._pricing_states(user)

        self.assertEqual(states["main"]["badge"], "Current plan")
        self.assertEqual(states["main"]["action_kind"], "disabled")
        self.assertEqual(states["basic"]["action_label"], "Downgrade")
        self.assertEqual(states["basic"]["action_kind"], "portal")
        self.assertEqual(states["premium"]["action_label"], "Manage Subscription")
        self.assertEqual(states["premium"]["action_kind"], "portal")

    def test_premium_member_sees_lower_paid_tiers_as_management(self):
        user = self._user("premium-pricing@test.com", self.premium, "sub_premium")
        states, _response = self._pricing_states(user)

        self.assertEqual(states["premium"]["badge"], "Current plan")
        self.assertEqual(states["premium"]["action_kind"], "disabled")
        for slug in ("basic", "main"):
            self.assertEqual(states[slug]["action_label"], "Downgrade")
            self.assertEqual(states[slug]["action_kind"], "portal")

    def test_pending_paid_downgrade_shows_scheduled_change_date(self):
        user = self._user(
            "main-pending-pricing@test.com",
            self.main,
            "sub_pending",
            self.basic,
        )
        user.billing_period_end = datetime(2026, 5, 29, 12, 0, tzinfo=dt_timezone.utc)
        user.save(update_fields=["billing_period_end"])
        states, response = self._pricing_states(user)

        self.assertEqual(states["main"]["badge"], "Current plan")
        self.assertIn("changes to Basic on May 29, 2026", states["main"]["note"])
        self.assertEqual(states["main"]["action_kind"], "disabled")
        self.assertEqual(states["basic"]["badge"], "Scheduled change")
        self.assertIn("May 29, 2026", states["basic"]["note"])
        self.assertEqual(states["basic"]["action_kind"], "portal")
        self.assertContains(response, "Scheduled change")

    def test_pending_cancellation_shows_access_ending_without_join_prompts(self):
        user = self._user(
            "canceling-pricing@test.com",
            self.basic,
            "sub_canceling",
            self.free,
        )
        user.billing_period_end = datetime(2026, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
        user.save(update_fields=["billing_period_end"])
        states, response = self._pricing_states(user)

        self.assertEqual(states["basic"]["badge"], "Access ending")
        self.assertIn("Access ends on June 15, 2026", states["basic"]["note"])
        self.assertEqual(states["free"]["badge"], "Included")
        for slug in ("basic", "main", "premium"):
            self.assertEqual(states[slug]["action_kind"], "portal")
            self.assertNotEqual(states[slug]["action_label"], "Join")
        self.assertContains(response, "Access ending")

    def test_temporary_override_distinguishes_base_from_override(self):
        user = self._user("override-pricing@test.com", self.basic, "sub_basic")
        TierOverride.objects.create(
            user=user,
            original_tier=self.basic,
            override_tier=self.premium,
            expires_at=timezone.now() + timedelta(days=14),
        )

        states, response = self._pricing_states(user)

        self.assertEqual(states["basic"]["badge"], "Current plan")
        self.assertIn("Base subscription", states["basic"]["note"])
        self.assertEqual(states["premium"]["badge"], "Temporary access")
        self.assertEqual(states["premium"]["action_kind"], "portal")
        self.assertEqual(states["main"]["action_kind"], "portal")
        self.assertContains(response, "Temporary access")

    def test_stale_subscription_uses_safe_management_for_paid_tiers(self):
        user = self._user("stale-pricing@test.com", self.free, "sub_stale")
        user.tier = None
        user.save(update_fields=["tier"])

        states, response = self._pricing_states(user)

        for slug in ("basic", "main", "premium"):
            self.assertEqual(states[slug]["action_label"], "Manage Subscription")
            self.assertEqual(states[slug]["action_kind"], "portal")
        self.assertNotContains(response, ">Join</a>")
        self.assertContains(response, "Your subscription needs review")
