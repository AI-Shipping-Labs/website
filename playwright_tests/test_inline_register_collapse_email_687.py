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
  - /pricing is unaffected — #654 disclosure still works, no #687 toggle
  - no-OAuth fallback renders the email form expanded (no dead-end)
  - the expanded email form still registers a user via /api/register
"""

import os
import uuid

import pytest

from playwright_tests.conftest import DEFAULT_PASSWORD, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


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

    def test_pricing_page_is_unaffected_by_collapse_email(
        self, django_server, page, django_db_blocker,
    ):
        """/pricing must keep its #654 compact OAuth disclosure with the
        email form rendered inline. No #687 toggle should appear there.
        """
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        free_card = page.locator('[data-tier-card="free"]')
        # Email input visible immediately — no collapse-email behavior.
        assert free_card.locator("#register-email").is_visible()
        # No #687 toggle anywhere on the pricing card.
        assert (
            free_card.locator(
                '[data-testid="inline-register-email-toggle"]',
            ).count() == 0
        )
        # The compact #654 OAuth toggle IS still present.
        assert (
            free_card.locator(
                '[data-testid="inline-register-oauth-toggle"]',
            ).count() == 1
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
        """After expanding the email block, the form still submits and
        renders the success message with the return link pointing back
        at /courses/collapse-687-demo."""
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

        success = card.locator("#register-success")
        success.wait_for(state="visible")
        # Non-empty success copy.
        message = success.inner_text()
        assert message.strip() != ""
        # Return link points back to the originating course.
        return_link = success.locator("a")
        assert (
            return_link.get_attribute("href")
            == "/courses/collapse-687-demo"
        )
        assert "Return to where you left off." in return_link.inner_text()
        # User row exists, unverified.
        with django_db_blocker.unblock():
            from accounts.models import User

            user = User.objects.get(email=email)
            assert user.email_verified is False
