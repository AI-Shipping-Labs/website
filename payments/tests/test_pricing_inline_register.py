"""Tests for the inline register card on the /pricing free-tier card.

Issue #652. Anonymous visitors land on /pricing, see the free tier card
render the inline register form (in place of the legacy "Create an
account" link button), and can register without leaving the page. The
authenticated branch never shows the form.
"""

from allauth.socialaccount.models import SocialApp
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.test import TestCase

from payments.models import Tier


class PricingInlineRegisterTest(TestCase):
    """Anonymous visitors on /pricing see the inline register card
    inside the free tier's CTA slot. Logged-in users do not."""

    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.free = Tier.objects.get(slug="free")

    def test_anonymous_pricing_shows_inline_register_in_free_card(self):
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        # The inline card renders inside the free tier card.
        self.assertContains(response, 'data-testid="inline-register-card"')
        self.assertContains(response, 'id="register-email"')
        self.assertContains(response, 'id="register-password"')
        # The legacy button is gone — there is no <a href="/accounts/register/"
        # block for the signup action kind anymore.
        body = response.content.decode()
        # Scope to the free tier card so the header/nav register links
        # (if any) don't false-positive.
        free_card_start = body.index('data-tier-card="free"')
        free_card_end = body.index('data-tier-card', free_card_start + 1) \
            if 'data-tier-card' in body[free_card_start + 1:] else len(body)
        free_card = body[free_card_start:free_card_end]
        self.assertNotIn(
            '<a href="/accounts/register/"',
            free_card,
            'Free-tier card must not render the legacy signup button.',
        )

    def test_anonymous_pricing_inline_form_has_next_url(self):
        """Login link inside the inline card returns the visitor to
        /pricing after sign-in."""
        response = self.client.get("/pricing")
        self.assertContains(response, '/accounts/login/?next=/pricing')
        # Guard against Django comment leaks — multi-line ``{# #}``
        # tags don't terminate so they leak into rendered HTML.
        self.assertNotContains(response, '{# ')

    def test_anonymous_pricing_inline_form_oauth_context(self):
        """Configured SocialApps render OAuth buttons targeted at
        /pricing via ?next=."""
        app = SocialApp.objects.create(
            provider='google', name='Google',
            client_id='google-cid', secret='google-secret',
        )
        app.sites.add(Site.objects.get_current())
        response = self.client.get("/pricing")
        self.assertContains(response, "Sign up with Google")
        self.assertContains(
            response,
            '/accounts/google/login/?next=/pricing',
        )

    def test_authenticated_pricing_hides_inline_register(self):
        """A logged-in user on /pricing never sees the inline register
        card. The free-tier card shifts to a disabled or current-plan
        state."""
        user = self.User.objects.create_user(
            email="auth-pricing@test.com", password="testpass123",
        )
        user.tier = self.free
        user.save(update_fields=["tier"])
        self.client.force_login(user)
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="inline-register-card"')
        self.assertNotContains(response, 'id="register-email"')
