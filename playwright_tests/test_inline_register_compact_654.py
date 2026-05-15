"""Playwright coverage for the compact inline-register variant (#654).

Issue #654 follows up on #652: the inline register card on /pricing
collapses its OAuth provider buttons behind a "More sign-in options"
disclosure so the free-tier card no longer outweighs the
Basic / Main / Premium siblings at 1440x900. Course detail and
workshop pages paywall keep the expanded variant.

The scenarios assert what the visitor sees:

  - the toggle is present on /pricing and absent on /courses/<slug>
  - clicking the toggle reveals OAuth buttons and flips aria-expanded
  - the Google href still carries ?next=/pricing once revealed
  - empty OAuth config drops the toggle (no orphan button)
  - the form still submits regardless of disclosure state
"""

import os
import uuid
from urllib.parse import quote

import pytest

from playwright_tests.conftest import DEFAULT_PASSWORD, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _new_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _reset_state():
    """Clear fixtures the compact-variant suite touches.

    Caller holds ``django_db_blocker.unblock()``; close the connection
    so the Django server thread can read its own writes.
    """
    from allauth.socialaccount.models import SocialApp
    from django.db import connection

    from content.models import Course, Module, Unit

    Unit.objects.filter(
        module__course__slug__startswith="compact-654",
    ).delete()
    Module.objects.filter(course__slug__startswith="compact-654").delete()
    Course.objects.filter(slug__startswith="compact-654").delete()
    SocialApp.objects.all().delete()
    connection.close()


def _seed_free_course(slug="compact-654-demo"):
    from django.db import connection

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Demo Course",
        slug=slug,
        status="published",
        required_level=0,
        description="A free course used by the compact inline-register E2E.",
    )
    module = Module.objects.create(
        course=course, title="Module", slug="module", sort_order=1,
    )
    Unit.objects.create(
        module=module, title="Lesson", slug="lesson", sort_order=1,
    )
    connection.close()
    return course, slug


def _configure_oauth(*providers):
    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site
    from django.db import connection

    SocialApp.objects.all().delete()
    site = Site.objects.get_current()
    names = {"google": "Google", "github": "GitHub", "slack": "Slack"}
    for provider in providers:
        app = SocialApp.objects.create(
            provider=provider,
            name=names[provider],
            client_id=f"{provider}-cid",
            secret=f"{provider}-secret",
        )
        app.sites.add(site)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestInlineRegisterCompactVariant:
    """Six BDD scenarios pinned by the issue body."""

    def test_pricing_free_card_renders_compact_with_oauth_hidden(
        self, django_server, page, django_db_blocker,
    ):
        """Visitor on /pricing sees the form + toggle button, with
        OAuth buttons hidden behind the disclosure."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        assert free_card.is_visible()
        # The inline register form is inside the free-tier card.
        assert free_card.locator("#register-email").is_visible()
        assert free_card.locator("#register-password").is_visible()
        assert free_card.locator("#register-password-confirm").is_visible()
        assert free_card.locator("#register-submit").is_visible()
        # The compact toggle is present and starts collapsed.
        toggle = free_card.locator(
            '[data-testid="inline-register-oauth-toggle"]',
        )
        assert toggle.is_visible()
        assert toggle.get_attribute("aria-expanded") == "false"
        # The Google button exists in the DOM but is hidden inside the
        # ``[hidden]`` block — Playwright treats it as not visible.
        google_button = free_card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        assert google_button.is_visible() is False

    def test_pricing_toggle_expands_and_collapses_oauth(
        self, django_server, page, django_db_blocker,
    ):
        """Clicking the toggle reveals the Google button and flips
        aria-expanded; clicking again hides it."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        toggle = free_card.locator(
            '[data-testid="inline-register-oauth-toggle"]',
        )
        google_button = free_card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        # Expand.
        toggle.click()
        google_button.wait_for(state="visible")
        assert toggle.get_attribute("aria-expanded") == "true"
        # Collapse again.
        toggle.click()
        google_button.wait_for(state="hidden")
        assert toggle.get_attribute("aria-expanded") == "false"

    def test_pricing_google_href_carries_next_url_after_disclosure(
        self, django_server, page, django_db_blocker,
    ):
        """Once the disclosure is open the Google button's href still
        round-trips /pricing via ?next= so the visitor lands back here
        after the OAuth callback."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        free_card.locator(
            '[data-testid="inline-register-oauth-toggle"]',
        ).click()
        google_button = free_card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        google_button.wait_for(state="visible")
        href = google_button.get_attribute("href")
        assert href is not None
        assert href.startswith("/accounts/google/login/")
        # ``next=`` carries the originating page so the user lands
        # back on /pricing after the OAuth callback. Forward slashes
        # in the value are not URL-encoded by Django's ``urlencode``
        # filter, so we accept both the safe shape and the fully
        # encoded shape to keep this assertion robust.
        assert ("next=/pricing" in href
                or f"next={quote('/pricing', safe='')}" in href)

    def test_free_course_keeps_expanded_variant_with_visible_oauth(
        self, django_server, page, django_db_blocker,
    ):
        """Course detail surfaces still render the expanded variant —
        OAuth provider buttons are visible immediately, no toggle."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            _configure_oauth("google")

        page.goto(
            f"{django_server}/courses/compact-654-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        assert card.is_visible()
        assert card.locator("#register-email").is_visible()
        # OAuth button visible without clicking anything.
        google_button = card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        assert google_button.is_visible()
        # No compact toggle on this surface.
        assert (
            card.locator(
                '[data-testid="inline-register-oauth-toggle"]',
            ).count() == 0
        )

    def test_pricing_with_no_oauth_shows_no_toggle(
        self, django_server, page, django_db_blocker,
    ):
        """No SocialApp configured → compact mode still hides cleanly.

        The toggle button only makes sense when there is OAuth to
        reveal; absent that, no orphan button should render and no
        OAuth provider markup should appear anywhere on the card.
        """
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        # Form still renders.
        assert free_card.locator("#register-email").is_visible()
        # No toggle, no OAuth markup.
        assert (
            free_card.locator(
                '[data-testid="inline-register-oauth-toggle"]',
            ).count() == 0
        )
        assert (
            free_card.locator("[data-auth-oauth-divider]").count() == 0
        )
        assert (
            free_card.locator("[data-auth-oauth-providers]").count() == 0
        )

    def test_pricing_inline_form_submits_regardless_of_disclosure(
        self, django_server, page, django_db_blocker,
    ):
        """A visitor can register from the compact card without ever
        opening the OAuth disclosure — the form POST is independent
        of the disclosure state."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")
        email = _new_email("compact-signup")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        free_card.locator("#register-email").fill(email)
        free_card.locator("#register-password").fill(DEFAULT_PASSWORD)
        free_card.locator("#register-password-confirm").fill(DEFAULT_PASSWORD)
        free_card.locator("#register-submit").click()

        success = free_card.locator("#register-success")
        success.wait_for(state="visible")
        # The return-link points back to /pricing.
        return_link = success.locator("a")
        assert return_link.get_attribute("href") == "/pricing"
        # Browser stayed on /pricing — no full-page redirect.
        assert page.url.endswith("/pricing")
        # User row exists, unverified.
        with django_db_blocker.unblock():
            from accounts.models import User

            user = User.objects.get(email=email)
            assert user.email_verified is False
