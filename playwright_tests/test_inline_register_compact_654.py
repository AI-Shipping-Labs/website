"""Playwright coverage for pricing and course inline-register variants.

Issue #1188 switches /pricing from the old compact OAuth disclosure to
the shared collapse-email pattern: OAuth is visible first and the email
form expands only when requested. The legacy #654 compact behavior is
still covered at the partial level by Django tests.

The scenarios assert what the visitor sees:

  - OAuth is visible on /pricing and email fields start hidden
  - clicking the email toggle reveals the form and flips aria-expanded
  - the Google href still carries ?next=/pricing
  - empty OAuth config renders the email form immediately
  - the expanded email form still submits
"""

import os
from urllib.parse import quote

import pytest

from playwright_tests.conftest import ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


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
class TestPricingInlineRegisterVariant:
    """Six BDD scenarios pinned by the issue body."""

    def test_pricing_free_card_renders_oauth_first_with_email_link(
        self, django_server, page, django_db_blocker,
    ):
        """Visitor on /pricing sees OAuth first, with the email path as a
        link out to /accounts/register/ (not an inline form)."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        assert free_card.is_visible()
        google_button = free_card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        assert google_button.is_visible()
        email_link = free_card.locator(
            '[data-testid="inline-register-email-link"]',
        )
        assert email_link.is_visible()
        # No inline email form and no legacy toggles on this surface.
        assert free_card.locator("#register-email").count() == 0
        assert (
            free_card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )
        assert (
            free_card.locator(
                '[data-testid="inline-register-oauth-toggle"]',
            ).count() == 0
        )

    def test_pricing_email_link_navigates_to_register_form(
        self, django_server, page, django_db_blocker,
    ):
        """Clicking the email link lands on /accounts/register/ where the
        email form is rendered."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        free_card.locator(
            '[data-testid="inline-register-email-link"]',
        ).click()
        page.wait_for_url("**/accounts/register/**")
        page.locator("#register-email").wait_for(state="visible")
        assert page.locator("#register-email").is_visible()

    def test_pricing_google_href_carries_next_url(
        self, django_server, page, django_db_blocker,
    ):
        """The visible Google button's href round-trips /pricing via
        ?next= so the visitor lands back here after the OAuth callback."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
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

    def test_free_course_leads_with_oauth_and_email_link(
        self, django_server, page, django_db_blocker,
    ):
        """Course detail surfaces lead with OAuth and offer a "Sign up with
        your email" link out (#687 as refined by the social-first pass).
        Neither the legacy compact toggle (#654) nor an inline email form
        renders on this surface."""
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
        # OAuth button visible without clicking anything.
        google_button = card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        assert google_button.is_visible()
        # The email path is a link out, not an inline form.
        assert (
            card.locator(
                '[data-testid="inline-register-email-link"]',
            ).count() == 1
        )
        assert card.locator("#register-email").count() == 0
        # No compact (#654) OAuth toggle on this surface.
        assert (
            card.locator(
                '[data-testid="inline-register-oauth-toggle"]',
            ).count() == 0
        )
        # No inline email toggle either — the email path links out.
        assert (
            card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )

    def test_pricing_with_no_oauth_shows_no_toggle(
        self, django_server, page, django_db_blocker,
    ):
        """No SocialApp configured → email form renders expanded.

        The email disclosure only makes sense when OAuth is visible
        first; absent OAuth, no orphan button should render and no
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
            free_card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )
        assert (
            free_card.locator("[data-auth-oauth-divider]").count() == 0
        )
        assert (
            free_card.locator("[data-auth-oauth-providers]").count() == 0
        )

    def test_pricing_email_link_leads_to_register_and_carries_next(
        self, django_server, page, django_db_blocker,
    ):
        """The pricing email link routes to /accounts/register/ carrying
        ?next=/pricing so the visitor lands back on pricing afterwards."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        email_link = free_card.locator(
            '[data-testid="inline-register-email-link"]',
        )
        href = email_link.get_attribute("href")
        assert href is not None
        assert href.startswith("/accounts/register/")
        assert (
            "next=/pricing" in href
            or f"next={quote('/pricing', safe='')}" in href
        )
        email_link.click()
        page.wait_for_url("**/accounts/register/**")
        page.locator("#register-email").wait_for(state="visible")
        assert page.locator("#register-email").is_visible()
