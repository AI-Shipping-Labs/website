"""Playwright coverage for the collapse-email inline-register variant (#687).

Issue #687 follows up on #652/#654: on free course detail pages, the
inline register card now hides the email/password/confirm form behind
a "Sign up with your email" toggle so OAuth becomes the visible-first
CTA. Other surfaces (pricing, gated articles) are unaffected.

Scenarios — pinned by the issue body:

  - anon visitor on /courses/<slug> sees OAuth + collapsed email toggle
  - clicking the toggle expands the form and focuses #register-email
  - clicking again collapses the form
  - keyboard (Space) activates the toggle the same as a mouse click
  - /pricing also uses collapse_email=True (#1188)
  - no-OAuth fallback renders the email form expanded (no dead-end)
  - the expanded email form still registers a user via /api/register
"""

import os
import uuid

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
    """Seven BDD scenarios pinned by the issue body."""

    def test_course_detail_renders_collapsed_email_form_with_oauth_visible(
        self, django_server, page, django_db_blocker,
    ):
        """Anon visitor lands on /courses/<slug> and sees OAuth + a
        "Sign up with your email" toggle. The email/password/confirm
        inputs are in the DOM but inside the hidden block."""
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
        # The new email toggle is present and starts collapsed.
        toggle = card.locator(
            '[data-testid="inline-register-email-toggle"]',
        )
        assert toggle.is_visible()
        assert toggle.get_attribute("aria-expanded") == "false"
        # Form fields exist in DOM but are hidden inside the [hidden] block.
        assert card.locator("#register-email").count() == 1
        assert card.locator("#register-email").is_visible() is False
        assert card.locator("#register-password").is_visible() is False
        assert card.locator("#register-password-confirm").is_visible() is False

    def test_toggle_expands_and_focuses_email_input(
        self, django_server, page, django_db_blocker,
    ):
        """Clicking the toggle expands the email block; focus jumps to
        #register-email and aria-expanded flips to "true"."""
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
        toggle = card.locator(
            '[data-testid="inline-register-email-toggle"]',
        )
        toggle.click()
        # Wait for the email input to become visible.
        card.locator("#register-email").wait_for(state="visible")
        assert toggle.get_attribute("aria-expanded") == "true"
        # Focus management requirement: keyboard / screen-reader users
        # land directly in the email field.
        active_id = page.evaluate("document.activeElement.id")
        assert active_id == "register-email"

    def test_toggle_collapses_email_block_on_second_click(
        self, django_server, page, django_db_blocker,
    ):
        """A second click hides the email block again and flips
        aria-expanded back to "false"."""
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
        toggle = card.locator(
            '[data-testid="inline-register-email-toggle"]',
        )
        toggle.click()
        card.locator("#register-email").wait_for(state="visible")
        toggle.click()
        card.locator("#register-email").wait_for(state="hidden")
        assert toggle.get_attribute("aria-expanded") == "false"

    def test_keyboard_space_activates_toggle(
        self, django_server, page, django_db_blocker,
    ):
        """Pressing Space on the focused toggle button expands the form
        identically to a mouse click — native <button> keyboard support.
        """
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
        toggle = card.locator(
            '[data-testid="inline-register-email-toggle"]',
        )
        toggle.focus()
        page.keyboard.press("Space")
        card.locator("#register-email").wait_for(state="visible")
        assert toggle.get_attribute("aria-expanded") == "true"

    def test_pricing_page_uses_collapse_email_pattern(
        self, django_server, page, django_db_blocker,
    ):
        """/pricing uses the same social-first collapsed email pattern."""
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
        assert free_card.locator("#register-email").is_visible() is False
        # The #687 email toggle is present on pricing as of #1188.
        assert (
            free_card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 1
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
        the email form is rendered expanded and no toggle is rendered.
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
        # No toggle, because there is nothing else to choose.
        assert (
            card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )

    def test_expanded_email_form_registers_user_successfully(
        self, django_server, page, django_db_blocker,
    ):
        """After expanding the email block, the form submits and redirects
        back to /courses/collapse-687-demo as an authenticated user."""
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            _configure_oauth("google")
        email = _new_email("collapse-signup")

        page.goto(
            f"{django_server}/courses/collapse-687-demo",
            wait_until="domcontentloaded",
        )
        card = page.locator('[data-testid="inline-register-card"]')
        card.locator(
            '[data-testid="inline-register-email-toggle"]',
        ).click()
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
