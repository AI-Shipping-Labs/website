"""Playwright E2E tests for course-scoped Studio enrollments (issue #293).

Covers the acceptance scenarios in the spec. Legacy ``/studio/enrollments/``
redirect-shim scenarios were removed in #421 along with the shims themselves.

1. Operator manages enrollments from within a course context.
2. Operator enrolls a user from the course-scoped page.
3. Operator unenrolls a user and the row reflects it.
6. Sidebar no longer advertises a top-level Enrollments tab.
7. Cross-course safety on unenroll (404 on mismatched course id).
8. Non-staff user cannot reach the course-scoped page (403 / login redirect).

Usage:
    uv run pytest playwright_tests/test_studio_course_enrollments.py -v
"""

import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
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
from playwright_tests.conftest import (
    expand_studio_sidebar_section as _expand_studio_sidebar_section,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _clear_enrollment_state():
    """Wipe courses + enrollments so each scenario starts from zero."""
    from content.models import Course, Enrollment, UserCourseProgress

    Enrollment.objects.all().delete()
    UserCourseProgress.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_course(title="Intro to AI", slug="intro-to-ai", status="published"):
    from content.models import Course, Module, Unit

    course = Course.objects.create(
        title=title,
        slug=slug,
        description=f"{title} description",
        status=status,
    )
    module = Module.objects.create(
        course=course, title="Module 1", slug=f"{slug}-m1", sort_order=0,
    )
    Unit.objects.create(
        module=module, title="Unit 1", slug=f"{slug}-u1", sort_order=0,
    )
    connection.close()
    return course


def _enroll(user, course, source="manual"):
    from content.models import Enrollment

    enrollment = Enrollment.objects.create(user=user, course=course, source=source)
    connection.close()
    return enrollment


# ---------------------------------------------------------------------------
# Scenario 1: Operator manages enrollments from within a course context
# ---------------------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario1OperatorManagesEnrollmentsInCourseContext:
    def test_course_edit_links_to_scoped_enrollments_page(
        self, django_server, browser,
    ):
        _clear_enrollment_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        alice = _create_user("alice@test.com", tier_slug="main")
        bob = _create_user("bob@test.com", tier_slug="main")
        course = _create_course()
        _enroll(alice, course)
        _enroll(bob, course)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Step 1: course list -> click into the course
        page.goto(
            f"{django_server}/studio/courses/", wait_until="domcontentloaded",
        )
        edit_link = page.locator(
            f'a[href*="/studio/courses/{course.pk}/edit"]',
        ).first
        edit_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Step 2: there's a Manage Enrollments button
        manage_btn = page.locator('a:has-text("Manage Enrollments")')
        assert manage_btn.count() == 1
        manage_btn.first.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.url.endswith(
            f"/studio/courses/{course.pk}/enrollments/",
        )

        # Breadcrumbs: Courses > Intro to AI > Enrollments
        breadcrumbs = page.locator(
            'a:has-text("Courses"), span:has-text("Enrollments")',
        )
        assert breadcrumbs.count() >= 2
        body = page.content()
        assert "Intro to AI" in body
        assert "Enrollments" in body

        # Both enrollments are visible
        assert "alice@test.com" in body
        assert "bob@test.com" in body

        # Exactly two enrollment rows
        rows = page.locator('[data-testid="enrollment-row"]')
        assert rows.count() == 2

        # No course-selection dropdown on the page
        assert page.locator('select[name="course"]').count() == 0
        assert page.locator('select[name="course_id"]').count() == 0


# ---------------------------------------------------------------------------
# Scenario 2: Operator enrolls a user from the course-scoped page
# ---------------------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario2OperatorEnrollsUser:
    def test_enrolling_a_user_creates_admin_source_enrollment(
        self, django_server, browser,
    ):
        _clear_enrollment_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _create_user("carol@test.com", tier_slug="main")
        course_a = _create_course("Intro to AI", slug="intro-to-ai")
        course_b = _create_course("Other Course", slug="other-course")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(
            f"{django_server}/studio/courses/{course_a.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        page.fill('input[name="email"]', "carol@test.com")
        page.click('button:has-text("Enroll user")')
        page.wait_for_load_state("domcontentloaded")

        # Lands back on the same page
        assert page.url.endswith(
            f"/studio/courses/{course_a.pk}/enrollments/",
        )
        body = page.content()
        # Success message
        assert "Enrolled carol@test.com" in body
        assert "Intro to AI" in body
        # Carol now appears in the table with the Admin source badge
        assert "carol@test.com" in body
        assert "Admin" in body

        # Cross-course isolation: course B should not list Carol.
        page.goto(
            f"{django_server}/studio/courses/{course_b.pk}/enrollments/",
            wait_until="domcontentloaded",
        )
        body_b = page.content()
        assert "carol@test.com" not in body_b


# ---------------------------------------------------------------------------
# Scenario 3: Operator unenrolls a user and the row reflects it
# ---------------------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario3OperatorUnenrollsUser:
    def test_unenroll_hides_row_in_active_filter_and_shows_with_all(
        self, django_server, browser,
    ):
        _clear_enrollment_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        dave = _create_user("dave@test.com", tier_slug="main")
        course = _create_course()
        _enroll(dave, course)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Auto-confirm the JS confirm() dialog used by the unenroll form.
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        # Click Unenroll on dave's row
        page.locator('[data-testid="unenroll-row-btn"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        # Lands back on the same page with a success message
        assert page.url.endswith(
            f"/studio/courses/{course.pk}/enrollments/",
        )
        body = page.content()
        assert "Unenrolled dave@test.com" in body

        # Default ?status=active hides dave's row
        rows = page.locator('[data-testid="enrollment-row"]')
        assert rows.count() == 0

        # Switch to "Including unenrolled" -> the row reappears with no
        # Unenroll button.
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.select_option('select[name="status"]', "all")
        # Sanity: we landed on the ?status=all URL.
        assert "status=all" in page.url
        rows = page.locator('[data-testid="enrollment-row"]')
        assert rows.count() == 1
        body = page.content()
        assert "dave@test.com" in body
        # No Unenroll button when status filter shows the unenrolled row
        unenroll_btns = page.locator('[data-testid="unenroll-row-btn"]')
        assert unenroll_btns.count() == 0


# ---------------------------------------------------------------------------
# Scenario 6: Sidebar no longer advertises a top-level Enrollments tab
# ---------------------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario6SidebarNoTopLevelEnrollments:
    def test_sidebar_omits_enrollments_link(self, django_server, browser):
        _clear_enrollment_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        course = _create_course()

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        page.goto(f"{django_server}/studio/", wait_until="domcontentloaded")

        # The sidebar still has the other Content links
        _expand_studio_sidebar_section(page, "content")
        sidebar = page.locator("#studio-sidebar")
        sidebar_text = sidebar.inner_text()
        assert "Courses" in sidebar_text
        assert "Articles" in sidebar_text

        # No top-level Enrollments link
        enrollments_links = sidebar.locator(
            'a[href="/studio/enrollments/"]',
        )
        assert enrollments_links.count() == 0

        # The course edit page exposes Manage Enrollments instead.
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        manage = page.locator('a:has-text("Manage Enrollments")')
        assert manage.count() == 1


# ---------------------------------------------------------------------------
# Scenario 7: Cross-course safety on unenroll
# ---------------------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario7CrossCourseUnenrollSafety:
    def test_unenroll_under_wrong_course_returns_404(
        self, django_server, browser,
    ):
        _clear_enrollment_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        eve = _create_user("eve@test.com", tier_slug="main")
        course_a = _create_course("Course A", slug="course-a")
        course_b = _create_course("Course B", slug="course-b")
        enrollment_a = _enroll(eve, course_a)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Hit the URL directly to ensure the server enforces the constraint
        # — POST via fetch from a primed page so the CSRF cookie is present.
        page.goto(
            f"{django_server}/studio/courses/{course_a.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        # Read CSRF token rendered by the form
        result = page.evaluate(
            """async ({courseId, enrollmentId}) => {
                const csrfInput = document.querySelector(
                    'input[name="csrfmiddlewaretoken"]',
                );
                const csrfToken = csrfInput ? csrfInput.value : '';
                const url = `/studio/courses/${courseId}/enrollments/${enrollmentId}/unenroll`;
                const resp = await fetch(url, {
                    method: 'POST',
                    headers: {'X-CSRFToken': csrfToken},
                    body: '',
                });
                return resp.status;
            }""",
            {"courseId": course_b.pk, "enrollmentId": enrollment_a.pk},
        )

        assert result == 404, f"expected 404, got {result}"

        # Eve's enrollment in course A is unchanged
        from content.models import Enrollment
        enrollment_a.refresh_from_db()
        assert enrollment_a.unenrolled_at is None
        assert Enrollment.objects.filter(
            user=eve, course=course_a, unenrolled_at__isnull=True,
        ).exists()
        connection.close()


# ---------------------------------------------------------------------------
# Scenario 8: Non-staff user cannot reach the course-scoped page
# ---------------------------------------------------------------------------

@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario8NonStaffCannotReachPage:
    def test_non_staff_gets_403_anonymous_redirected_to_login(
        self, django_server, browser,
    ):
        _clear_enrollment_state()
        _ensure_tiers()
        _create_user("regular@test.com", tier_slug="main")
        course = _create_course()

        # Non-staff user -> 403
        context = _auth_context(browser, "regular@test.com")
        page = context.new_page()

        response = page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )
        assert response.status == 403, (
            f"expected 403 for non-staff, got {response.status} url={page.url}"
        )

        # Anonymous user -> redirected to /accounts/login/
        anon_context = browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        anon_page = anon_context.new_page()
        anon_page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )
        assert "/accounts/login" in anon_page.url
