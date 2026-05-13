"""Playwright coverage for sign-out return context (issue #519).

The Log out link in the header propagates the current page as ``?next=``
so a user can sign out and stay on the same public detail page to
inspect its anonymous variant. Sign-out from member-only / admin-only
surfaces (``/account``, ``/accounts``, ``/studio``, ``/admin``,
``/notifications``) always lands on ``/`` instead.
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


def _reset_fixtures():
    from content.models import Article, Course, Module, Unit
    from content.models.workshop import Workshop
    from events.models import Event

    Unit.objects.filter(module__course__slug__startswith="logout-ctx").delete()
    Module.objects.filter(course__slug__startswith="logout-ctx").delete()
    Course.objects.filter(slug__startswith="logout-ctx").delete()
    Event.objects.filter(slug__startswith="logout-ctx").delete()
    Workshop.objects.filter(slug__startswith="logout-ctx").delete()
    Article.objects.filter(slug__startswith="logout-ctx").delete()
    connection.close()


def _seed_event(slug="logout-ctx-event"):
    from events.models import Event

    event = Event.objects.create(
        title="Logout Context Event",
        slug=slug,
        description="Event used to verify post-logout return context.",
        start_datetime=timezone.now() + timedelta(days=7),
        status="upcoming",
        required_level=LEVEL_OPEN,
        published=True,
    )
    connection.close()
    return event


def _seed_course(slug="logout-ctx-course"):
    from content.models import Course

    course = Course.objects.create(
        title="Logout Context Course",
        slug=slug,
        status="published",
        required_level=LEVEL_OPEN,
        description="Course used to verify post-logout return context.",
    )
    connection.close()
    return course


def _seed_workshop(slug="logout-ctx-workshop"):
    from content.models.workshop import Workshop

    workshop = Workshop.objects.create(
        title="Logout Context Workshop",
        slug=slug,
        status="published",
        landing_required_level=LEVEL_OPEN,
        pages_required_level=LEVEL_OPEN,
        recording_required_level=LEVEL_OPEN,
        description="Workshop used to verify post-logout return context.",
        date=timezone.now().date(),
    )
    connection.close()
    return workshop


def _seed_article(slug="logout-ctx-article"):
    from content.models import Article

    article = Article.objects.create(
        title="Logout Context Article",
        slug=slug,
        status="published",
        published_at=timezone.now() - timedelta(days=1),
        content_markdown="A free, anonymously readable article.",
        required_level=LEVEL_OPEN,
        date=timezone.now().date(),
    )
    connection.close()
    return article


def _logout_via_header(page):
    """Click the Log out link in the desktop account dropdown."""
    page.click('[data-testid="account-menu-trigger"]')
    page.wait_for_selector(
        '[data-testid="account-menu-dropdown"]:not(.hidden)'
    )
    page.click('[data-testid="account-menu-dropdown"] a:has-text("Log out")')


@pytest.mark.django_db(transaction=True)
class TestLogoutReturnContext:
    def test_logout_from_event_detail_stays_on_event(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)
            _seed_event()

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/events/logout-ctx-event",
            wait_until="domcontentloaded",
        )
        _logout_via_header(page)
        page.wait_for_url(
            f"{django_server}/events/logout-ctx-event", timeout=10000
        )
        # The header now shows the anonymous "Sign in" button, not the
        # account avatar trigger.
        assert page.locator('a:has-text("Sign in")').count() >= 1
        assert page.locator('[data-testid="account-menu-trigger"]').count() == 0
        # Event title still rendered.
        assert "Logout Context Event" in page.content()
        context.close()

    def test_logout_from_course_stays_on_course(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)
            _seed_course()

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/courses/logout-ctx-course",
            wait_until="domcontentloaded",
        )
        _logout_via_header(page)
        page.wait_for_url(
            f"{django_server}/courses/logout-ctx-course", timeout=10000
        )
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()

    def test_logout_from_workshop_stays_on_workshop(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)
            _seed_workshop()

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/workshops/logout-ctx-workshop",
            wait_until="domcontentloaded",
        )
        _logout_via_header(page)
        page.wait_for_url(
            f"{django_server}/workshops/logout-ctx-workshop", timeout=10000
        )
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()

    def test_logout_from_blog_article_stays_on_article(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)
            _seed_article()

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/blog/logout-ctx-article",
            wait_until="domcontentloaded",
        )
        _logout_via_header(page)
        page.wait_for_url(
            f"{django_server}/blog/logout-ctx-article", timeout=10000
        )
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()

    def test_logout_from_account_page_redirects_to_homepage(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/account/",
            wait_until="domcontentloaded",
        )
        _logout_via_header(page)
        # ``/account`` is on the exclusion list — sign-out goes home.
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()

    def test_logout_from_studio_redirects_to_homepage(
        self, django_server, browser, django_db_blocker
    ):
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_staff_user("logout-staff@test.com")

        context = auth_context(browser, "logout-staff@test.com")
        page = context.new_page()
        # Studio templates do not embed the public ``includes/header.html``,
        # so we drive sign-out by hitting the logout URL with a tampered
        # ``next`` to verify the server-side exclusion. The visible Studio
        # exit path is through the public header which is enforced at the
        # server-side level.
        page.goto(
            f"{django_server}/accounts/logout/?next=/studio/articles/",
            wait_until="domcontentloaded",
        )
        # Exclusion list rejects /studio/ — user lands on /, not /studio.
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()

    def test_logout_with_external_next_lands_on_homepage(
        self, django_server, browser, django_db_blocker
    ):
        """A tampered URL pointing off-site is sanitized and falls back to /."""
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/accounts/logout/?next=https://evil.example.com/phish",
            wait_until="domcontentloaded",
        )
        page.wait_for_url(f"{django_server}/", timeout=10000)
        # User is signed out — header now shows Sign in button.
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()

    def test_logout_from_homepage_stays_on_homepage(
        self, django_server, browser, django_db_blocker
    ):
        """Sign-out from ``/`` keeps the user on ``/``; renders anonymous home."""
        with django_db_blocker.unblock():
            _reset_fixtures()
            create_user("logout-ctx@test.com", password=DEFAULT_PASSWORD)

        context = auth_context(browser, "logout-ctx@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        _logout_via_header(page)
        page.wait_for_url(f"{django_server}/", timeout=10000)
        assert page.locator('a:has-text("Sign in")').count() >= 1
        context.close()
