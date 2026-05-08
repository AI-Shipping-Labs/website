"""Post-launch newsletter / signup CTA copy tests (issue #319).

After we launched, every CTA must use post-launch framing. These tests
pin the locked copy from the groomed issue body so a regression flips
a test, not a silent string drift.

- Home: hero pill, hero CTA button, footer block, articles empty-state
  copy, and consolidated newsletter placement.
- Blog list: browse-first empty-state copy.
- Pricing: free-tier CTA button.
- subscribe_form.html partial defaults (rendered via /subscribe).
- Repository-wide grep guard against pre-launch phrases and the
  founder's personal Substack URL.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings

from tests.fixtures import TierSetupMixin

# ---------------------------------------------------------------
# Home page: anonymous, post-launch copy
# ---------------------------------------------------------------


class HomePostLaunchCopyTest(TierSetupMixin, TestCase):
    """Anonymous visitor sees post-launch copy on /."""

    def test_hero_pill_reads_action_oriented(self):
        response = self.client.get("/")
        self.assertContains(response, "Action-oriented builders. Now open.")
        self.assertNotContains(response, "Invite-only community")

    def test_hero_primary_button_points_to_membership_tiers(self):
        response = self.client.get("/")
        # Match button-cell content rather than the whole HTML body so
        # we fail if the locked text disappears or changes.
        self.assertInHTML(
            (
                '<a href="/#tiers" '
                'class="inline-flex min-h-[44px] w-full items-center '
                'justify-center gap-2 rounded-md bg-accent px-6 py-3 '
                'text-sm font-medium text-accent-foreground transition-colors '
                'hover:bg-accent/90 sm:w-auto">'
                "View Membership Tiers"
                '<i data-lucide="arrow-right" class="h-4 w-4"></i>'
                "</a>"
            ),
            response.content.decode(),
        )
        self.assertNotContains(response, "Subscribe for updates")
        self.assertNotContains(response, "Get the Friday newsletter")

    def test_footer_heading_and_body_post_launch(self):
        response = self.client.get("/")
        self.assertContains(response, "Build AI in public, with a group.")
        self.assertContains(
            response,
            "Subscribe to stay on top of what's happening in the community "
            "and receive updates.",
        )
        # Pre-launch strings must be gone.
        self.assertNotContains(response, "Want to know when we launch?")
        self.assertNotContains(response, "first ping")
        self.assertNotContains(response, "when the community opens")
        # Old post-launch copy must be gone too.
        self.assertNotContains(response, "Free Friday newsletter")

    def test_home_uses_footer_as_single_newsletter_form(self):
        response = self.client.get("/")
        body = response.content.decode()
        self.assertContains(response, 'id="newsletter"')
        self.assertEqual(len(re.findall(r'<form[^>]*class="subscribe-form', body)), 1)
        self.assertNotContains(response, "Stop shipping alone.")

    def test_articles_empty_state_is_browse_first(self):
        # No published articles in the test DB, so the empty-state
        # branch always renders on /.
        response = self.client.get("/")
        # The locked body copy.
        self.assertContains(
            response,
            "New articles drop every Friday. Browse the archive here "
            "as it grows.",
        )
        self.assertNotContains(response, "Subscribe to the newsletter")
        # The Substack URL is gone, including target="_blank".
        self.assertNotContains(response, "alexeyondata.substack.com")

    def test_events_empty_state_no_coming_soon(self):
        response = self.client.get("/")
        # Empty-state text is in this branch when no recordings exist.
        self.assertContains(
            response,
            "New event recordings drop after each live session. "
            "The newsletter has them first.",
        )
        # Old phrasing must be gone from this paragraph.
        self.assertNotContains(
            response,
            "Event recordings coming soon. Check back",
        )

    def test_projects_empty_state_no_coming_soon(self):
        response = self.client.get("/")
        self.assertContains(
            response,
            "Project ideas land here as the community ships them.",
        )
        self.assertNotContains(response, "Subscribe below to see new ones first.")
        self.assertNotContains(
            response,
            "Project ideas coming soon. Check back",
        )


# ---------------------------------------------------------------
# Blog list: anonymous, empty state
# ---------------------------------------------------------------


class BlogEmptyStateCopyTest(TierSetupMixin, TestCase):
    """Anonymous visitor on /blog with no published articles."""

    def test_empty_state_copy_is_browse_first(self):
        response = self.client.get("/blog")
        self.assertContains(
            response,
            "No articles match this filter yet. Browse all articles as "
            "the archive grows.",
        )
        self.assertNotContains(response, "Get articles in the Friday newsletter")
        # The old short CTA text must be gone.
        self.assertNotContains(response, "Subscribe to get notified")
        # And the pre-launch "Check back soon" framing is replaced.
        self.assertNotContains(
            response,
            "Check back soon for articles on AI engineering",
        )


# ---------------------------------------------------------------
# Pricing: free tier CTA
# ---------------------------------------------------------------


class PricingFreeTierPostLaunchCTATest(TierSetupMixin, TestCase):
    """Anonymous visitor on /pricing sees the rewritten free-tier CTA."""

    def test_free_tier_cta_says_get_the_newsletter(self):
        response = self.client.get("/pricing")
        # The button text inside the free-tier card.
        self.assertInHTML(
            (
                '<a href="/#newsletter" '
                'class="block w-full rounded-md px-4 py-3 sm:py-2.5 '
                'text-center text-sm font-medium transition-colors '
                'bg-secondary text-foreground hover:bg-secondary/80 '
                'min-h-[44px] flex items-center justify-center">'
                "Get the newsletter"
                "</a>"
            ),
            response.content.decode(),
        )


# ---------------------------------------------------------------
# subscribe_form.html partial defaults
# ---------------------------------------------------------------


class SubscribeFormDefaultsTest(TierSetupMixin, TestCase):
    """The /subscribe page renders the partial with no overrides, so it
    surfaces the default heading / description verbatim."""

    def test_subscribe_page_uses_community_defaults(self):
        response = self.client.get("/subscribe")
        self.assertContains(response, "Stay on top of the community")
        self.assertContains(
            response,
            "Subscribe to stay on top of what's happening in the community "
            "and receive updates. No spam.",
        )
        # Defaults must NOT be the old generic strings.
        self.assertNotContains(
            response,
            "Ready to turn your AI ideas into real projects?",
        )
        self.assertNotContains(
            response,
            "Subscribe to the free newsletter and get notified about "
            "new content, events, and community updates.",
        )


# ---------------------------------------------------------------
# Repository grep guard
# ---------------------------------------------------------------


# Forbidden phrases. Each one is a regression marker for one of the
# surfaces we just rewrote. If a future change reintroduces any of
# them, this test fails so the team sees the regression in CI.
FORBIDDEN_PATTERNS = [
    "community opens",
    "when we launch",
    "first ping",
    "alexeyondata.substack.com",
]


def _templates_root() -> Path:
    """Resolve the templates/ directory at the repo root."""
    return Path(settings.BASE_DIR) / "templates"


class TemplatesGrepGuardTest(TestCase):
    """CI guard: walking templates/ must find zero matches for the
    pre-launch phrases or the founder's personal Substack URL.

    Implemented as a Django test (instead of a CI step) so devs running
    `uv run python manage.py test` see regressions locally too.
    """

    def test_no_pre_launch_phrases_in_templates(self):
        templates_root = _templates_root()
        self.assertTrue(
            templates_root.is_dir(),
            f"templates/ directory not found at {templates_root}",
        )

        # Use grep with -E so the pipe-alternation is one pass over
        # the tree. -r recursive, -n line numbers, -I skip binary.
        pattern = "|".join(re.escape(p) for p in FORBIDDEN_PATTERNS)
        result = subprocess.run(
            [
                "grep",
                "-rnIE",
                pattern,
                str(templates_root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        # grep exit codes:
        #   0 -> matches found (FAIL: regression)
        #   1 -> no matches (PASS)
        #   2 -> error (FAIL: investigate)
        if result.returncode == 1:
            return  # zero matches, all good
        if result.returncode == 0:
            self.fail(
                "Pre-launch / personal-Substack copy reintroduced in "
                f"templates/:\n{result.stdout}"
            )
        self.fail(
            f"grep failed (exit {result.returncode}): {result.stderr}"
        )


# ---------------------------------------------------------------
# Robustness: empty-state grep guard runs against a full home render
# (covers cases where the rendered HTML differs from the on-disk
# template, e.g. a context processor injecting the old copy).
# ---------------------------------------------------------------


@override_settings(DEBUG=False)
class RenderedHomeNoPreLaunchTest(TierSetupMixin, TestCase):
    """Render / and assert no forbidden phrase reaches the wire."""

    def test_rendered_home_has_no_pre_launch_phrases(self):
        response = self.client.get("/")
        body = response.content.decode()
        for phrase in FORBIDDEN_PATTERNS:
            self.assertNotIn(
                phrase,
                body,
                msg=(
                    f"Pre-launch phrase {phrase!r} appeared in the "
                    "rendered home page. Check templates and any "
                    "context processors."
                ),
            )
