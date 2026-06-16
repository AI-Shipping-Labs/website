from datetime import datetime
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from payments.models import Tier
from payments.tier_state import build_tier_state, format_period_end


class TierStateSnapshotTest(TestCase):
    maxDiff = None

    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.tiers = {
            tier.slug: tier
            for tier in Tier.objects.order_by("level")
        }

    def _user(self, email, tier=None, subscription_id="", pending_tier=None):
        user = self.User.objects.create_user(email=email, password="testpass123")
        user.tier = tier if tier is not None else self.tiers["free"]
        user.subscription_id = subscription_id
        user.pending_tier = pending_tier
        user.save(update_fields=["tier", "subscription_id", "pending_tier"])
        return user

    def _states_for(self, user, active_override=None):
        return {
            slug: build_tier_state(tier, user, active_override)
            for slug, tier in self.tiers.items()
        }

    def test_anonymous_visitor_sees_signup_and_join_actions(self):
        self.assertEqual(
            self._states_for(AnonymousUser()),
            {
                "free": {
                    "badge": "",
                    "note": "",
                    "action_label": "Create an account",
                    "action_kind": "signup",
                },
                "basic": {
                    "badge": "",
                    "note": "",
                    "action_label": "Join",
                    "action_kind": "checkout",
                },
                "main": {
                    "badge": "",
                    "note": "",
                    "action_label": "Join",
                    "action_kind": "checkout",
                },
                "premium": {
                    "badge": "",
                    "note": "",
                    "action_label": "Join",
                    "action_kind": "checkout",
                },
            },
        )

    def test_free_member_sees_current_free_plan_and_paid_upgrades(self):
        user = self._user("free-tier-state@example.com")

        self.assertEqual(
            self._states_for(user),
            {
                "free": {
                    "badge": "Current free plan",
                    "note": "You are on the free membership.",
                    "action_label": "Current plan",
                    "action_kind": "disabled",
                },
                "basic": {
                    "badge": "",
                    "note": "",
                    "action_label": "Upgrade",
                    "action_kind": "checkout",
                },
                "main": {
                    "badge": "",
                    "note": "",
                    "action_label": "Upgrade",
                    "action_kind": "checkout",
                },
                "premium": {
                    "badge": "",
                    "note": "",
                    "action_label": "Upgrade",
                    "action_kind": "checkout",
                },
            },
        )

    def test_paid_member_sees_current_included_and_portal_changes(self):
        user = self._user(
            "main-tier-state@example.com",
            self.tiers["main"],
            "sub_main_snapshot",
        )

        self.assertEqual(
            self._states_for(user),
            {
                "free": {
                    "badge": "Included",
                    "note": "Included with every paid membership.",
                    "action_label": "Included",
                    "action_kind": "disabled",
                },
                "basic": {
                    "badge": "",
                    "note": "Manage your subscription to switch to this tier.",
                    "action_label": "Downgrade",
                    "action_kind": "portal",
                },
                "main": {
                    "badge": "Current plan",
                    "note": "",
                    "action_label": "Current plan",
                    "action_kind": "disabled",
                },
                "premium": {
                    "badge": "",
                    "note": "Manage your subscription to switch to this tier.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
            },
        )

    def test_canceling_paid_member_is_routed_through_portal(self):
        user = self._user(
            "canceling-tier-state@example.com",
            self.tiers["basic"],
            "sub_basic_canceling",
            self.tiers["free"],
        )
        user.billing_period_end = datetime(
            2026, 6, 15, 12, 0, tzinfo=dt_timezone.utc
        )
        user.save(update_fields=["billing_period_end"])

        self.assertEqual(
            self._states_for(user),
            {
                "free": {
                    "badge": "Included",
                    "note": "Free access continues after June 15, 2026.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "basic": {
                    "badge": "Access ending",
                    "note": "Access ends on June 15, 2026.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "main": {
                    "badge": "",
                    "note": "Your subscription is already scheduled to cancel.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "premium": {
                    "badge": "",
                    "note": "Your subscription is already scheduled to cancel.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
            },
        )

    def test_stale_subscription_stays_out_of_checkout_flows(self):
        user = self._user(
            "stale-tier-state@example.com",
            self.tiers["free"],
            "sub_stale_snapshot",
        )
        user.tier = None
        user.save(update_fields=["tier"])

        self.assertEqual(
            self._states_for(user),
            {
                "free": {
                    "badge": "Included",
                    "note": "Your subscription needs review.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "basic": {
                    "badge": "Manage Subscription",
                    "note": (
                        "Your subscription needs review before changing plans."
                    ),
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "main": {
                    "badge": "Manage Subscription",
                    "note": (
                        "Your subscription needs review before changing plans."
                    ),
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "premium": {
                    "badge": "Manage Subscription",
                    "note": (
                        "Your subscription needs review before changing plans."
                    ),
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
            },
        )

    def test_active_override_preserves_base_and_temporary_messages(self):
        user = self._user(
            "override-tier-state@example.com",
            self.tiers["basic"],
            "sub_basic_override",
        )
        active_override = TierOverride.objects.create(
            user=user,
            original_tier=self.tiers["basic"],
            override_tier=self.tiers["premium"],
            expires_at=datetime(2026, 7, 2, 12, 0, tzinfo=dt_timezone.utc),
        )

        self.assertEqual(
            self._states_for(user, active_override),
            {
                "free": {
                    "badge": "Included",
                    "note": "Included with every paid membership.",
                    "action_label": "Included",
                    "action_kind": "disabled",
                },
                "basic": {
                    "badge": "Current plan",
                    "note": (
                        "Base subscription. Temporary Premium access is active."
                    ),
                    "action_label": "Current plan",
                    "action_kind": "disabled",
                },
                "main": {
                    "badge": "Temporary access",
                    "note": "Included with your temporary Premium access.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
                "premium": {
                    "badge": "Temporary access",
                    "note": "Temporary access active until July 2, 2026.",
                    "action_label": "Manage Subscription",
                    "action_kind": "portal",
                },
            },
        )

    def test_format_period_end_remains_stable(self):
        date_value = timezone.make_aware(datetime(2026, 6, 5, 9, 30))

        self.assertEqual(format_period_end(None), "")
        self.assertEqual(format_period_end(date_value), "June 5, 2026")
