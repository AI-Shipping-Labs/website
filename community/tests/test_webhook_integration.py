"""Tests for community hooks in payment webhook handlers.

Verifies that community invite/remove/reactivate are triggered
correctly from payment service functions when tier changes occur.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase

from accounts.models import User
from payments.models import Tier
from payments.services import (
    handle_checkout_completed,
    handle_subscription_deleted,
    handle_subscription_updated,
)


class CheckoutCompletedCommunityTest(TestCase):
    """Test that checkout.session.completed triggers community invite."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.main_tier.stripe_price_id_monthly = "price_main_monthly"
        self.main_tier.save()

        self.basic_tier = Tier.objects.get(slug="basic")
        self.basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        self.basic_tier.save()

        self.user = User.objects.create_user(email="checkout_community@test.com")

    @patch("payments.services._community_invite")
    @patch("payments.services._get_subscription_period_end", return_value=None)
    def test_main_tier_triggers_invite(self, mock_period, mock_invite):
        """Purchasing Main tier triggers community invite."""
        session_data = {
            "id": "cs_test_1",
            "client_reference_id": str(self.user.pk),
            "customer": "cus_1",
            "subscription": "sub_1",
            "customer_details": {"email": self.user.email},
            "metadata": {"tier_slug": "main"},
        }

        handle_checkout_completed(session_data)

        mock_invite.assert_called_once()
        called_user = mock_invite.call_args[0][0]
        self.assertEqual(called_user.pk, self.user.pk)

    @patch("payments.services._community_invite")
    @patch("payments.services._get_subscription_period_end", return_value=None)
    def test_basic_tier_does_not_trigger_invite(self, mock_period, mock_invite):
        """Purchasing Basic tier does NOT trigger community invite."""
        session_data = {
            "id": "cs_test_2",
            "client_reference_id": str(self.user.pk),
            "customer": "cus_2",
            "subscription": "sub_2",
            "customer_details": {"email": self.user.email},
            "metadata": {"tier_slug": "basic"},
        }

        handle_checkout_completed(session_data)

        mock_invite.assert_not_called()


class SubscriptionUpdatedCommunityTest(TestCase):
    """Test that subscription.updated triggers community reactivate/removal."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.main_tier.stripe_price_id_monthly = "price_main_monthly"
        self.main_tier.save()

        self.basic_tier = Tier.objects.get(slug="basic")
        self.basic_tier.stripe_price_id_monthly = "price_basic_monthly"
        self.basic_tier.save()

        self.free_tier = Tier.objects.get(slug="free")

    @patch("payments.services._community_reactivate")
    def test_upgrade_to_main_triggers_reactivate(self, mock_reactivate):
        """Upgrading from Basic to Main triggers community reactivate."""
        user = User.objects.create_user(email="upgrade_comm@test.com")
        user.tier = self.basic_tier
        user.subscription_id = "sub_upgrade"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_upgrade",
            "customer": "cus_upgrade",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {
                "data": [{"price": {"id": "price_main_monthly"}}]
            },
        }

        handle_subscription_updated(subscription_data)

        mock_reactivate.assert_called_once()

    @patch("payments.services._community_remove")
    def test_downgrade_below_main_triggers_remove(self, mock_remove):
        """Downgrading from Main to Basic triggers community remove."""
        user = User.objects.create_user(email="downgrade_comm@test.com")
        user.tier = self.main_tier
        user.subscription_id = "sub_downgrade"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_downgrade",
            "customer": "cus_downgrade",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {
                "data": [{"price": {"id": "price_basic_monthly"}}]
            },
        }

        handle_subscription_updated(subscription_data)

        mock_remove.assert_called_once()

    @patch("payments.services._community_schedule_removal")
    def test_cancel_at_period_end_schedules_removal(self, mock_schedule):
        """cancel_at_period_end with community tier schedules removal."""
        user = User.objects.create_user(email="cancel_comm@test.com")
        user.tier = self.main_tier
        user.subscription_id = "sub_cancel"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_cancel",
            "customer": "cus_cancel",
            "status": "active",
            "cancel_at_period_end": True,
            "current_period_end": 1700000000,
            "items": {
                "data": [{"price": {"id": "price_main_monthly"}}]
            },
        }

        handle_subscription_updated(subscription_data)

        mock_schedule.assert_called_once()


class SubscriptionDeletedCommunityTest(TestCase):
    """Test that subscription.deleted triggers community removal."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")

    @patch("payments.services._community_remove")
    def test_deleted_with_community_tier_triggers_remove(self, mock_remove):
        """Deleting subscription for Main-tier user triggers removal."""
        user = User.objects.create_user(email="deleted_comm@test.com")
        user.tier = self.main_tier
        user.subscription_id = "sub_deleted"
        user.slack_user_id = "U123"
        user.save(update_fields=["tier", "subscription_id", "slack_user_id"])

        subscription_data = {
            "id": "sub_deleted",
            "customer": "cus_deleted",
        }

        handle_subscription_deleted(subscription_data)

        mock_remove.assert_called_once()

    @patch("payments.services._community_remove")
    def test_deleted_without_community_tier_does_not_remove(self, mock_remove):
        """Deleting subscription for Basic-tier user does NOT trigger removal."""
        basic_tier = Tier.objects.get(slug="basic")
        user = User.objects.create_user(email="deleted_basic@test.com")
        user.tier = basic_tier
        user.subscription_id = "sub_deleted_basic"
        user.save(update_fields=["tier", "subscription_id"])

        subscription_data = {
            "id": "sub_deleted_basic",
            "customer": "cus_deleted_basic",
        }

        handle_subscription_deleted(subscription_data)

        mock_remove.assert_not_called()
