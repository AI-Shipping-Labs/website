"""Tests for the payments services module.

Tests cover:
- _tier_for_price_id helper
- verify_webhook_signature
- webhook fulfillment helpers
- hard-deprecated local checkout/subscription mutation helpers
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, tag

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
class DeprecatedLocalStripeMutationServicesTest(TestCase):
    """Obsolete local Stripe mutation helpers fail before calling Stripe."""

    def setUp(self):
        self.user = User.objects.create_user(email="deprecated-services@test.com")

    @patch("payments.services._get_stripe_client")
    def test_create_checkout_session_is_hard_deprecated(self, mock_get_client):
        with self.assertRaisesMessage(RuntimeError, "Payment Links"):
            create_checkout_session(
                self.user,
                "basic",
                "monthly",
                "https://example.test/success",
                "https://example.test/cancel",
            )
        mock_get_client.assert_not_called()

    @patch("payments.services._get_stripe_client")
    def test_direct_subscription_mutations_are_hard_deprecated(
        self, mock_get_client,
    ):
        deprecated_calls = [
            lambda: upgrade_subscription(self.user, "main", "monthly"),
            lambda: downgrade_subscription(self.user, "basic", "monthly"),
            lambda: cancel_subscription(self.user),
        ]

        for call in deprecated_calls:
            with self.subTest(call=call):
                with self.assertRaisesMessage(RuntimeError, "Customer Portal"):
                    call()

        mock_get_client.assert_not_called()
