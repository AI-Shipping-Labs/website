"""Issue #970: webhook tier changes reconcile TierOverride + billing_period_end.

These tests fire Stripe payloads into the handlers and assert the resulting
``user.tier``, ``TierOverride.is_active``, ``billing_period_end``, effective
level (via ``content.access.get_user_level``), and community side effects
(mocked ``_community_remove``).

Rules under test (Option A — effective tier = max(base level, override level)):
- R1: a new non-free base tier retires the override matching that tier; a
  higher override survives.
- R2: ``subscription.deleted`` reverts base to free, computes the effective
  level from any surviving override, and only removes community access when
  the effective level is below Main. The override is NOT deactivated; the
  ``stripe:*`` tags still churn.
- R3: ``billing_period_end`` is cleared whenever no active paid subscription
  remains (deleted, or a free-resulting update with no ``current_period_end``).
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import TierOverride, User
from content.access import get_user_level
from payments.models import Tier
from payments.services import (
    handle_subscription_deleted,
    handle_subscription_updated,
)

from .test_webhooks import QuietSubscriptionLookupMixin, handle_checkout_completed


def _make_override(user, override_tier, *, expires_in_days=30, is_active=True):
    """Create a TierOverride. Negative ``expires_in_days`` makes it lapsed."""
    return TierOverride.objects.create(
        user=user,
        original_tier=user.tier,
        override_tier=override_tier,
        expires_at=timezone.now() + timedelta(days=expires_in_days),
        is_active=is_active,
    )


@tag("core")
class CheckoutOverrideReconcileTest(QuietSubscriptionLookupMixin, TestCase):
    """R1 — checkout retires an equal-tier override, keeps a higher one."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.basic = Tier.objects.get(slug="basic")
        cls.main = Tier.objects.get(slug="main")
        cls.premium = Tier.objects.get(slug="premium")

    def _checkout(self, user, tier_slug):
        return {
            "id": f"cs_{tier_slug}_{user.pk}",
            "customer": f"cus_{user.pk}",
            "customer_details": {"email": user.email},
            "subscription": f"sub_{user.pk}",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": tier_slug, "user_id": str(user.pk)},
        }

    def test_equal_tier_checkout_retires_override(self):
        """Paid checkout for `main` retires an active `main` override."""
        user = User.objects.create_user(email="equal@test.com")
        user.tier = self.free
        user.save(update_fields=["tier"])
        override = _make_override(user, self.main)

        handle_checkout_completed(self._checkout(user, "main"))

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.main)
        self.assertFalse(override.is_active)
        self.assertEqual(get_user_level(user), 20)

    def test_checkout_below_override_keeps_override(self):
        """Paid checkout for `basic` keeps an active `premium` override (Option A)."""
        user = User.objects.create_user(email="below@test.com")
        user.tier = self.free
        user.save(update_fields=["tier"])
        override = _make_override(user, self.premium)

        handle_checkout_completed(self._checkout(user, "basic"))

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.basic)
        self.assertTrue(override.is_active)
        self.assertEqual(get_user_level(user), 30)

    def test_checkout_consistency_invariant_end_to_end(self):
        """Premium checkout: override retired, billing date set, effective=30."""
        user = User.objects.create_user(email="invariant@test.com")
        user.tier = self.free
        user.save(update_fields=["tier"])
        override = _make_override(user, self.premium)

        billing_end = timezone.now() + timedelta(days=30)
        with patch(
            "payments.services._get_subscription_period_end",
            return_value=billing_end,
        ):
            handle_checkout_completed(self._checkout(user, "premium"))

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.premium)
        self.assertFalse(override.is_active)
        self.assertIsNotNone(user.billing_period_end)
        self.assertEqual(get_user_level(user), 30)


@tag("core")
class SubscriptionUpdatedOverrideReconcileTest(TestCase):
    """R1/R3 for customer.subscription.updated."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.basic = Tier.objects.get(slug="basic")
        cls.main = Tier.objects.get(slug="main")
        cls.main.stripe_price_id_monthly = "price_main_monthly"
        cls.main.save(update_fields=["stripe_price_id_monthly"])

    def test_update_to_higher_tier_retires_matching_override(self):
        """An active update from basic to main retires the `main` override."""
        user = User.objects.create_user(email="upgrade970@test.com")
        user.tier = self.basic
        user.subscription_id = "sub_upgrade970"
        user.stripe_customer_id = "cus_upgrade970"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])
        override = _make_override(user, self.main)

        subscription_data = {
            "id": "sub_upgrade970",
            "customer": "cus_upgrade970",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {"data": [{"price": {"id": "price_main_monthly"}}]},
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.main)
        self.assertFalse(override.is_active)

    def test_free_resulting_update_without_period_end_clears_billing(self):
        """A free-resulting update carrying no current_period_end clears
        a stale billing_period_end (R3)."""
        self.free.stripe_price_id_monthly = "price_free_monthly"
        self.free.save(update_fields=["stripe_price_id_monthly"])

        user = User.objects.create_user(email="stalebill@test.com")
        user.tier = self.main
        user.subscription_id = "sub_stalebill"
        user.stripe_customer_id = "cus_stalebill"
        user.billing_period_end = timezone.now() + timedelta(days=10)
        user.save(update_fields=[
            "tier", "subscription_id", "stripe_customer_id", "billing_period_end",
        ])

        subscription_data = {
            "id": "sub_stalebill",
            "customer": "cus_stalebill",
            "status": "active",
            "cancel_at_period_end": False,
            # no current_period_end
            "items": {"data": [{"price": {"id": "price_free_monthly"}}]},
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, self.free)
        self.assertIsNone(user.billing_period_end)

    def test_downgrade_keeps_community_when_main_override_survives(self):
        """Base tier downgrades below Main, but an active Main override keeps
        effective community access and suppresses immediate removal."""
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.save(update_fields=["stripe_price_id_monthly"])

        user = User.objects.create_user(email="downgrade-override@test.com")
        user.tier = self.main
        user.subscription_id = "sub_downgrade_override"
        user.stripe_customer_id = "cus_downgrade_override"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])
        override = _make_override(user, self.main)

        subscription_data = {
            "id": "sub_downgrade_override",
            "customer": "cus_downgrade_override",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1774396800,
            "items": {"data": [{"price": {"id": "price_basic_monthly"}}]},
        }

        with patch("payments.services._community_remove") as mock_remove:
            handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.basic)
        self.assertTrue(override.is_active)
        self.assertEqual(get_user_level(user), 20)
        mock_remove.assert_not_called()

    def test_downgrade_removes_community_without_surviving_main_override(self):
        """The effective-tier guard still removes access when no override
        keeps the user at Main or above after a base-tier downgrade."""
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.save(update_fields=["stripe_price_id_monthly"])

        user = User.objects.create_user(email="downgrade-no-override@test.com")
        user.tier = self.main
        user.subscription_id = "sub_downgrade_no_override"
        user.stripe_customer_id = "cus_downgrade_no_override"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])

        subscription_data = {
            "id": "sub_downgrade_no_override",
            "customer": "cus_downgrade_no_override",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1774396800,
            "items": {"data": [{"price": {"id": "price_basic_monthly"}}]},
        }

        with patch("payments.services._community_remove") as mock_remove:
            handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic)
        self.assertEqual(get_user_level(user), 10)
        mock_remove.assert_called_once()

    def test_reactivation_keeps_pending_tier_behaviour_no_disturbance(self):
        """Regression #968: cancel_at_period_end=False clears pending_tier on a
        same-tier no-change update; override-retirement does not disturb it."""
        free_tier = self.free
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.save(update_fields=["stripe_price_id_monthly"])

        user = User.objects.create_user(email="reactivate970@test.com")
        user.tier = self.main
        user.subscription_id = "sub_react970"
        user.stripe_customer_id = "cus_react970"
        user.pending_tier = free_tier
        user.save(update_fields=[
            "tier", "subscription_id", "stripe_customer_id", "pending_tier",
        ])
        # An override granting `main` would be retired if the update wrongly
        # treated a no-change update as a new grant. The price below resolves
        # to `main` (same as current tier) -> no tier change -> override stays.
        self.main.stripe_price_id_monthly = "price_main_monthly"
        self.main.save(update_fields=["stripe_price_id_monthly"])
        override = _make_override(user, self.main)

        subscription_data = {
            "id": "sub_react970",
            "customer": "cus_react970",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1774396800,
            "items": {"data": [{"price": {"id": "price_main_monthly"}}]},
        }

        handle_subscription_updated(subscription_data)

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertIsNone(user.pending_tier)
        self.assertEqual(user.tier, self.main)
        # No tier change occurred, so the same-tier override is undisturbed.
        self.assertTrue(override.is_active)


@tag("core")
class SubscriptionDeletedOverrideReconcileTest(TestCase):
    """R2/R3 for customer.subscription.deleted."""

    @classmethod
    def setUpTestData(cls):
        cls.free = Tier.objects.get(slug="free")
        cls.main = Tier.objects.get(slug="main")
        cls.premium = Tier.objects.get(slug="premium")

    def _deleted_payload(self, user):
        return {
            "id": user.subscription_id,
            "customer": user.stripe_customer_id,
        }

    def _make_paid_user(self, email, tier, sub="sub_x", cus="cus_x"):
        user = User.objects.create_user(email=email)
        user.tier = tier
        user.subscription_id = sub
        user.stripe_customer_id = cus
        user.billing_period_end = timezone.now() + timedelta(days=5)
        user.save(update_fields=[
            "tier", "subscription_id", "stripe_customer_id", "billing_period_end",
        ])
        return user

    def test_main_override_keeps_community_access(self):
        """Sub ends but an active `main` override keeps effective Main and
        community access; billing cleared; override stays active."""
        user = self._make_paid_user(
            "keepcomm@test.com", self.main, "sub_keep", "cus_keep",
        )
        override = _make_override(user, self.main)

        with patch("payments.services._community_remove") as mock_remove:
            handle_subscription_deleted(self._deleted_payload(user))

        user.refresh_from_db()
        override.refresh_from_db()
        self.assertEqual(user.tier, self.free)
        self.assertTrue(override.is_active)
        self.assertEqual(get_user_level(user), 20)
        self.assertIsNone(user.billing_period_end)
        mock_remove.assert_not_called()

    def test_no_override_removes_community_access(self):
        """Sub ends with no override: base -> free, billing cleared, community
        removal fires once (prior tier was Main+)."""
        user = self._make_paid_user(
            "nocomm@test.com", self.main, "sub_noov", "cus_noov",
        )

        with patch("payments.services._community_remove") as mock_remove:
            handle_subscription_deleted(self._deleted_payload(user))

        user.refresh_from_db()
        self.assertEqual(user.tier, self.free)
        self.assertIsNone(user.billing_period_end)
        mock_remove.assert_called_once()

    def test_expired_override_removes_community_access(self):
        """An already-lapsed override does not protect community access."""
        user = self._make_paid_user(
            "expired@test.com", self.main, "sub_exp", "cus_exp",
        )
        _make_override(user, self.main, expires_in_days=-1)

        with patch("payments.services._community_remove") as mock_remove:
            handle_subscription_deleted(self._deleted_payload(user))

        user.refresh_from_db()
        self.assertEqual(get_user_level(user), 0)
        mock_remove.assert_called_once()

    def test_churned_tags_reconciled_despite_surviving_override(self):
        """Regression #969 + R2: stripe:* tags churn even when the override
        survives; tags track Stripe, not the override."""
        user = self._make_paid_user(
            "churn@test.com", self.premium, "sub_churn", "cus_churn",
        )
        user.tags = ["stripe:active", "stripe:plan-premium"]
        user.save(update_fields=["tags"])
        override = _make_override(user, self.main)

        with patch("payments.services._community_remove"):
            handle_subscription_deleted(self._deleted_payload(user))

        user.refresh_from_db()
        override.refresh_from_db()
        tags = user.tags or []
        self.assertNotIn("stripe:active", tags)
        self.assertNotIn("stripe:plan-premium", tags)
        self.assertIn("stripe:churned", tags)
        self.assertTrue(override.is_active)
        self.assertEqual(get_user_level(user), 20)
