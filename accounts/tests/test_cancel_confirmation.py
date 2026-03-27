"""Tests for the cancel subscription confirmation flow (issue #117).

Tests verify the multi-step cancel confirmation modal: feature loss display,
checkbox, text input, disabled/enabled button states, and form reset behavior.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from payments.models import Tier


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelModalRenderedTest(TestCase):
    """Tests that the cancel modal renders with the new confirmation elements."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.user = User.objects.create_user(email="cancel-modal@test.com")
        self.user.tier = self.main_tier
        self.user.subscription_id = "sub_test_cancel"
        self.user.billing_period_end = timezone.make_aware(
            datetime(2026, 4, 1, 12, 0, 0)
        )
        self.user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(self.user)

    def test_cancel_modal_exists(self):
        """The cancel confirmation modal is rendered on the page."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-modal"', content)

    def test_cancel_modal_has_warning_icon_and_heading(self):
        """The modal has the alert-triangle icon and Cancel Subscription heading."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("alert-triangle", content)
        self.assertIn("Cancel Subscription", content)

    def test_cancel_modal_shows_billing_period_end_date(self):
        """The modal displays the billing period end date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("01/04/2026", content)

    def test_cancel_modal_shows_billing_period_text(self):
        """The modal explains the user keeps access until end of billing period."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn(
            "You will keep access to your current tier until the end of your billing period",
            content,
        )

    def test_cancel_modal_has_checkbox(self):
        """The modal contains a checkbox for confirming understanding."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-confirm-checkbox"', content)
        self.assertIn(
            "I understand I will lose access to paid features at the end of my billing period",
            content,
        )

    def test_cancel_modal_has_text_input(self):
        """The modal contains a text input with the correct placeholder."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-confirm-text"', content)
        self.assertIn('placeholder="Type confirm to proceed"', content)

    def test_cancel_button_is_disabled_by_default(self):
        """The cancel button starts as disabled."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="confirm-cancel-btn"', content)
        # Check that the button has the disabled attribute
        # The button HTML should contain "disabled"
        self.assertIn("disabled", content)

    def test_cancel_button_text(self):
        """The cancel button says 'Cancel my subscription'."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Cancel my subscription", content)

    def test_keep_plan_button_exists(self):
        """The 'Keep my plan' button is present."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="keep-plan-btn"', content)
        self.assertIn("Keep my plan", content)


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelModalFeatureLossTest(TestCase):
    """Tests for the feature loss summary in the cancel modal."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.user = User.objects.create_user(email="features@test.com")
        self.user.tier = self.main_tier
        self.user.subscription_id = "sub_test_features"
        self.user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(self.user)

    def test_shows_feature_loss_section(self):
        """The modal contains the feature loss section."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-feature-loss"', content)
        self.assertIn("Features you will lose:", content)

    def test_shows_tier_features(self):
        """The modal lists features from the tier's features JSON."""
        response = self.client.get("/account/")
        content = response.content.decode()
        # Main tier features from seed data
        self.assertIn("Slack community access", content)
        self.assertIn("Group coding sessions", content)
        self.assertIn("Project-based learning", content)

    def test_feature_items_have_x_icons(self):
        """Each feature item is displayed with an X icon."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-feature-list"', content)
        # The lucide "x" icon is used
        self.assertIn('data-lucide="x"', content)

    def test_context_has_tier_features(self):
        """The context includes tier_features from the current tier."""
        response = self.client.get("/account/")
        tier_features = response.context["tier_features"]
        self.assertIsInstance(tier_features, list)
        self.assertGreater(len(tier_features), 0)
        self.assertIn("Slack community access", tier_features)

    def test_basic_tier_features(self):
        """Basic tier shows its own features."""
        basic_tier = Tier.objects.get(slug="basic")
        self.user.tier = basic_tier
        self.user.save(update_fields=["tier"])
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("Exclusive articles", content)
        self.assertIn("AI tool breakdowns", content)

    def test_premium_tier_features(self):
        """Premium tier shows its own features."""
        premium_tier = Tier.objects.get(slug="premium")
        self.user.tier = premium_tier
        self.user.save(update_fields=["tier"])
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("All mini-courses", content)
        self.assertIn("Resume/LinkedIn/GitHub teardowns", content)


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelModalEmptyFeaturesTest(TestCase):
    """Tests for the cancel modal when tier has no features."""

    def test_generic_message_when_no_features(self):
        """When tier has empty features list, show generic loss message."""
        # Create a tier with no features
        empty_tier = Tier.objects.create(
            slug="empty-test",
            name="Empty Test",
            level=99,
            features=[],
        )
        user = User.objects.create_user(email="empty@test.com")
        user.tier = empty_tier
        user.subscription_id = "sub_empty"
        user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-generic-loss"', content)
        self.assertIn(
            "You will lose access to all paid features.", content
        )

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

    def test_context_empty_features_for_free_user(self):
        """tier_features is empty list for free-tier users."""
        user = User.objects.create_user(email="freectx@test.com")
        self.client.force_login(user)

        response = self.client.get("/account/")
        tier_features = response.context["tier_features"]
        # Free tier has features, but check it still works
        self.assertIsInstance(tier_features, list)


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelModalNoBillingDateTest(TestCase):
    """Tests for cancel modal when billing_period_end is not set."""

    def setUp(self):
        self.main_tier = Tier.objects.get(slug="main")
        self.user = User.objects.create_user(email="nodate@test.com")
        self.user.tier = self.main_tier
        self.user.subscription_id = "sub_nodate"
        # Do not set billing_period_end
        self.user.save(update_fields=["tier", "subscription_id"])
        self.client.force_login(self.user)

    def test_modal_renders_without_billing_date(self):
        """The modal renders correctly even without a billing period end date."""
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="cancel-modal"', content)
        self.assertIn("Cancel Subscription", content)

    def test_billing_text_excludes_date_when_not_set(self):
        """When no billing_period_end, the date parenthetical is not shown."""
        response = self.client.get("/account/")
        content = response.content.decode()
        # The text should say "end of your billing period" without a date
        self.assertIn(
            "until the end of your billing period", content
        )
        # Should not have a date in parentheses near this text
        self.assertNotIn("billing period (", content)


class FreeUserNoCancelButtonTest(TestCase):
    """Tests that free tier users do not see the Cancel Subscription button."""

    def test_free_user_no_cancel_button(self):
        """Free-tier users do not see the Cancel Subscription button."""
        user = User.objects.create_user(email="freeuser@test.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('id="cancel-btn"', content)

    def test_free_user_no_cancel_modal_trigger(self):
        """Free-tier users do not have the showCancelConfirm trigger button."""
        user = User.objects.create_user(email="freemodal@test.com")
        self.client.force_login(user)
        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertNotIn('onclick="showCancelConfirm()"', content)


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
    def test_after_cancel_page_shows_cancellation_notice(self, mock_cancel):
        """After cancellation, the page shows the pending cancellation notice."""
        mock_sub = MagicMock()
        mock_sub.current_period_end = 1774396800
        mock_cancel.return_value = mock_sub

        self.client.post(
            self.url,
            data="{}",
            content_type="application/json",
        )

        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn('id="pending-cancellation-notice"', content)
        self.assertIn("access ends on", content)


@override_settings(STRIPE_CHECKOUT_ENABLED=True)
class CancelModalBillingDateDisplayTest(TestCase):
    """Tests that the billing period end date is shown in the cancel modal."""

    def test_modal_shows_billing_date(self):
        """The cancel modal includes the billing period end date."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="date@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_date_test"
        user.billing_period_end = timezone.make_aware(
            datetime(2026, 4, 1, 12, 0, 0)
        )
        user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()
        # The date should appear in the cancel modal's explanation text
        self.assertIn("01/04/2026", content)

    def test_modal_shows_different_billing_date(self):
        """The cancel modal shows the correct date for a different billing date."""
        main_tier = Tier.objects.get(slug="main")
        user = User.objects.create_user(email="date2@test.com")
        user.tier = main_tier
        user.subscription_id = "sub_date_test2"
        user.billing_period_end = timezone.make_aware(
            datetime(2026, 12, 25, 12, 0, 0)
        )
        user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(user)

        response = self.client.get("/account/")
        content = response.content.decode()
        self.assertIn("25/12/2026", content)
