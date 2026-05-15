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


class PricingInlineRegisterCompactTest(TestCase):
    """Issue #654: /pricing renders the inline register card in compact
    mode so the free-tier slot stops dwarfing the Basic/Main/Premium
    siblings on a 1440-wide grid.
    """

    @classmethod
    def setUpTestData(cls):
        cls.User = get_user_model()
        cls.free = Tier.objects.get(slug="free")

    def _free_card_html(self, response):
        """Slice out just the free-tier card so we don't false-positive
        on toggle buttons elsewhere on the page (header, footer)."""
        body = response.content.decode()
        free_start = body.index('data-tier-card="free"')
        # The next tier card opens with another ``data-tier-card=``
        # attribute — slice up to it.
        next_card = body.find('data-tier-card=', free_start + 1)
        end = next_card if next_card != -1 else len(body)
        return body[free_start:end]

    def test_anonymous_pricing_uses_compact_variant(self):
        """The free-tier card on /pricing renders the compact toggle
        and keeps the OAuth block hidden until the visitor expands it."""
        app = SocialApp.objects.create(
            provider='google', name='Google',
            client_id='google-cid', secret='google-secret',
        )
        app.sites.add(Site.objects.get_current())

        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        free_card = self._free_card_html(response)
        # Compact toggle is inside the free-tier card.
        self.assertIn(
            'data-testid="inline-register-oauth-toggle"', free_card,
        )
        self.assertIn("More sign-in options", free_card)
        # OAuth wrapper is hidden by default.
        self.assertIn('id="inline-register-oauth-block"', free_card)
        block_segment = free_card.split(
            'id="inline-register-oauth-block"', 1,
        )[1]
        opening_tag = block_segment.split('>', 1)[0]
        self.assertIn('hidden', opening_tag)
        # The OAuth markup itself is still rendered (inside the hidden
        # wrapper) — clicking the toggle reveals it without re-render.
        self.assertIn("Sign up with Google", free_card)

    def test_anonymous_pricing_compact_with_no_oauth_no_toggle(self):
        """No SocialApp configured → no toggle button on /pricing.

        Compact mode must not leave an orphan "More sign-in options"
        button when OAuth would have been empty.
        """
        response = self.client.get("/pricing")
        self.assertEqual(response.status_code, 200)
        free_card = self._free_card_html(response)
        # Inline card is still present (email + password form).
        self.assertIn('data-testid="inline-register-card"', free_card)
        self.assertIn('id="register-email"', free_card)
        # No toggle button, no disclosure wrapper.
        self.assertNotIn(
            'data-testid="inline-register-oauth-toggle"', free_card,
        )
        self.assertNotIn("More sign-in options", free_card)
        self.assertNotIn(
            'data-testid="inline-register-oauth-disclosure"', free_card,
        )
