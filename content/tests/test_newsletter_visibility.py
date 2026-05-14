"""Tests for hiding newsletter signup for logged-in users - issue #149.

Covers:
- Footer newsletter form hidden for authenticated users, visible for anonymous
- Pricing page Free tier CTA hidden for authenticated users, visible for anonymous
- Blog empty-state stays browse-first; footer remains the anonymous signup
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
        # Post-launch footer copy (issue #319).
        self.assertContains(response, "Build AI in public, with a group.")
        self.assertContains(response, "Subscribe to stay on top of what's happening in the community")

    def test_authenticated_user_does_not_see_newsletter_form_in_footer(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/about")
        # Authenticated members do not see the footer signup block.
        self.assertNotContains(response, "Build AI in public, with a group.")
        self.assertNotContains(
            response, 'class="subscribe-form', msg_prefix="footer"
        )

    def test_authenticated_user_still_sees_footer_logo_links_and_version(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/about")
        self.assertContains(response, "AI Shipping Labs")
        self.assertContains(response, "Community")
        self.assertContains(response, "Version N/A")
        self.assertNotContains(response, "All rights reserved")


class PricingFreeTierCTATest(TierSetupMixin, TestCase):
    """Test that the Free tier Subscribe button respects authentication state."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="member@test.com", password="testpass123"
        )

    def test_anonymous_user_sees_free_tier_signup_cta(self):
        response = self.client.get("/pricing")
        # Post-CTA-swap: free tier directs anonymous visitors at account
        # signup instead of the newsletter anchor.
        self.assertContains(
            response,
            'href="/accounts/register/"',
            msg_prefix="Free tier CTA should link to account signup",
        )
        self.assertContains(response, "Create an account")
        self.assertNotContains(response, 'href="/#newsletter"')

    def test_authenticated_user_does_not_see_free_tier_subscribe_button(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/pricing")
        self.assertNotContains(response, 'href="/#newsletter"')
        self.assertNotContains(response, "Create an account")
        self.assertContains(response, "Current free plan")

    def test_authenticated_user_still_sees_paid_tier_upgrade_buttons(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/pricing")
        # Paid tiers should still offer a paid upgrade path.
        self.assertContains(response, "Upgrade")


class BlogEmptyStateSubscribeTest(TierSetupMixin, TestCase):
    """Test that the blog empty-state no longer competes with the footer."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email="member@test.com", password="testpass123"
        )

    def test_anonymous_user_sees_browse_first_empty_blog_copy(self):
        response = self.client.get("/blog")
        self.assertContains(
            response,
            "No articles match this filter yet. Browse all articles as the archive grows.",
        )
        self.assertNotContains(response, "Get articles in the Friday newsletter")

    def test_authenticated_user_does_not_see_subscribe_link_in_empty_blog(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/blog")
        self.assertNotContains(
            response, "Get articles in the Friday newsletter"
        )

    def test_authenticated_user_still_sees_empty_state_text(self):
        self.client.login(email="member@test.com", password="testpass123")
        response = self.client.get("/blog")
        # Post-launch empty-state copy (issue #319).
        self.assertContains(
            response, "No articles match this filter yet"
        )
