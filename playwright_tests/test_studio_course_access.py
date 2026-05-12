"""Playwright E2E tests for issue #492: Studio course access management.

Covers the acceptance scenarios:
- Desktop: search/select a user, grant access, verify row appears, revoke,
  verify success message.
- Mobile: access list renders without horizontal overflow and Revoke is
  tappable (>= 44px).
- Mobile: enrollments list renders without horizontal overflow and Unenroll
  is tappable (>= 44px).

Usage:
    uv run pytest playwright_tests/test_studio_course_access.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_session_for_user as _create_session_for_user,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)


def _mobile_auth_context(browser, email):
    """Create a 390x844 mobile auth context for the given user."""
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport={"width": 390, "height": 844})
    context.add_cookies([
        {
            "name": "sessionid",
            "value": session_key,
            "domain": "127.0.0.1",
            "path": "/",
        },
        {
            "name": "csrftoken",
            "value": "e2e-test-csrf-token-value",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_state():
    from content.models import Course, CourseAccess, Enrollment

    CourseAccess.objects.all().delete()
    Enrollment.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_course(title="Access Course", slug="access-course"):
    from content.models import Course

    course = Course.objects.create(
        title=title, slug=slug, status="published",
    )
    connection.close()
    return course


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestDesktopGrantSearchAndRevoke:
    def test_search_select_grant_then_revoke(self, django_server, browser):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        target = _create_user("findme@test.com", tier_slug="main")
        course = _create_course()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )

        # Type a fragment to trigger autocomplete
        page.fill('input[data-testid="grant-email-input"]', "findme")

        # Wait for the suggestion to appear
        suggestion = page.locator('[data-testid="grant-suggestion"]').first
        suggestion.wait_for(state="visible", timeout=5000)
        # The suggestion should reference the target user's id
        assert suggestion.get_attribute("data-user-id") == str(target.pk)
        suggestion.click()

        # Hidden user_id should now be set
        assert page.locator('[data-testid="grant-user-id-input"]').input_value() == str(
            target.pk,
        )

        page.click('[data-testid="grant-submit-btn"]')
        page.wait_for_load_state("domcontentloaded")

        # Access row should appear with the target email
        body = page.content()
        assert "findme@test.com" in body
        assert "Access granted to findme@test.com" in body

        # Revoke from desktop button
        revoke = page.locator('[data-testid="revoke-btn"]').first
        revoke.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Access revoked for findme@test.com" in body


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestMobileAccessListResponsive:
    def test_mobile_no_horizontal_overflow_and_revoke_tappable(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        admin = _create_staff_user("admin@test.com")
        target = _create_user("mobile@test.com", tier_slug="main")
        course = _create_course()

        from content.models import CourseAccess
        CourseAccess.objects.create(
            user=target, course=course, access_type="granted", granted_by=admin,
        )
        connection.close()

        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            storage_state=_auth_context(browser, "admin@test.com").storage_state(),
        )
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )

        # Mobile cards block visible (md:hidden hides on >= md)
        cards = page.locator('[data-testid="access-card"]')
        assert cards.count() == 1

        # No horizontal overflow on the document body.
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (
            f"Horizontal overflow detected: scrollWidth={scroll_width} clientWidth={client_width}"
        )

        # Mobile revoke button is visible and tall enough to tap
        revoke = page.locator('[data-testid="revoke-btn-mobile"]').first
        assert revoke.is_visible()
        box = revoke.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"Mobile revoke height {box['height']} < 44px"

        # Click revoke and verify success
        revoke.click()
        page.wait_for_load_state("domcontentloaded")
        assert "Access revoked for mobile@test.com" in page.content()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestMobileEnrollmentsResponsive:
    def test_mobile_no_overflow_and_unenroll_tappable(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        student = _create_user("student@test.com", tier_slug="main")
        course = _create_course()

        from content.models import Enrollment
        Enrollment.objects.create(user=student, course=course, source="admin")
        connection.close()

        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            storage_state=_auth_context(browser, "admin@test.com").storage_state(),
        )
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        # Mobile cards visible
        cards = page.locator('[data-testid="enrollment-card"]')
        assert cards.count() == 1

        # No horizontal overflow on the document
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (
            f"Horizontal overflow: scrollWidth={scroll_width} clientWidth={client_width}"
        )

        # Mobile unenroll button is visible and >=44px tall
        unenroll = page.locator(
            '[data-testid="unenroll-row-btn-mobile"]',
        ).first
        assert unenroll.is_visible()
        box = unenroll.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"Mobile unenroll height {box['height']} < 44px"

        # Click and verify success message
        unenroll.click()
        page.wait_for_load_state("domcontentloaded")
        assert "Unenrolled student@test.com" in page.content()
