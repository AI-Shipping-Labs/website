"""Playwright coverage for inline registration card surfaces (#652).

Three public surfaces replace the "Sign Up Free" / "Create an account"
button with an inline register form:

  - /courses/<slug>           (free-course free-anon CTA)
  - /workshops/<slug>         (pages-level paywall on a registered wall)
  - /pricing                  (free-tier card)

Each scenario asserts a specific user-visible behavior, not an
implementation detail. Standalone /accounts/register/ flow is covered
by ``playwright_tests/test_auth_shared_components.py`` and is unchanged
by this work.
"""

import os
import uuid
from datetime import date

import pytest

from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_user,
    ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _new_email(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}@test.com"


def _reset_state():
    """Clear all fixtures the inline-register suite touches.

    Caller must hold django_db_blocker.unblock() — we close the
    connection at the end so the Django server thread can read.
    """
    from allauth.socialaccount.models import SocialApp
    from django.db import connection

    from content.models import Course, Module, Unit, Workshop, WorkshopPage

    Unit.objects.filter(module__course__slug__startswith="inline-652").delete()
    Module.objects.filter(course__slug__startswith="inline-652").delete()
    Course.objects.filter(slug__startswith="inline-652").delete()
    WorkshopPage.objects.filter(
        workshop__slug__startswith="inline-652",
    ).delete()
    Workshop.objects.filter(slug__startswith="inline-652").delete()
    SocialApp.objects.all().delete()
    connection.close()


def _seed_free_course(slug="inline-652-demo"):
    from django.db import connection

    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Demo Course",
        slug=slug,
        status="published",
        required_level=0,
        description="A free course used by inline-register E2E.",
    )
    module = Module.objects.create(
        course=course, title="Module", slug="module", sort_order=1,
    )
    Unit.objects.create(
        module=module, title="Lesson", slug="lesson", sort_order=1,
    )
    connection.close()
    return course, slug


def _seed_paid_course(slug="inline-652-premium"):
    from django.db import connection

    from content.models import Course, Module

    course = Course.objects.create(
        title="Premium Course",
        slug=slug,
        status="published",
        required_level=30,
        description="A premium-gated course used by inline-register E2E.",
    )
    Module.objects.create(
        course=course, title="Module", slug="module", sort_order=1,
    )
    connection.close()
    return course, slug


def _seed_anon_pages_workshop(slug="inline-652-ws"):
    from django.db import connection

    from content.models import Workshop, WorkshopPage

    workshop = Workshop.objects.create(
        slug=slug,
        title="Anon Pages Workshop",
        status="published",
        date=date(2026, 4, 21),
        landing_required_level=0,
        pages_required_level=5,
        recording_required_level=20,
        description="Workshop used by inline-register E2E (registered wall).",
    )
    WorkshopPage.objects.create(
        workshop=workshop, slug="intro", title="Intro", sort_order=1,
        body="Tutorial body.",
    )
    connection.close()
    return workshop, slug


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
class TestInlineRegisterCard:
    """Scenarios pinned by the issue body (9 total).

    The tests do not exercise OAuth callbacks themselves — those flow
    through allauth and are covered by the HUMAN AC. We assert that
    the buttons render with the correct ``href`` (next= round-trip).
    """

    def test_anonymous_signs_up_directly_from_free_course_page(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
        email = _new_email("new-learner")

        page.goto(
            f"{django_server}/courses/inline-652-demo",
            wait_until="domcontentloaded",
        )
        # Inline register card renders, button no longer present.
        assert page.locator(
            "[data-testid='inline-register-card']"
        ).is_visible()
        assert page.locator("#register-email").is_visible()
        # Submit a valid registration.
        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")

        success = page.locator("#register-success")
        success.wait_for(state="visible")
        assert "Check your email" in success.inner_text()
        # The return-link points back to the originating course URL.
        return_link = success.locator("a")
        assert return_link.get_attribute("href") == "/courses/inline-652-demo"
        # No full-page redirect happened — we are still on the course page.
        assert page.url.endswith("/courses/inline-652-demo")
        # User exists, unverified.
        with django_db_blocker.unblock():
            from accounts.models import User

            user = User.objects.get(email=email)
            assert user.email_verified is False

    def test_duplicate_email_shows_inline_error_and_sign_in_link(
        self, django_server, page, django_db_blocker
    ):
        email = _new_email("existing-652")
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            create_user(email=email, password=DEFAULT_PASSWORD)

        page.goto(
            f"{django_server}/courses/inline-652-demo",
            wait_until="domcontentloaded",
        )
        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert "already exists" in error.inner_text()
        # The form is still on the same URL — no reload.
        assert page.url.endswith("/courses/inline-652-demo")
        # Click the inline card's Sign in link — must carry ?next=.
        login_link = page.locator(
            "[data-testid='inline-register-card'] #login-link"
        )
        assert (
            login_link.get_attribute("href")
            == "/accounts/login/?next=/courses/inline-652-demo"
        )

    def test_password_mismatch_does_not_post_to_api(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()

        register_calls = {"count": 0}

        def count_register(route):
            register_calls["count"] += 1
            route.continue_()

        page.route("**/api/register", count_register)
        page.goto(
            f"{django_server}/courses/inline-652-demo",
            wait_until="domcontentloaded",
        )
        page.fill("#register-email", _new_email("mismatch"))
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", "Different123!")
        page.click("#register-submit")

        error = page.locator("#register-error")
        error.wait_for(state="visible")
        assert error.inner_text() == "Passwords do not match"
        assert register_calls["count"] == 0

    def test_github_oauth_button_on_course_carries_next_url(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            _configure_oauth("github")

        page.goto(
            f"{django_server}/courses/inline-652-demo",
            wait_until="domcontentloaded",
        )
        github_button = page.get_by_role(
            "link", name="Sign up with GitHub", exact=True
        )
        assert github_button.is_visible()
        href = github_button.get_attribute("href")
        assert href is not None
        assert href.startswith("/accounts/github/login/")
        assert "next=" in href
        assert "courses/inline-652-demo" in href

    def test_google_oauth_button_on_pricing_carries_next_url(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _configure_oauth("google")

        page.goto(f"{django_server}/pricing", wait_until="domcontentloaded")
        google_button = page.get_by_role(
            "link", name="Sign up with Google", exact=True
        )
        assert google_button.is_visible()
        href = google_button.get_attribute("href")
        assert href is not None
        assert href.startswith("/accounts/google/login/")
        assert "next=" in href
        assert "pricing" in href

    def test_anonymous_signs_up_from_workshop_pages_paywall(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_anon_pages_workshop()
        email = _new_email("workshop-signup")

        page.goto(
            f"{django_server}/workshops/inline-652-ws",
            wait_until="domcontentloaded",
        )
        # Pages paywall renders the inline card, not the "Create a free
        # account" link.
        assert page.locator(
            "[data-testid='workshop-pages-paywall']"
        ).is_visible()
        assert page.locator(
            "[data-testid='inline-register-card']"
        ).is_visible()

        page.fill("#register-email", email)
        page.fill("#register-password", DEFAULT_PASSWORD)
        page.fill("#register-password-confirm", DEFAULT_PASSWORD)
        page.click("#register-submit")

        success = page.locator("#register-success")
        success.wait_for(state="visible")
        return_link = success.locator("a")
        assert return_link.get_attribute("href") == "/workshops/inline-652-ws"

    def test_logged_in_free_user_on_free_course_sees_no_inline_form(
        self, django_server, browser, django_db_blocker
    ):
        # Reuse the auth_context helper — create user, attach session.
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()
            create_user(
                "logged-in-652@test.com",
                tier_slug="free",
                password=DEFAULT_PASSWORD,
            )

        context = auth_context(browser, "logged-in-652@test.com")
        page = context.new_page()
        try:
            page.goto(
                f"{django_server}/courses/inline-652-demo",
                wait_until="domcontentloaded",
            )
            assert (
                page.locator("[data-testid='inline-register-card']").count()
                == 0
            )
            # The course content (syllabus) is accessible.
            assert "Lesson" in page.content()
        finally:
            context.close()

    def test_paid_course_anonymous_still_shows_view_pricing(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_paid_course()

        page.goto(
            f"{django_server}/courses/inline-652-premium",
            wait_until="domcontentloaded",
        )
        # "View Pricing" link points at /pricing; no inline form.
        view_pricing = page.get_by_role(
            "link", name="View Pricing", exact=False
        )
        assert view_pricing.first.is_visible()
        assert (
            page.locator("[data-testid='inline-register-card']").count() == 0
        )
        # Click it and confirm navigation.
        view_pricing.first.click()
        page.wait_for_url(f"{django_server}/pricing", timeout=10000)

    def test_oauth_buttons_hidden_when_no_provider_configured(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_state()
            ensure_tiers()
            _seed_free_course()

        page.goto(
            f"{django_server}/courses/inline-652-demo",
            wait_until="domcontentloaded",
        )
        # Inline card is visible (email + password form).
        assert page.locator(
            "[data-testid='inline-register-card']"
        ).is_visible()
        # No OAuth buttons or divider.
        assert (
            page.locator("[data-auth-oauth-divider]").count() == 0
        )
        assert (
            page.locator("[data-auth-oauth-providers]").count() == 0
        )
