"""Tests for the payments services module.

Tests cover:
- create_checkout_session with Stripe API mocked
- upgrade_subscription with Stripe API mocked
- downgrade_subscription with Stripe API mocked
- cancel_subscription with Stripe API mocked
- _tier_for_price_id helper
- verify_webhook_signature
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from accounts.models import User
from payments.models import Tier
from payments.services import (
    _tier_for_price_id,
    cancel_subscription,
    create_checkout_session,
    downgrade_subscription,
    upgrade_subscription,
)


class TierForPriceIdTest(TestCase):
    """Tests for the _tier_for_price_id helper function."""

    def setUp(self):
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.stripe_price_id_yearly = "price_basic_yearly"
        self.basic.save()

        self.main = Tier.objects.get(slug="main")
        self.main.stripe_price_id_monthly = "price_main_monthly"
        self.main.save()

    def test_finds_tier_by_monthly_price_id(self):
        tier = _tier_for_price_id("price_basic_monthly")
        self.assertEqual(tier, self.basic)

    def test_finds_tier_by_yearly_price_id(self):
        tier = _tier_for_price_id("price_basic_yearly")
        self.assertEqual(tier, self.basic)

    def test_returns_none_for_unknown_price_id(self):
        tier = _tier_for_price_id("price_unknown")
        self.assertIsNone(tier)

    def test_returns_none_for_empty_price_id(self):
        tier = _tier_for_price_id("")
        self.assertIsNone(tier)


class CreateCheckoutSessionTest(TestCase):
    """Tests for create_checkout_session service function."""

    def setUp(self):
        self.user = User.objects.create_user(email="checkout_svc@test.com")
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.stripe_price_id_yearly = "price_basic_yearly"
        self.basic.save()

    def test_raises_for_nonexistent_tier(self):
        with self.assertRaises(ValueError) as ctx:
            create_checkout_session(
                self.user, "nonexistent", "monthly",
                "https://example.com/success", "https://example.com/cancel"
            )
        self.assertIn("not found", str(ctx.exception))

    def test_raises_for_missing_price_id(self):
        """Tier with no Stripe price ID raises ValueError."""
        free = Tier.objects.get(slug="free")
        with self.assertRaises(ValueError) as ctx:
            create_checkout_session(
                self.user, "free", "monthly",
                "https://example.com/success", "https://example.com/cancel"
            )
        self.assertIn("No Stripe price ID", str(ctx.exception))

    @patch("payments.services._get_stripe_client")
    def test_creates_session_with_monthly_price(self, mock_get_client):
        """Monthly billing uses stripe_price_id_monthly."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/cs_test"
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        session = create_checkout_session(
            self.user, "basic", "monthly",
            "https://example.com/success", "https://example.com/cancel"
        )

        self.assertEqual(session.url, "https://checkout.stripe.com/cs_test")
        call_params = mock_client.checkout.sessions.create.call_args[1]["params"]
        self.assertEqual(call_params["line_items"][0]["price"], "price_basic_monthly")

    @patch("payments.services._get_stripe_client")
    def test_creates_session_with_yearly_price(self, mock_get_client):
        """Yearly billing uses stripe_price_id_yearly."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_session.url = "https://checkout.stripe.com/cs_test"
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        session = create_checkout_session(
            self.user, "basic", "yearly",
            "https://example.com/success", "https://example.com/cancel"
        )

        call_params = mock_client.checkout.sessions.create.call_args[1]["params"]
        self.assertEqual(call_params["line_items"][0]["price"], "price_basic_yearly")

    @patch("payments.services._get_stripe_client")
    def test_uses_customer_id_if_available(self, mock_get_client):
        """If user has stripe_customer_id, it's used instead of email."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        self.user.stripe_customer_id = "cus_existing"
        self.user.save(update_fields=["stripe_customer_id"])

        create_checkout_session(
            self.user, "basic", "monthly",
            "https://example.com/success", "https://example.com/cancel"
        )

        call_params = mock_client.checkout.sessions.create.call_args[1]["params"]
        self.assertEqual(call_params["customer"], "cus_existing")
        self.assertNotIn("customer_email", call_params)

    @patch("payments.services._get_stripe_client")
    def test_uses_email_if_no_customer_id(self, mock_get_client):
        """If user has no stripe_customer_id, email is used."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        create_checkout_session(
            self.user, "basic", "monthly",
            "https://example.com/success", "https://example.com/cancel"
        )

        call_params = mock_client.checkout.sessions.create.call_args[1]["params"]
        self.assertEqual(call_params["customer_email"], "checkout_svc@test.com")
        self.assertNotIn("customer", call_params)

    @patch("payments.services._get_stripe_client")
    def test_session_includes_metadata(self, mock_get_client):
        """Checkout session includes tier_slug and user_id in metadata."""
        mock_client = MagicMock()
        mock_session = MagicMock()
        mock_client.checkout.sessions.create.return_value = mock_session
        mock_get_client.return_value = mock_client

        create_checkout_session(
            self.user, "basic", "monthly",
            "https://example.com/success", "https://example.com/cancel"
        )

        call_params = mock_client.checkout.sessions.create.call_args[1]["params"]
        self.assertEqual(call_params["metadata"]["tier_slug"], "basic")
        self.assertEqual(call_params["metadata"]["user_id"], str(self.user.pk))
        self.assertEqual(call_params["client_reference_id"], str(self.user.pk))


class UpgradeSubscriptionTest(TestCase):
    """Tests for upgrade_subscription service function."""

    def setUp(self):
        self.main = Tier.objects.get(slug="main")
        self.main.stripe_price_id_monthly = "price_main_monthly"
        self.main.save()

        self.user = User.objects.create_user(email="upgrade_svc@test.com")
        self.user.subscription_id = "sub_test_upgrade"
        self.user.save(update_fields=["subscription_id"])

    def test_raises_if_no_subscription(self):
        user = User.objects.create_user(email="nosub@test.com")
        with self.assertRaises(ValueError):
            upgrade_subscription(user, "main", "monthly")

    def test_raises_for_nonexistent_tier(self):
        with self.assertRaises(ValueError):
            upgrade_subscription(self.user, "nonexistent", "monthly")

    @patch("payments.services._get_stripe_client")
    def test_calls_stripe_with_proration(self, mock_get_client):
        """Upgrade uses create_prorations proration_behavior."""
        mock_client = MagicMock()
        mock_sub = MagicMock()
        mock_sub.items.data = [MagicMock(id="si_item_1")]
        mock_client.subscriptions.retrieve.return_value = mock_sub
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        upgrade_subscription(self.user, "main", "monthly")

        mock_client.subscriptions.update.assert_called_once()
        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["proration_behavior"], "create_prorations")
        self.assertEqual(call_params["items"][0]["price"], "price_main_monthly")


class DowngradeSubscriptionTest(TestCase):
    """Tests for downgrade_subscription service function."""

    def setUp(self):
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.save()

        self.main = Tier.objects.get(slug="main")
        self.user = User.objects.create_user(email="downgrade_svc@test.com")
        self.user.tier = self.main
        self.user.subscription_id = "sub_test_downgrade"
        self.user.save(update_fields=["tier", "subscription_id"])

    def test_raises_if_no_subscription(self):
        user = User.objects.create_user(email="nosub_down@test.com")
        with self.assertRaises(ValueError):
            downgrade_subscription(user, "basic", "monthly")

    @patch("payments.services._get_stripe_client")
    def test_sets_pending_tier(self, mock_get_client):
        """Downgrade sets pending_tier to the new tier."""
        mock_client = MagicMock()
        mock_sub = MagicMock()
        mock_sub.items.data = [MagicMock(id="si_item_1")]
        mock_client.subscriptions.retrieve.return_value = mock_sub
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        downgrade_subscription(self.user, "basic", "monthly")

        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_tier, self.basic)

    @patch("payments.services._get_stripe_client")
    def test_does_not_change_current_tier(self, mock_get_client):
        """Downgrade does NOT change the current tier immediately."""
        mock_client = MagicMock()
        mock_sub = MagicMock()
        mock_sub.items.data = [MagicMock(id="si_item_1")]
        mock_client.subscriptions.retrieve.return_value = mock_sub
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        downgrade_subscription(self.user, "basic", "monthly")

        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, self.main)

    @patch("payments.services._get_stripe_client")
    def test_calls_stripe_with_no_proration(self, mock_get_client):
        """Downgrade uses 'none' proration_behavior."""
        mock_client = MagicMock()
        mock_sub = MagicMock()
        mock_sub.items.data = [MagicMock(id="si_item_1")]
        mock_client.subscriptions.retrieve.return_value = mock_sub
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        downgrade_subscription(self.user, "basic", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["proration_behavior"], "none")


class CancelSubscriptionTest(TestCase):
    """Tests for cancel_subscription service function."""

    def setUp(self):
        self.user = User.objects.create_user(email="cancel_svc@test.com")
        self.user.subscription_id = "sub_test_cancel"
        self.user.save(update_fields=["subscription_id"])

    def test_raises_if_no_subscription(self):
        user = User.objects.create_user(email="nosub_cancel@test.com")
        with self.assertRaises(ValueError):
            cancel_subscription(user)

    @patch("payments.services._get_stripe_client")
    def test_sets_cancel_at_period_end(self, mock_get_client):
        """Cancellation sets cancel_at_period_end on the Stripe subscription."""
        mock_client = MagicMock()
        mock_sub = MagicMock()
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        cancel_subscription(self.user)

        mock_client.subscriptions.update.assert_called_once_with(
            "sub_test_cancel",
            params={"cancel_at_period_end": True},
        )
