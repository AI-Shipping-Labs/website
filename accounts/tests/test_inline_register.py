"""Tests for the inline register card partial (issue #652).

The partial — ``accounts/includes/_inline_register_card.html`` — wraps
the existing register form / OAuth / legal partials and is included by
three public surfaces (course detail free-anon branch, workshop pages
paywall, pricing free-tier card). These tests render the partial in
isolation through ``django.template.loader.render_to_string`` so the
surface views' own context-setup doesn't need to be exercised here.
"""

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import TestCase


class InlineRegisterPartialTest(TestCase):
    """Render the inline register card partial directly and assert the
    expected fields, OAuth gates, and ``next_url`` round-trip."""

    template = "accounts/includes/_inline_register_card.html"

    def _seed_provider(self, provider, name):
        app = SocialApp.objects.create(
            provider=provider,
            name=name,
            client_id=f"{provider}-cid",
            secret=f"{provider}-secret",
        )
        app.sites.add(Site.objects.get_current())
        return app

    def test_partial_includes_register_form(self):
        html = render_to_string(self.template, {"next_url": "/courses/demo"})
        # Form fields from _register_form.html.
        self.assertIn('id="register-email"', html)
        self.assertIn('id="register-password"', html)
        self.assertIn('id="register-password-confirm"', html)
        # Submit button label.
        self.assertIn("Create Account", html)
        # Wrapper testid so surface tests can scope to the card.
        self.assertIn('data-testid="inline-register-card"', html)

    def test_partial_includes_oauth_when_provider_configured(self):
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
        })
        self.assertIn("Sign up with Google", html)
        self.assertIn("/accounts/google/login/", html)
        # OAuth divider is rendered when any provider is enabled.
        self.assertIn("or sign up with", html)

    def test_partial_hides_oauth_when_no_provider(self):
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": False,
            "oauth_github_enabled": False,
            "oauth_slack_enabled": False,
        })
        self.assertNotIn("Sign up with Google", html)
        self.assertNotIn("Sign up with GitHub", html)
        self.assertNotIn("Sign up with Slack", html)
        self.assertNotIn("or sign up with", html)

    def test_partial_login_link_carries_next_url(self):
        html = render_to_string(self.template, {
            "next_url": "/courses/demo-course",
        })
        # _register_form.html renders the login link with the urlencoded
        # next value. Django's urlencode filter does not encode forward
        # slashes by default — assert the safe shape.
        self.assertIn(
            '/accounts/login/?next=/courses/demo-course',
            html,
        )

    def test_partial_oauth_buttons_carry_next_url(self):
        """OAuth provider links must round-trip the originating page so
        the visitor lands back where they started after callback."""
        self._seed_provider("github", "GitHub")
        html = render_to_string(self.template, {
            "next_url": "/pricing",
            "oauth_github_enabled": True,
        })
        self.assertIn(
            '/accounts/github/login/?next=/pricing',
            html,
        )
