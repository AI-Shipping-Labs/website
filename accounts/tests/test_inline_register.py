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
        self.assertIn("Create account", html)
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

    def test_partial_ignores_slack_as_a_signup_provider(self):
        self._seed_provider("slack", "Slack")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_slack_enabled": True,
            "collapse_email": True,
        })

        self.assertIn('id="register-email"', html)
        self.assertNotIn("Sign up with Slack", html)
        self.assertNotIn("/accounts/slack/login/", html)
        self.assertNotIn('data-auth-oauth-divider', html)
        self.assertNotIn('data-testid="inline-register-email-toggle"', html)
        self.assertNotIn('data-testid="inline-register-oauth-toggle"', html)

    def test_partial_omits_paid_tier_slack_upsell(self):
        """The Slack line was an upsell caveat for Main/Premium sitting at
        the bottom of a FREE signup card, stacked under an unrelated
        newsletter disclosure. It is not relevant to someone creating a
        free account, so it must not come back here."""
        html = render_to_string(self.template, {"next_url": "/pricing"})

        self.assertNotIn(
            "Main and Premium members can join Slack after activating membership.",
            html,
        )
        # The newsletter opt-in disclosure (#653) DOES stay — it is the
        # only notice that a free account subscribes you to updates.
        self.assertIn("You can unsubscribe at any time.", html)

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

    def test_partial_login_link_omits_homepage_next_url(self):
        html = render_to_string(self.template, {
            "next_url": "/",
        })
        self.assertIn('/accounts/login/"', html)
        self.assertNotIn('/accounts/login/?next=/', html)

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


class InlineRegisterCollapseEmailVariantTest(TestCase):
    """Issue #687: the inline register card grows a ``collapse_email``
    flag used by free course detail pages.

    When ``collapse_email=True`` and at least one OAuth provider is
    configured, the card renders the OAuth row, then an "or" divider,
    then a "Sign up with your email" LINK to /accounts/register/. The
    email form is deliberately not rendered inline: expanding it in
    place made the embedding page reflow awkwardly, so the email path
    now navigates instead of disclosing (it was an inline toggle when
    #687 first shipped). When no OAuth provider is configured the form
    renders expanded (no dead-end card with nothing to click).

    The flag is independent of ``compact``; surfaces opt into each
    disclosure mode explicitly.
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

    def test_collapse_email_default_is_false(self):
        """No ``collapse_email`` key in context means the legacy
        expanded layout — no email toggle button rendered."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
        })
        self.assertNotIn(
            'data-testid="inline-register-email-toggle"', html,
        )
        self.assertNotIn(
            'id="inline-register-email-block"', html,
        )

    def test_collapse_email_false_renders_form_expanded(self):
        """Explicit ``collapse_email=False`` keeps the legacy layout."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "collapse_email": False,
        })
        self.assertNotIn(
            'data-testid="inline-register-email-toggle"', html,
        )
        # Email form is rendered (not inside a hidden block).
        self.assertIn('id="register-email"', html)
        self.assertNotIn(
            'id="inline-register-email-block"', html,
        )

    def test_collapse_email_true_with_oauth_links_out_to_register(self):
        """With ``collapse_email=True`` and one provider enabled, the
        email path is a link to the standalone register page — not an
        inline disclosure. The OAuth row is rendered inline (not
        hidden)."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "collapse_email": True,
        })
        # Email CTA is present with the spec'd label.
        self.assertIn('data-testid="inline-register-email-link"', html)
        self.assertIn("Sign up with your email", html)
        # No inline disclosure machinery survives.
        self.assertNotIn('data-testid="inline-register-email-toggle"', html)
        self.assertNotIn('id="inline-register-email-block"', html)
        # The email form is NOT inlined on this surface — navigating to
        # /accounts/register/ is what makes the embedding page stop
        # reflowing, so an inline form here would be the regression.
        self.assertNotIn('id="register-email"', html)
        self.assertNotIn('id="register-password"', html)
        # OAuth row is rendered inline (not behind a compact toggle).
        self.assertIn("Sign up with Google", html)
        self.assertNotIn(
            'data-testid="inline-register-oauth-toggle"', html,
        )

    def test_collapse_email_true_oauth_renders_before_email_link(self):
        """OAuth row must appear ABOVE the email link in the DOM so the
        visitor sees the social buttons first."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "collapse_email": True,
        })
        google_idx = html.index("Sign up with Google")
        link_idx = html.index('data-testid="inline-register-email-link"')
        self.assertLess(google_idx, link_idx)

    def test_collapse_email_link_targets_register_page_with_next(self):
        """The email CTA is an anchor to the standalone register page and
        carries ``next`` so the visitor returns to the embedding page."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "collapse_email": True,
        })
        link_idx = html.index('data-testid="inline-register-email-link"')
        anchor_start = html.rfind('<a', 0, link_idx)
        anchor_tag = html[anchor_start:html.index('>', link_idx) + 1]
        self.assertIn('href="/accounts/register/?next=', anchor_tag)
        self.assertIn('/courses/demo', anchor_tag)
        # It must be a real navigation, not a disclosure control.
        self.assertNotIn('aria-expanded', anchor_tag)

    def test_collapse_email_divider_sits_between_oauth_and_email(self):
        """Regression for the dangling divider: when the OAuth row is
        rendered FIRST, the card must not open with "or sign up with"
        referring to nothing above it."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "collapse_email": True,
        })
        self.assertNotIn("or sign up with", html)
        google_idx = html.index("Sign up with Google")
        divider_idx = html.index('data-auth-oauth-divider')
        link_idx = html.index('data-testid="inline-register-email-link"')
        self.assertLess(google_idx, divider_idx)
        self.assertLess(divider_idx, link_idx)

    def test_collapse_email_true_without_oauth_renders_form_expanded(self):
        """Dead-end guard: if no OAuth provider is configured AND
        ``collapse_email=True``, render the email form expanded (no
        toggle) so the visitor still has a clear path to sign up."""
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": False,
            "oauth_github_enabled": False,
            "oauth_slack_enabled": False,
            "collapse_email": True,
        })
        # No toggle.
        self.assertNotIn(
            'data-testid="inline-register-email-toggle"', html,
        )
        self.assertNotIn(
            'id="inline-register-email-block"', html,
        )
        # Form is rendered (visible, not hidden).
        self.assertIn('id="register-email"', html)
        self.assertIn('id="register-password"', html)
        self.assertIn('id="register-password-confirm"', html)

    def test_collapse_email_does_not_affect_compact_variant(self):
        """The collapse_email flag is orthogonal to ``compact``.
        Rendering the partial with compact=True and no collapse_email
        still renders the OAuth disclosure and the email form inline.
        """
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/pricing",
            "oauth_google_enabled": True,
            "compact": True,
        })
        # No #687 toggle/block on the pricing variant.
        self.assertNotIn(
            'data-testid="inline-register-email-toggle"', html,
        )
        self.assertNotIn(
            'id="inline-register-email-block"', html,
        )
        # The compact #654 toggle and block are still present.
        self.assertIn(
            'data-testid="inline-register-oauth-toggle"', html,
        )
        self.assertIn('id="inline-register-oauth-block"', html)
        # Email form is rendered inline (no hidden wrapper).
        self.assertIn('id="register-email"', html)

    def test_collapse_email_legal_and_newsletter_rendered(self):
        """Both legal footer and newsletter opt-in must render
        regardless of disclosure state — they don't move."""
        self._seed_provider("google", "Google")
        html = render_to_string(self.template, {
            "next_url": "/courses/demo",
            "oauth_google_enabled": True,
            "collapse_email": True,
        })
        self.assertIn(
            'data-testid="inline-register-opt-in', html,
        )
        # Legal footer copy from _legal_footer.html mentions Terms.
        self.assertIn("By creating an account", html)
