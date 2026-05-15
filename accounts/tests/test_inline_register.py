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


class InlineRegisterCompactVariantTest(TestCase):
    """Issue #654: the inline register card grows a ``compact`` flag.

    When ``compact=True``, the OAuth divider + provider buttons are
    tucked behind a "More sign-in options" disclosure so the free-tier
    card on /pricing stops dwarfing its Basic/Main/Premium siblings.
    Course detail and workshop pages paywall stay on the default
    expanded variant where the wider container can absorb the form.
    """

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

    def test_compact_false_renders_oauth_expanded(self):
        """``compact=False`` (default) keeps the legacy expanded layout —
        no toggle button, OAuth divider rendered inline."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "compact": False,
        })
        self.assertNotIn('data-testid="inline-register-oauth-toggle"', html)
        self.assertNotIn(
            'data-testid="inline-register-oauth-disclosure"', html,
        )
        # OAuth block is present and NOT wrapped by a hidden container.
        self.assertIn("Sign up with Google", html)
        self.assertNotIn(
            'id="inline-register-oauth-block"', html,
        )

    def test_compact_true_renders_toggle_button(self):
        """With ``compact=True`` and one provider enabled, the toggle
        button renders and the OAuth block is wrapped in a hidden
        container — collapsed by default."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/pricing",
            "oauth_google_enabled": True,
            "compact": True,
        })
        self.assertIn('data-testid="inline-register-oauth-toggle"', html)
        self.assertIn("More sign-in options", html)
        # OAuth block wrapper exists with the hidden attribute.
        self.assertIn(
            'id="inline-register-oauth-block"', html,
        )
        # The wrapper carries the ``hidden`` HTML attribute so the
        # OAuth markup is collapsed before JS runs.
        block_segment = html.split('id="inline-register-oauth-block"', 1)[1]
        # The opening tag's attribute list runs up to the next ``>``;
        # ``hidden`` must appear there (not inside the nested OAuth markup).
        opening_tag = block_segment.split('>', 1)[0]
        self.assertIn('hidden', opening_tag)
        # OAuth content is inside the wrapper.
        self.assertIn("Sign up with Google", html)
        # Initial aria-expanded is false on the toggle.
        self.assertIn('aria-expanded="false"', html)

    def test_compact_true_with_no_oauth_hides_toggle(self):
        """When no SocialApp is configured, the toggle button must NOT
        render — there is nothing behind it. Matches today's hide-when-
        empty behavior of the expanded OAuth partial."""
        html = render_to_string(self.template, {
            "next_url": "/pricing",
            "oauth_google_enabled": False,
            "oauth_github_enabled": False,
            "oauth_slack_enabled": False,
            "compact": True,
        })
        self.assertNotIn(
            'data-testid="inline-register-oauth-toggle"', html,
        )
        self.assertNotIn("More sign-in options", html)
        self.assertNotIn(
            'data-testid="inline-register-oauth-disclosure"', html,
        )
        self.assertNotIn("Sign up with Google", html)

    def test_compact_default_is_false(self):
        """No ``compact`` key in context means the partial renders the
        legacy expanded variant — no toggle button, OAuth inline."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
        })
        self.assertNotIn(
            'data-testid="inline-register-oauth-toggle"', html,
        )
        # OAuth still visible inline.
        self.assertIn("Sign up with Google", html)

    def test_compact_toggle_aria_attributes(self):
        """The toggle button must wire ``aria-controls`` to the OAuth
        block ``id`` for screen readers — the disclosure pattern from
        the WAI-ARIA Authoring Practices."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/pricing",
            "oauth_google_enabled": True,
            "compact": True,
        })
        # Pull out the toggle button's opening tag.
        toggle_idx = html.index('data-testid="inline-register-oauth-toggle"')
        # Find the enclosing ``<button`` tag start.
        button_start = html.rfind('<button', 0, toggle_idx)
        button_end = html.index('>', toggle_idx)
        button_tag = html[button_start:button_end + 1]
        self.assertIn('aria-expanded="false"', button_tag)
        self.assertIn(
            'aria-controls="inline-register-oauth-block"', button_tag,
        )
        # And the controlled element exists with that id.
        self.assertIn('id="inline-register-oauth-block"', html)
