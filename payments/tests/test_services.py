"""Tests for the payments services module.

Tests cover:
- create_checkout_session with Stripe API mocked
- upgrade_subscription with Stripe API mocked
- downgrade_subscription with Stripe API mocked
- cancel_subscription with Stripe API mocked
- _tier_for_price_id helper
- verify_webhook_signature
- Stripe error handling (CardError, RateLimitError, network timeout)
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import stripe
from django.test import TestCase, override_settings, tag

from accounts.models import User
from payments.models import ConversionAttribution, Tier
from payments.services import (
    _get_subscription_period_end,
    _get_subscription_price_id,
    _tier_for_price_id,
    _tier_from_subscription,
    cancel_subscription,
    create_checkout_session,
    downgrade_subscription,
    handle_checkout_completed,
    upgrade_subscription,
)


class StripeMappingObject:
    """Minimal StripeObject-like mapping whose .items is the dict method."""

    def __init__(self, **values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]

    def items(self):
        return self._values.items()


@tag('core')
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


@tag('core')
class SubscriptionExtractionTest(TestCase):
    """Tests for resilient Stripe subscription shape parsing."""

    def setUp(self):
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.stripe_price_id_yearly = "price_basic_yearly"
        self.basic.save(update_fields=[
            "stripe_price_id_monthly", "stripe_price_id_yearly",
        ])

    @patch("payments.services._get_stripe_client")
    def test_period_end_from_top_level_subscription(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "current_period_end": 1774396800,
            "items": {"data": []},
        }
        mock_get_client.return_value = mock_client

        period_end = _get_subscription_period_end("sub_top_period")

        self.assertEqual(
            period_end,
            datetime.fromtimestamp(1774396800, tz=timezone.utc),
        )

    @patch("payments.services._get_stripe_client")
    def test_period_end_from_first_subscription_item(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {
                "data": [
                    {
                        "current_period_end": 1774396800,
                        "price": {"id": "price_basic_monthly"},
                    },
                ],
            },
        }
        mock_get_client.return_value = mock_client

        period_end = _get_subscription_period_end("sub_item_period")

        self.assertEqual(
            period_end,
            datetime.fromtimestamp(1774396800, tz=timezone.utc),
        )

    @patch("payments.services._get_stripe_client")
    def test_price_id_from_dict_like_subscription(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {
                "data": [
                    {"price": {"id": "price_basic_yearly"}},
                ],
            },
        }
        mock_get_client.return_value = mock_client

        self.assertEqual(
            _get_subscription_price_id("sub_dict_price"),
            "price_basic_yearly",
        )

    @patch("payments.services._get_stripe_client")
    def test_price_id_from_mapping_object_with_items_method_collision(
        self, mock_get_client,
    ):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = StripeMappingObject(
            items={
                "data": [
                    StripeMappingObject(
                        current_period_end=1774396800,
                        price=StripeMappingObject(id="price_basic_monthly"),
                    ),
                ],
            },
        )
        mock_get_client.return_value = mock_client

        with patch("payments.services.logger") as mock_logger:
            price_id = _get_subscription_price_id("sub_collision_price")

        self.assertEqual(price_id, "price_basic_monthly")
        mock_logger.exception.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_tier_from_subscription_uses_extracted_price_id(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {
                "data": [
                    {"price": {"id": "price_basic_yearly"}},
                ],
            },
        }
        mock_get_client.return_value = mock_client

        self.assertEqual(_tier_from_subscription("sub_tier_price"), self.basic)

    @patch("payments.services._get_stripe_client")
    def test_incomplete_subscription_shape_fails_soft(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {"items": {"data": []}}
        mock_get_client.return_value = mock_client

        with patch("payments.services.logger") as mock_logger:
            price_id = _get_subscription_price_id("sub_incomplete")
            period_end = _get_subscription_period_end("sub_incomplete")

        self.assertEqual(price_id, "")
        self.assertIsNone(period_end)
        mock_logger.exception.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_checkout_resolves_tier_and_yearly_attribution_from_subscription_price(
        self, mock_get_client,
    ):
        user = User.objects.create_user(email="subprice@test.com")
        subscription = {
            "items": {
                "data": [
                    {
                        "current_period_end": 1774396800,
                        "price": {"id": "price_basic_yearly"},
                    },
                ],
            },
        }
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = subscription
        mock_get_client.return_value = mock_client
        session_data = {
            "id": "cs_subprice",
            "customer": "cus_subprice",
            "customer_details": {"email": "subprice@test.com"},
            "subscription": "sub_subprice",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "missing", "user_id": str(user.pk)},
        }

        handle_checkout_completed(session_data)

        user.refresh_from_db()
        self.assertEqual(user.tier, self.basic)
        self.assertEqual(
            user.billing_period_end,
            datetime.fromtimestamp(1774396800, tz=timezone.utc),
        )
        attribution = ConversionAttribution.objects.get(
            stripe_session_id="cs_subprice",
        )
        self.assertEqual(attribution.billing_period, "yearly")
        self.assertEqual(attribution.amount_eur, self.basic.price_eur_year)
        self.assertEqual(attribution.mrr_eur, self.basic.price_eur_year // 12)


@tag('core')
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
        Tier.objects.get(slug="free")
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

        create_checkout_session(
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


@tag('core')
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
        mock_sub = {"items": {"data": [{"id": "si_item_1"}]}}
        mock_client.subscriptions.retrieve.return_value = mock_sub
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        upgrade_subscription(self.user, "main", "monthly")

        mock_client.subscriptions.update.assert_called_once()
        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["proration_behavior"], "create_prorations")
        self.assertEqual(call_params["items"][0]["price"], "price_main_monthly")
        self.assertEqual(call_params["items"][0]["id"], "si_item_1")

    @patch("payments.services._get_stripe_client")
    def test_updates_dict_like_subscription_item(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {
            "items": {"data": [{"id": "si_dict_item"}]},
        }
        mock_get_client.return_value = mock_client

        upgrade_subscription(self.user, "main", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(
            call_params["items"],
            [{"id": "si_dict_item", "price": "price_main_monthly"}],
        )

    @patch("payments.services._get_stripe_client")
    def test_updates_mapping_object_with_items_method_collision(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = StripeMappingObject(
            items={"data": [StripeMappingObject(id="si_collision_item")]},
        )
        mock_get_client.return_value = mock_client

        upgrade_subscription(self.user, "main", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["items"][0]["id"], "si_collision_item")

    @patch("payments.services._get_stripe_client")
    def test_updates_object_like_subscription_item(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = SimpleNamespace(
            items=SimpleNamespace(data=[SimpleNamespace(id="si_object_item")]),
        )
        mock_get_client.return_value = mock_client

        upgrade_subscription(self.user, "main", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["items"][0]["id"], "si_object_item")

    @patch("payments.services._get_stripe_client")
    def test_raises_for_empty_subscription_items(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {"items": {"data": []}}
        mock_get_client.return_value = mock_client

        with self.assertRaisesMessage(
            ValueError, "Subscription has no subscription item to update."
        ):
            upgrade_subscription(self.user, "main", "monthly")

        mock_client.subscriptions.update.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_raises_for_subscription_item_missing_id(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {"items": {"data": [{}]}}
        mock_get_client.return_value = mock_client

        with self.assertRaisesMessage(
            ValueError, "Subscription has no subscription item to update."
        ):
            upgrade_subscription(self.user, "main", "monthly")

        mock_client.subscriptions.update.assert_not_called()


@tag('core')
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
        mock_sub = {"items": {"data": [{"id": "si_item_1"}]}}
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
        mock_sub = {"items": {"data": [{"id": "si_item_1"}]}}
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
        mock_sub = {"items": {"data": [{"id": "si_item_1"}]}}
        mock_client.subscriptions.retrieve.return_value = mock_sub
        mock_client.subscriptions.update.return_value = mock_sub
        mock_get_client.return_value = mock_client

        downgrade_subscription(self.user, "basic", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["proration_behavior"], "none")
        self.assertEqual(call_params["billing_cycle_anchor"], "unchanged")
        self.assertEqual(
            call_params["items"],
            [{"id": "si_item_1", "price": "price_basic_monthly"}],
        )

    @patch("payments.services._get_stripe_client")
    def test_updates_mapping_object_with_items_method_collision(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = StripeMappingObject(
            items={"data": [StripeMappingObject(id="si_collision_item")]},
        )
        mock_get_client.return_value = mock_client

        downgrade_subscription(self.user, "basic", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(
            call_params["items"],
            [{"id": "si_collision_item", "price": "price_basic_monthly"}],
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_tier, self.basic)
        self.assertEqual(self.user.tier, self.main)

    @patch("payments.services._get_stripe_client")
    def test_updates_object_like_subscription_item(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = SimpleNamespace(
            items=SimpleNamespace(data=[SimpleNamespace(id="si_object_item")]),
        )
        mock_get_client.return_value = mock_client

        downgrade_subscription(self.user, "basic", "monthly")

        call_params = mock_client.subscriptions.update.call_args[1]["params"]
        self.assertEqual(call_params["items"][0]["id"], "si_object_item")

    @patch("payments.services._get_stripe_client")
    def test_raises_for_empty_subscription_items(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {"items": {"data": []}}
        mock_get_client.return_value = mock_client

        with self.assertRaisesMessage(
            ValueError, "Subscription has no subscription item to update."
        ):
            downgrade_subscription(self.user, "basic", "monthly")

        mock_client.subscriptions.update.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_raises_for_subscription_item_missing_id(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.return_value = {"items": {"data": [{}]}}
        mock_get_client.return_value = mock_client

        with self.assertRaisesMessage(
            ValueError, "Subscription has no subscription item to update."
        ):
            downgrade_subscription(self.user, "basic", "monthly")

        mock_client.subscriptions.update.assert_not_called()


@tag('core')
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


# ── Stripe Error Handling (view-level) ────────────────────────────────


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CheckoutStripeErrorViewTest(TestCase):
    """Test that Stripe errors during checkout return user-friendly errors, not 500s."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="stripe-err@test.com", password="testpass123"
        )
        self.basic = Tier.objects.get(slug="basic")
        self.basic.stripe_price_id_monthly = "price_basic_monthly"
        self.basic.save()
        self.client.login(email="stripe-err@test.com", password="testpass123")

    def _post_checkout(self, data=None):
        if data is None:
            data = {"tier_slug": "basic", "billing_period": "monthly"}
        return self.client.post(
            "/api/checkout/create",
            data=json.dumps(data),
            content_type="application/json",
        )

    @patch("payments.services._get_stripe_client")
    def test_card_error_during_checkout_returns_500(self, mock_get_client):
        """stripe.error.CardError during checkout returns 500 with friendly message."""
        mock_client = MagicMock()
        mock_client.checkout.sessions.create.side_effect = stripe.CardError(
            message="Your card was declined.",
            param="card",
            code="card_declined",
        )
        mock_get_client.return_value = mock_client

        with self.assertLogs("payments.views.checkout", level="ERROR") as logs:
            response = self._post_checkout()
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to create checkout session", logs.output[0])
        data = response.json()
        self.assertIn("error", data)
        # Should be a user-friendly message, not a traceback
        self.assertNotIn("Traceback", data["error"])

    @patch("payments.services._get_stripe_client")
    def test_rate_limit_error_during_checkout_returns_500(self, mock_get_client):
        """stripe.error.RateLimitError during checkout returns 500 with friendly message."""
        mock_client = MagicMock()
        mock_client.checkout.sessions.create.side_effect = stripe.RateLimitError(
            message="Too many requests",
        )
        mock_get_client.return_value = mock_client

        with self.assertLogs("payments.views.checkout", level="ERROR") as logs:
            response = self._post_checkout()
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to create checkout session", logs.output[0])
        data = response.json()
        self.assertIn("error", data)
        self.assertNotIn("Traceback", data["error"])


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class UpgradeStripeErrorViewTest(TestCase):
    """Test that Stripe errors during upgrade return user-friendly errors."""

    def setUp(self):
        self.main = Tier.objects.get(slug="main")
        self.main.stripe_price_id_monthly = "price_main_monthly"
        self.main.save()

        self.user = User.objects.create_user(
            email="upgrade-err@test.com", password="testpass123"
        )
        self.user.subscription_id = "sub_upgrade_err"
        self.user.save(update_fields=["subscription_id"])
        self.client.login(email="upgrade-err@test.com", password="testpass123")

    @patch("payments.services._get_stripe_client")
    def test_rate_limit_error_during_upgrade_returns_500(self, mock_get_client):
        """stripe.error.RateLimitError during upgrade returns 500, not a traceback."""
        mock_client = MagicMock()
        mock_client.subscriptions.retrieve.side_effect = stripe.RateLimitError(
            message="Too many requests",
        )
        mock_get_client.return_value = mock_client

        with self.assertLogs("payments.views.checkout", level="ERROR") as logs:
            response = self.client.post(
                "/api/subscription/upgrade",
                data=json.dumps({"tier_slug": "main", "billing_period": "monthly"}),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to upgrade subscription", logs.output[0])
        data = response.json()
        self.assertIn("error", data)
        self.assertNotIn("Traceback", data["error"])


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelStripeErrorViewTest(TestCase):
    """Test that Stripe errors during cancel return user-friendly errors."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="cancel-err@test.com", password="testpass123"
        )
        self.user.subscription_id = "sub_cancel_err"
        self.user.save(update_fields=["subscription_id"])
        self.client.login(email="cancel-err@test.com", password="testpass123")

    @patch("payments.services._get_stripe_client")
    def test_network_timeout_during_cancel_returns_500(self, mock_get_client):
        """Network timeout during cancel returns 500 with friendly message."""
        mock_client = MagicMock()
        mock_client.subscriptions.update.side_effect = stripe.APIConnectionError(
            message="Network error: Connection timed out",
        )
        mock_get_client.return_value = mock_client

        with self.assertLogs("payments.views.checkout", level="ERROR") as logs:
            response = self.client.post(
                "/api/subscription/cancel",
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to cancel subscription", logs.output[0])
        data = response.json()
        self.assertIn("error", data)
        self.assertNotIn("Traceback", data["error"])

    @patch("payments.services._get_stripe_client")
    def test_stripe_error_during_cancel_returns_500(self, mock_get_client):
        """Generic Stripe API error during cancel returns 500 with friendly message."""
        mock_client = MagicMock()
        mock_client.subscriptions.update.side_effect = stripe.APIError(
            message="Internal Stripe error",
        )
        mock_get_client.return_value = mock_client

        with self.assertLogs("payments.views.checkout", level="ERROR") as logs:
            response = self.client.post(
                "/api/subscription/cancel",
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertIn("Failed to cancel subscription", logs.output[0])
        data = response.json()
        self.assertIn("error", data)
        self.assertEqual(data["error"], "Failed to cancel subscription")
