"""Playwright coverage for the header sign-in return-context (issue #594).

The header's ``Sign in`` link captures the current path as ``?next=``
so a visitor who lands on a deep page (blog post, event, course unit,
workshop) and clicks Sign in returns to that page after auth instead
of always being dropped on ``/``. The ``next`` value is sanitized via
the same helpers used by the logout flow, so absolute and protocol-
relative URLs are rejected (open-redirect protection).

Companion scenarios (5-9) re-confirm the symmetric sign-out behavior
already shipped in #519: sign-out from a public page stays on that
page, sign-out from a member-only or staff-only page redirects to ``/``.
"""

import os
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from content.access import LEVEL_OPEN
from playwright_tests.conftest import (
    DEFAULT_PASSWORD,
    auth_context,
    create_staff_user,
    create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _reset_fixtures():
    from content.models import Article, Course, Module, Unit
    from content.models.workshop import Workshop
    from events.models import Event

    Unit.objects.filter(module__course__slug__startswith="login-ctx").delete()
    Module.objects.filter(course__slug__startswith="login-ctx").delete()
    Course.objects.filter(slug__startswith="login-ctx").delete()
    Event.objects.filter(slug__startswith="login-ctx").delete()
    Workshop.objects.filter(slug__startswith="login-ctx").delete()
    Article.objects.filter(slug__startswith="login-ctx").delete()
    connection.close()


def _seed_article(slug="login-ctx-getting-started-with-llms"):
    from content.models import Article

    article = Article.objects.create(
        title="Getting Started with LLMs",
        slug=slug,
        status="published",
        published_at=timezone.now() - timedelta(days=1),
        content_markdown=(
            "An anonymously readable article used to verify the post-login "
            "return context."
        ),
        required_level=LEVEL_OPEN,
        date=timezone.now().date(),
    )
    connection.close()
    return article


def _seed_event(slug="login-ctx-event"):
    from events.models import Event

    event = Event.objects.create(
        title="Login Context Event",
        slug=slug,
        description="Event used to verify post-login return context.",
        start_datetime=timezone.now() + timedelta(days=7),
        status="upcoming",
        required_level=LEVEL_OPEN,
        published=True,
    )
    connection.close()
    return event


def _seed_course_with_unit(
    slug="login-ctx-intro-to-ai", unit_slug="welcome", module_slug="module-1"
):
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title="Intro to AI",
        slug=slug,
        status="published",
        required_level=LEVEL_OPEN,
        default_unit_required_level=LEVEL_OPEN,
        description="Course used to verify mobile-vs-desktop sign-in parity.",
    )
    module = Module.objects.create(
        course=course,
        title="Module 1",
        slug=module_slug,
        sort_order=1,
    )
    unit = Unit.objects.create(
        module=module,
        title="Welcome",
        slug=unit_slug,
        sort_order=1,
        body="Public welcome lesson body.",
    )
    connection.close()
    return course, unit


def _login(page, email, password=DEFAULT_PASSWORD):
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.click("#login-submit")


def _click_desktop_sign_in(page):
    """Click the desktop header Sign-in button (visible on >= md viewports)."""
    # The desktop Sign-in lives in the wrapper hidden below md breakpoint
    # (``hidden md:flex``) and the mobile one lives in ``#mobile-menu``.
    # Use the link inside the desktop wrapper to disambiguate.
    page.click(
        '[data-testid="desktop-primary-nav"] ~ div a:has-text("Sign in"), '
        'header nav > div.hidden.md\\:flex a:has-text("Sign in")'
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestLoginReturnContext:
    # --- Scenario 1: reader returns to the blog post they were reading

    def test_blog_sign_in_returns_to_same_blog_post(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )
            article = _seed_article()

        page.goto(
            f"{django_server}/blog/{article.slug}",
            wait_until="domcontentloaded",
        )
        # Click the desktop Sign-in link in the header. Default Playwright
        # viewport (1280x720) shows the desktop button; the mobile button
        # is rendered but hidden via ``hidden`` class on the wrapper.
        page.click(
            'header a.inline-flex:has-text("Sign in")'
        )
        page.wait_for_url(
            f"{django_server}/accounts/login/?next=%2Fblog%2F{article.slug}",
            timeout=10000,
        )
        _login(page, "free@test.com")
        page.wait_for_url(
            f"{django_server}/blog/{article.slug}", timeout=10000
        )
        # The header now shows the account menu, not the Sign-in button.
        assert page.locator(
            '[data-testid="account-menu-trigger"]'
        ).count() == 1

    # --- Scenario 2: signed-out reader stays on the public page and re-enters

    def test_signed_out_reader_can_sign_back_in_and_return(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )
            article = _seed_article()

        # Step 1: signed-in user opens the article and signs out via the
        # header account menu.
        context = auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/{article.slug}",
            wait_until="domcontentloaded",
        )
        page.click('[data-testid="account-menu-trigger"]')
        page.wait_for_selector(
            '[data-testid="account-menu-dropdown"]:not(.hidden)'
        )
        page.click(
            '[data-testid="account-menu-dropdown"] a:has-text("Log out")'
        )
        page.wait_for_url(
            f"{django_server}/blog/{article.slug}", timeout=10000
        )
        # Now anonymous; header shows Sign-in button.
        sign_in = page.locator('header a.inline-flex:has-text("Sign in")')
        assert sign_in.count() == 1
        href = sign_in.first.get_attribute("href")
        assert href == f"/accounts/login/?next=%2Fblog%2F{article.slug}", (
            f"Expected captured next, got href={href!r}"
        )

        # Step 2: click Sign-in and complete login.
        sign_in.first.click()
        page.wait_for_url(
            f"{django_server}/accounts/login/?next=%2Fblog%2F{article.slug}",
            timeout=10000,
        )
        _login(page, "free@test.com")
        page.wait_for_url(
            f"{django_server}/blog/{article.slug}", timeout=10000
        )
        context.close()

    # --- Scenario 3: open-redirect via tampered absolute next is rejected

    def test_login_with_external_next_lands_on_homepage(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )

        page.goto(
            f"{django_server}/accounts/login/?next=https://evil.example.com/phish",
            wait_until="domcontentloaded",
        )
        _login(page, "free@test.com")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        # The landing URL is the local homepage, NOT the off-site target.
        assert page.url == f"{django_server}/"

    # --- Scenario 4: open-redirect via protocol-relative next is rejected

    def test_login_with_protocol_relative_next_lands_on_homepage(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )

        page.goto(
            f"{django_server}/accounts/login/?next=//evil.example.com/phish",
            wait_until="domcontentloaded",
        )
        _login(page, "free@test.com")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.url == f"{django_server}/"

    # --- Scenario 5: sign-in from the home page goes to the home page

    def test_sign_in_from_homepage_has_no_next_and_lands_on_home(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )

        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        sign_in = page.locator('header a.inline-flex:has-text("Sign in")')
        assert sign_in.count() == 1
        href = sign_in.first.get_attribute("href")
        # Bare login URL — no ``?next=`` for the homepage.
        assert href == "/accounts/login/", (
            f"Homepage Sign-in should have no ?next; got href={href!r}"
        )
        sign_in.first.click()
        page.wait_for_url(f"{django_server}/accounts/login/", timeout=10000)
        _login(page, "free@test.com")
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.url == f"{django_server}/"

    # --- Scenario 6: sign-in entry point on a member-only page

    def test_sign_in_link_on_login_page_does_not_loop_back(
        self, django_server, page, django_db_blocker
    ):
        """Anonymous visitor who lands on ``/accounts/login/`` directly
        must not see a header Sign-in link that points back at login.
        ``/accounts`` is on the exclusion list so the helper omits
        ``?next=``."""
        with django_db_blocker.unblock():
            _reset_fixtures()

        page.goto(
            f"{django_server}/accounts/login/",
            wait_until="domcontentloaded",
        )
        # Find the header Sign-in button (excludes the login form).
        header_sign_in = page.locator(
            'header a:has-text("Sign in")'
        )
        # The header Sign-in href must be the bare login URL, never one
        # carrying ``?next=/accounts/...`` which would create a loop.
        for i in range(header_sign_in.count()):
            href = header_sign_in.nth(i).get_attribute("href") or ""
            assert "next=%2Faccounts" not in href, (
                f"Header Sign-in #{i} loops back to auth: {href!r}"
            )

    # --- Scenario 7: sign-out from an event detail page stays on the event

    def test_logout_from_event_detail_stays_on_event(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )
            event = _seed_event()

        context = auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/{event.slug}",
            wait_until="domcontentloaded",
        )
        page.click('[data-testid="account-menu-trigger"]')
        page.wait_for_selector(
            '[data-testid="account-menu-dropdown"]:not(.hidden)'
        )
        page.click(
            '[data-testid="account-menu-dropdown"] a:has-text("Log out")'
        )
        page.wait_for_url(
            f"{django_server}/events/{event.slug}", timeout=10000
        )
        # User is now anonymous; header shows Sign-in button.
        assert page.locator(
            'header a.inline-flex:has-text("Sign in")'
        ).count() == 1
        context.close()

    # --- Scenario 8: sign-out from /account/ redirects home

    def test_logout_from_account_page_redirects_to_homepage(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user(
                "free@test.com",
                password=DEFAULT_PASSWORD,
                email_verified=True,
            )

        context = auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/account/", wait_until="domcontentloaded"
        )
        page.click('[data-testid="account-menu-trigger"]')
        page.wait_for_selector(
            '[data-testid="account-menu-dropdown"]:not(.hidden)'
        )
        page.click(
            '[data-testid="account-menu-dropdown"] a:has-text("Log out")'
        )
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.url == f"{django_server}/"
        context.close()

    # --- Scenario 9: sign-out from Studio redirects home

    def test_logout_from_studio_redirects_to_homepage(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_staff_user("login-ctx-staff@test.com")

        context = auth_context(browser, "login-ctx-staff@test.com")
        page = context.new_page()
        # Studio templates do not embed the public ``includes/header.html``
        # so sign-out from a Studio page is exercised by hitting the
        # logout URL with a tampered ``next`` to verify the server-side
        # exclusion list rejects ``/studio`` (issue #519 re-confirm).
        page.goto(
            f"{django_server}/accounts/logout/?next=/studio/articles/",
            wait_until="domcontentloaded",
        )
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.url == f"{django_server}/"
        context.close()

    # --- Scenario 10: mobile and desktop Sign-in buttons capture same next

    def test_mobile_and_desktop_sign_in_buttons_carry_same_next(
        self, django_server, page, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            _seed_course_with_unit()

        target_path = "/courses/login-ctx-intro-to-ai/module-1/welcome"
        expected_href = (
            "/accounts/login/"
            "?next=%2Fcourses%2Flogin-ctx-intro-to-ai%2Fmodule-1%2Fwelcome"
        )
        page.goto(
            f"{django_server}{target_path}",
            wait_until="domcontentloaded",
        )
        # Both desktop and mobile Sign-in links live in the rendered
        # DOM regardless of viewport (CSS toggles visibility). Read the
        # href attribute from each rendered occurrence.
        sign_in_links = page.locator('header a:has-text("Sign in")')
        # Header renders Sign-in twice for anonymous users (desktop + mobile).
        assert sign_in_links.count() == 2, (
            f"Expected 2 Sign-in links in the header, got "
            f"{sign_in_links.count()}"
        )
        hrefs = [
            sign_in_links.nth(i).get_attribute("href")
            for i in range(sign_in_links.count())
        ]
        assert hrefs[0] == expected_href, (
            f"Desktop Sign-in href mismatch: {hrefs[0]!r}"
        )
        assert hrefs[1] == expected_href, (
            f"Mobile Sign-in href mismatch: {hrefs[1]!r}"
        )
        assert hrefs[0] == hrefs[1], (
            f"Desktop and mobile Sign-in hrefs diverged: "
            f"{hrefs[0]!r} vs {hrefs[1]!r}"
        )
