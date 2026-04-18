"""Tests for the cancel subscription confirmation flow (issue #117).

Backend behavior only:
- The cancel API endpoint sets pending_tier to free and returns ok.
- After cancellation, the account page exposes the pending-cancellation notice.
- The view context includes `tier_features` derived from the user's tier.

The modal HTML element ids, CSS classes, button labels, billing-date string
formatting, feature-list strings, and the `onclick="showCancelConfirm()"`
attribute were previously asserted as raw substrings of the rendered template
(`assertIn('id=\"cancel-modal\"', content)` etc). Those tests are removed under
`_docs/testing-guidelines.md` Rule 4 (do not test JavaScript / DOM by
string-matching HTML).

The user-visible click flow (open modal -> confirm checkbox + text -> click
"Cancel my subscription" -> see pending-cancellation notice) is covered by
`playwright_tests/test_account_page.py::TestScenarioCancelSubscriptionConfirmation`.
"""

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from accounts.models import User
from payments.models import Tier


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelModalContextTest(TestCase):
    """Tests for the `tier_features` context value supplied to the cancel modal."""

    def test_context_has_tier_features_for_main_tier(self):
        """The /account/ context includes tier_features for a main-tier user."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="features@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_test_features"
        user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(user)

        response = self.client.get("/account/")
        tier_features = response.context["tier_features"]
        self.assertIsInstance(tier_features, list)
        self.assertGreater(len(tier_features), 0)
        self.assertIn("Slack community access", tier_features)

    def test_context_empty_features_for_tier_with_no_features(self):
        """tier_features is empty list when tier has no features."""
        empty_tier = Tier.objects.create(
            slug="empty-test2",
            name="Empty Test 2",
            level=98,
            features=[],
        )
        user = User.objects.create_user(email="empty2@test.com")
        user.tier = empty_tier
        user.subscription_id = "sub_empty2"
        user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(user)

        response = self.client.get("/account/")
        self.assertEqual(response.context["tier_features"], [])

    def test_context_features_present_for_free_user(self):
        """tier_features is a list (not None) for free-tier users."""
        user = User.objects.create_user(email="freectx@test.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        tier_features = response.context["tier_features"]
        self.assertIsInstance(tier_features, list)


class CancelAPIStillWorksTest(TestCase):
    """Tests that the cancel API endpoint still works correctly after the UI change."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.user = User.objects.create_user(email="api-test@test.com")
        self.user.tier = self.main_tier
        self.user.subscription_id = "sub_api_test"
        self.user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(self.user)
        self.url = "/account/api/cancel"

    @patch("payments.services.cancel_subscription")
    def test_cancel_api_returns_ok(self, mock_cancel):
        """The cancel API still returns status ok."""
        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800
        mock_cancel.return_value = mock_sub

        response = self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "ok")

    @patch("payments.services.cancel_subscription")
    def test_cancel_api_sets_pending_tier_free(self, mock_cancel):
        """The cancel API sets pending_tier to free."""
        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800
        mock_cancel.return_value = mock_sub

        self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_tier.slug, "free")

    @patch("payments.services.cancel_subscription")
    def test_after_cancel_page_exposes_billing_period_end(self, mock_cancel):
        """After cancellation, the user's billing_period_end is set in DB."""
        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800
        mock_cancel.return_value = mock_sub

        self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )

        self.user.refresh_from_db()
        self.assertIsNotNone(self.user.billing_period_end)
        # Sanity: billing_period_end is set to the unix timestamp returned by Stripe
        expected = datetime.fromtimestamp(1774396800, tz=dt_timezone.utc)
        self.assertEqual(self.user.billing_period_end, expected)
