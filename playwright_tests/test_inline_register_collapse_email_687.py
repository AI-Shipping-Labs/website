"""Playwright coverage for the collapse-email inline-register variant (#687).

Issue #687 introduced ``collapse_email`` so free course detail pages lead
with OAuth. Issue #8d80cf13 ("Lead auth surfaces with social sign-in")
then refined the pattern: expanding the email form in place made the
embedding page (course detail, pricing, homepage free section) reflow, so
the email path now links out to /accounts/register/ instead of expanding
inline. The current contract on a ``collapse_email=True`` surface with at
least one OAuth provider enabled is:

  - the OAuth provider buttons render first and are visible immediately
  - a "Sign up with your email" LINK (not a toggle) follows the divider
  - the email/password inputs are NOT rendered inline (no dead form in DOM)
  - the link carries ?next=<originating page>

When no OAuth provider is configured the email form renders expanded, so
the card is never a dead end.

These scenarios pin that visitor-facing contract on both /courses/<slug>
and /pricing. The partial-level HTML shape is additionally covered by
``accounts/tests/test_inline_register.py``.
"""

import os
import uuid
from urllib.parse import quote

import pytest

from playwright_tests.conftest import DEFAULT_PASSWORD, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _new_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _reset_state():
    """Clear fixtures the collapse-email suite touches.

    Caller holds ``django_db_blocker.unblock()``; close the connection
    so the Django server thread can read its own writes.
    """
    from allauth.socialaccount.models import SocialApp
    from django.db import connection

    from content.models import Course, Module, Unit

    Unit.objects.filter(
        module__course__slug__startswith="collapse-687",
    ).delete()
    Module.objects.filter(course__slug__startswith="collapse-687").delete()
    Course.objects.filter(slug__startswith="collapse-687").delete()
    SocialApp.objects.all().delete()
    connection.close()


def _seed_free_course(slug="collapse-687-demo"):
    from django.db import connection

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Demo Course",
        slug=slug,
        status="published",
        required_level=0,
        description="A free course used by the collapse-email E2E.",
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
class TestInlineRegisterCollapseEmailVariant:
    """Scenarios pinning the OAuth-first, link-out collapse-email pattern."""

    def test_course_detail_leads_with_oauth_and_email_link(
        self, django_server, page, django_db_blocker,
    ):
        """Anon visitor on /courses/<slug> sees OAuth first and a
        "Sign up with your email" link. The email inputs are NOT rendered
        inline; the email path links out to /accounts/register/."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            _configure_oauth("google")

        page.goto(
            f"{django_server}/courses/collapse-687-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        assert card.is_visible()
        # OAuth provider visible without clicking anything.
        google_button = card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        assert google_button.is_visible()
        # The email path is a link out, not an inline toggle or form.
        email_link = card.locator(
            '[data-testid="inline-register-email-link"]',
        )
        assert email_link.is_visible()
        assert (
            card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )
        # The email/password inputs are not rendered inline on this surface.
        assert card.locator("#register-email").count() == 0
        assert card.locator("#register-password").count() == 0

    def test_email_link_targets_register_with_next(
        self, django_server, page, django_db_blocker,
    ):
        """The "Sign up with your email" link points at /accounts/register/
        and carries ?next=<course detail> so the visitor lands back here."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            _configure_oauth("google")

        page.goto(
            f"{django_server}/courses/collapse-687-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        email_link = card.locator(
            '[data-testid="inline-register-email-link"]',
        )
        href = email_link.get_attribute("href")
        assert href is not None
        assert href.startswith("/accounts/register/")
        assert (
            "next=/courses/collapse-687-demo" in href
            or f"next={quote('/courses/collapse-687-demo', safe='')}" in href
        )

    def test_email_link_navigates_to_register_form(
        self, django_server, page, django_db_blocker,
    ):
        """Clicking the email link lands on the standalone register page
        where the email/password form is rendered."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            _configure_oauth("google")

        page.goto(
            f"{django_server}/courses/collapse-687-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        card.locator(
            '[data-testid="inline-register-email-link"]',
        ).click()
        page.wait_for_url("**/accounts/register/**")
        page.locator("#register-email").wait_for(state="visible")
        assert page.locator("#register-email").is_visible()

    def test_pricing_page_uses_collapse_email_pattern(
        self, django_server, page, django_db_blocker,
    ):
        """/pricing uses the same social-first, email-link-out pattern
        as of #1188."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        google_button = free_card.get_by_role(
            "link", name="Sign up with Google", exact=True,
        )
        assert google_button.is_visible()
        # Email path is a link out; no inline email inputs, no toggle.
        assert (
            free_card.locator(
                '[data-testid="inline-register-email-link"]',
            ).count() == 1
        )
        assert free_card.locator("#register-email").count() == 0
        assert (
            free_card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )
        # The old #654 OAuth toggle is not used on pricing anymore.
        assert (
            free_card.locator(
                '[data-testid="inline-register-oauth-toggle"]',
            ).count() == 0
        )

    def test_no_oauth_renders_email_expanded_on_course_detail(
        self, django_server, page, django_db_blocker,
    ):
        """When no SocialApp is configured, the dead-end guard kicks in:
        the email form is rendered expanded and no email link is rendered.
        """
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            # Intentionally NOT calling _configure_oauth — SocialApp is empty.

        page.goto(
            f"{django_server}/courses/collapse-687-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        # Email form is visible right away.
        assert card.locator("#register-email").is_visible()
        # No email link, because OAuth is not offered here.
        assert (
            card.locator(
                '[data-testid="inline-register-email-link"]',
            ).count() == 0
        )
        assert (
            card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )

    def test_no_oauth_email_form_registers_user_successfully(
        self, django_server, page, django_db_blocker,
    ):
        """With no OAuth configured the inline email form submits and
        redirects back to the course as an authenticated user."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            # No OAuth configured — the email form renders expanded.
        email = _new_email("collapse-signup")

        page.goto(
            f"{django_server}/courses/collapse-687-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        card.locator("#register-email").wait_for(state="visible")
        card.locator("#register-email").fill(email)
        card.locator("#register-password").fill(DEFAULT_PASSWORD)
        card.locator("#register-password-confirm").fill(DEFAULT_PASSWORD)
        card.locator("#register-submit").click()

        page.locator('[data-testid="account-menu-trigger"]').wait_for(
            state="visible",
        )
        assert page.url.endswith("/courses/collapse-687-demo")
        # User row exists, unverified.
        with django_db_blocker.unblock():
            from accounts.models import User

            user = User.objects.get(email=email)
            assert user.email_verified is False
