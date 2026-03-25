"""Tests for hiding newsletter signup for logged-in users - issue #149.

Covers:
- Footer newsletter form hidden for authenticated users, visible for anonymous
- Pricing page Free tier CTA hidden for authenticated users, visible for anonymous
- Blog empty-state subscribe link hidden for authenticated users, visible for anonymous
- Footer still shows logo, tagline, community links for all users
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from tests.fixtures import TierSetupMixin

User = get_user_model()


class FooterNewsletterTest(TierSetupMixin, TestCase):
    """Test that the footer newsletter block respects authentication state."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="member@test.com", password="testpass123"
        )

    def test_anonymous_user_sees_newsletter_form_in_footer(self):
        response = self.client.get("/about")
        self.assertContains(response, 'name="email"')
        self.assertContains(response, "Subscribe to the free newsletter")

    def test_authenticated_user_does_not_see_newsletter_form_in_footer(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/about")
        self.assertNotContains(response, "Subscribe to the free newsletter")
        self.assertNotContains(
            response, 'class="subscribe-form', msg_prefix="footer"
        )

    def test_authenticated_user_still_sees_footer_logo_and_links(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/about")
        self.assertContains(response, "AI Shipping Labs")
        self.assertContains(response, "Community")
        self.assertContains(response, "All rights reserved")


class PricingFreeTierCTATest(TierSetupMixin, TestCase):
    """Test that the Free tier Subscribe button respects authentication state."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="member@test.com", password="testpass123"
        )

    def test_anonymous_user_sees_free_tier_subscribe_button(self):
        response = self.client.get("/pricing")
        self.assertContains(
            response,
            '/#newsletter',
            msg_prefix="Free tier Subscribe link should point to newsletter",
        )

    def test_authenticated_user_does_not_see_free_tier_subscribe_button(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/pricing")
        self.assertNotContains(response, '/#newsletter')
        self.assertContains(response, "You're a member")

    def test_authenticated_user_still_sees_paid_tier_join_buttons(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/pricing")
        # Paid tiers should still have Join buttons
        self.assertContains(response, "Join")


class BlogEmptyStateSubscribeTest(TierSetupMixin, TestCase):
    """Test that the blog empty-state subscribe link respects authentication."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="member@test.com", password="testpass123"
        )

    def test_anonymous_user_sees_subscribe_link_in_empty_blog(self):
        response = self.client.get("/blog")
        self.assertContains(response, "Subscribe to get notified")
        self.assertContains(response, '/#newsletter')

    def test_authenticated_user_does_not_see_subscribe_link_in_empty_blog(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/blog")
        self.assertNotContains(response, "Subscribe to get notified")

    def test_authenticated_user_still_sees_empty_state_text(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/blog")
        self.assertContains(response, "No posts yet")
