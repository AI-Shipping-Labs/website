"""Playwright E2E tests for Studio course access and enrollment management.

Covers the acceptance scenarios:
- Desktop: search/select a user, grant access, verify row appears, revoke,
  verify success message.
- Issue #1542: each record has one responsive semantic row on desktop and
  mobile, with one tappable destructive action and shared empty states.

Usage:
    uv run pytest playwright_tests/test_studio_course_access.py -v
"""

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

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
from scripts.browser_journey_policy import browser_journey

SCREENSHOT_DIR = Path('.tmp/screenshots/issue-1542')


def _auth_context_at_width(browser, email, width):
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport={"width": width, "height": 844})
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
        {
            "name": "aslab_analytics_consent",
            "value": "denied",
            "domain": "127.0.0.1",
            "path": "/",
        },
    ])
    return context


def _capture(page, filename, *, full_page=True):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / filename, full_page=full_page)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _lookup_query(request):
    return parse_qs(urlparse(request.url).query).get("q", [""])[0]


def _fulfill_lookup(route, results):
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps({"results": results}),
    )


def _release_lookup(page, route, results):
    with page.expect_response(lambda response: response.url == route.request.url) as info:
        _fulfill_lookup(route, results)
    info.value.body()
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => "
        "requestAnimationFrame(resolve)))"
    )


def _expect_lookup_dismissed(page):
    suggestions = page.locator('[data-testid="grant-suggestions"]')
    expect(suggestions).to_be_hidden()
    expect(suggestions.locator("li")).to_have_count(0)


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


@pytest.mark.django_db(transaction=True)
class TestDesktopGrantSearchAndRevoke:
    def test_search_select_grant_then_revoke(self, django_server, browser):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        target = _create_user("findme@test.com", tier_slug="main")
        exact = _create_user("access@test.com", tier_slug="main")
        keyboard = _create_user("keyboard@test.com", tier_slug="main")
        course = _create_course()

        context = _auth_context_at_width(browser, "admin@test.com", 1280)
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        target_result = {"id": target.pk, "email": target.email, "name": "Target"}
        exact_result = {"id": exact.pk, "email": exact.email, "name": "Exact"}
        keyboard_result = {
            "id": keyboard.pk,
            "email": keyboard.email,
            "name": "Keyboard",
        }
        stale_result = {
            "id": 999999,
            "email": "stale-access@test.com",
            "name": "Stale Access",
        }
        hold_once = {"access@test.com", "older-access"}
        pending = {}

        def control_lookup(route):
            query = _lookup_query(route.request)
            if query in hold_once:
                hold_once.remove(query)
                pending[query] = route
                return
            results = {
                "findme": [target_result],
                "keyboard": [keyboard_result],
            }.get(query, [])
            _fulfill_lookup(route, results)

        page.route("**/studio/courses/*/access/users/search/**", control_lookup)

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )

        # Exact-email submission remains available after a delayed lookup
        # resolves post-blur; the late list cannot cover Grant Access.
        grant_input = page.locator('[data-testid="grant-email-input"]')
        grant_button = page.locator('[data-testid="grant-submit-btn"]')
        with page.expect_request(
            lambda request: _lookup_query(request) == "access@test.com"
        ):
            grant_input.fill("access@test.com")
        grant_input.press("Tab")
        expect(grant_button).to_be_focused()
        _release_lookup(page, pending["access@test.com"], [exact_result])
        _expect_lookup_dismissed(page)
        expect(page.locator('[data-testid="grant-user-id-input"]')).to_have_value("")
        grant_button.click()
        page.wait_for_load_state("domcontentloaded")
        assert "Access granted to access@test.com" in page.content()

        # Select a newer result while an older response is still pending.
        grant_input = page.locator('[data-testid="grant-email-input"]')
        with page.expect_request(
            lambda request: _lookup_query(request) == "older-access"
        ):
            grant_input.fill("older-access")
        with page.expect_response(
            lambda response: _lookup_query(response.request) == "findme"
        ):
            grant_input.fill("findme")

        # Wait for the suggestion to appear
        suggestion = page.locator('[data-testid="grant-suggestion"]').first
        suggestion.wait_for(state="visible", timeout=5000)
        # The suggestion should reference the target user's id
        assert suggestion.get_attribute("data-user-id") == str(target.pk)
        suggestion.click()
        _release_lookup(page, pending["older-access"], [stale_result])

        # Hidden user_id should now be set
        expect(grant_input).to_have_value("findme@test.com")
        expect(page.locator('[data-testid="grant-user-id-input"]')).to_have_value(
            str(target.pk)
        )
        _expect_lookup_dismissed(page)

        page.click('[data-testid="grant-submit-btn"]')
        page.wait_for_load_state("domcontentloaded")

        # Access row should appear with the target email
        body = page.content()
        assert "findme@test.com" in body
        assert "Access granted to findme@test.com" in body

        # Revoke from desktop button
        revoke = page.locator('[data-testid="access-row"]').filter(
            has_text="findme@test.com"
        ).locator('[data-testid="revoke-btn"]')
        revoke.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Access revoked for findme@test.com" in body

        # Keyboard selection uses the same form contract and keeps focus on
        # the combobox until the operator submits Grant Access.
        grant_input = page.locator('[data-testid="grant-email-input"]')
        with page.expect_response(
            lambda response: _lookup_query(response.request) == "keyboard"
        ):
            grant_input.fill("keyboard")
        keyboard_suggestion = page.locator('[data-testid="grant-suggestion"]').filter(
            has_text="keyboard@test.com"
        )
        expect(keyboard_suggestion).to_be_visible()
        grant_input.press("ArrowDown")
        grant_input.press("Enter")
        expect(grant_input).to_be_focused()
        expect(grant_input).to_have_value("keyboard@test.com")
        expect(page.locator('[data-testid="grant-user-id-input"]')).to_have_value(
            str(keyboard.pk)
        )
        _expect_lookup_dismissed(page)
        page.locator('[data-testid="grant-submit-btn"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "Access granted to keyboard@test.com" in page.content()


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

        context = _auth_context_at_width(browser, "admin@test.com", 390)
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )

        records = page.locator('[data-testid="access-records"]')
        assert records.get_by_text("mobile@test.com", exact=True).count() == 1
        assert records.locator('[data-testid="access-row"]').count() == 1

        # No horizontal overflow on the document body.
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (
            f"Horizontal overflow detected: scrollWidth={scroll_width} clientWidth={client_width}"
        )

        # Mobile revoke button is visible and tall enough to tap
        revoke = page.locator('[data-testid="revoke-btn"]')
        assert revoke.is_visible()
        box = revoke.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"Mobile revoke height {box['height']} < 44px"

        _capture(page, 'access-mobile.png')

        # Click the only revoke control and verify success.
        revoke.click()
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("Access revoked for mobile@test.com.", exact=True)).to_have_count(1)
        assert page.locator('[data-testid="access-records"]').count() == 0
        expect(page.get_by_test_id('studio-empty-state-fresh')).to_be_visible()


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

        other = _create_user("other@test.com", tier_slug="main")
        from content.models import Enrollment
        Enrollment.objects.create(user=other, course=course, source="admin")
        connection.close()

        context = _auth_context_at_width(browser, "admin@test.com", 390)
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        records = page.locator('[data-testid="enrollments-records"]')
        assert records.get_by_text("student@test.com", exact=True).count() == 1
        assert records.get_by_text("other@test.com", exact=True).count() == 1
        assert records.locator('[data-testid="enrollment-row"]').count() == 2

        # No horizontal overflow on the document
        scroll_width = page.evaluate("document.documentElement.scrollWidth")
        client_width = page.evaluate("document.documentElement.clientWidth")
        assert scroll_width <= client_width + 1, (
            f"Horizontal overflow: scrollWidth={scroll_width} clientWidth={client_width}"
        )

        # Mobile unenroll button is visible and >=44px tall
        unenroll = page.locator(
            '[data-testid="enrollment-row"]', has_text="student@test.com",
        ).get_by_test_id('unenroll-row-btn')
        assert unenroll.is_visible()
        box = unenroll.bounding_box()
        assert box is not None
        assert box["height"] >= 44, f"Mobile unenroll height {box['height']} < 44px"

        _capture(page, 'enrollments-mobile.png')

        # Click the only unenroll control and verify the active list updates.
        unenroll.click()
        page.wait_for_load_state("domcontentloaded")
        expect(
            page.get_by_text(
                'Unenrolled student@test.com from "Access Course".',
                exact=True,
            )
        ).to_have_count(1)
        records = page.locator('[data-testid="enrollments-records"]')
        assert records.get_by_text("student@test.com", exact=True).count() == 0
        assert records.get_by_text("other@test.com", exact=True).count() == 1


@pytest.mark.django_db(transaction=True)
class TestResponsiveCourseRecordLists:
    @browser_journey
    def test_desktop_enrollments_list_each_student_and_action_once(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        student = _create_user("student@test.com", tier_slug="main")
        other = _create_user("other@test.com", tier_slug="main")
        course = _create_course(title="Shipping AI", slug="shipping-ai")

        from content.models import Enrollment
        Enrollment.objects.create(user=student, course=course, source="admin")
        Enrollment.objects.create(user=other, course=course, source="admin")
        connection.close()

        context = _auth_context_at_width(browser, "admin@test.com", 1280)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        records = page.get_by_test_id('enrollments-records')
        assert records.get_by_text('student@test.com', exact=True).count() == 1
        assert records.get_by_text('other@test.com', exact=True).count() == 1
        student_row = records.get_by_test_id('enrollment-row').filter(
            has_text='student@test.com'
        )
        assert student_row.get_by_test_id('unenroll-row-btn').count() == 1
        assert records.locator('form[data-confirm]').count() == 2
        _capture(page, 'enrollments-desktop.png')

    @browser_journey
    def test_desktop_unenroll_has_no_hidden_duplicate_form(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        student = _create_user("student@test.com", tier_slug="main")
        course = _create_course(title="Shipping AI", slug="shipping-ai")

        from content.models import Enrollment
        Enrollment.objects.create(user=student, course=course, source="admin")
        connection.close()

        context = _auth_context_at_width(browser, "admin@test.com", 1280)
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        records = page.get_by_test_id('enrollments-records')
        assert records.locator('form[data-confirm]').count() == 1
        records.get_by_test_id('unenroll-row-btn').click()
        page.wait_for_load_state('domcontentloaded')
        expect(
            page.get_by_text(
                'Unenrolled student@test.com from "Shipping AI".',
                exact=True,
            )
        ).to_have_count(1)

        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/?status=all",
            wait_until="domcontentloaded",
        )
        row = page.get_by_test_id('enrollment-row').filter(
            has_text='student@test.com'
        )
        assert row.get_by_text('student@test.com', exact=True).count() == 1
        assert row.get_by_test_id('unenroll-row-btn').count() == 0
        expect(row.get_by_text('Already unenrolled', exact=True)).to_be_visible()

    @browser_journey
    def test_desktop_access_lists_grant_and_purchase_once(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        admin = _create_staff_user("admin@test.com")
        granted = _create_user("mobile@test.com", tier_slug="main")
        buyer = _create_user("buyer@test.com", tier_slug="main")
        course = _create_course(title="Shipping AI", slug="shipping-ai")

        from content.models import CourseAccess
        CourseAccess.objects.create(
            user=granted,
            course=course,
            access_type='granted',
            granted_by=admin,
        )
        CourseAccess.objects.create(
            user=buyer,
            course=course,
            access_type='purchased',
        )
        connection.close()

        context = _auth_context_at_width(browser, "admin@test.com", 1280)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )

        records = page.get_by_test_id('access-records')
        assert records.get_by_text('mobile@test.com', exact=True).count() == 1
        assert records.get_by_text('buyer@test.com', exact=True).count() == 1
        assert records.get_by_test_id('access-row').count() == 2
        assert records.get_by_test_id('revoke-btn').count() == 1
        _capture(page, 'access-desktop.png')

    @browser_journey
    def test_purchased_access_has_one_explanation_and_no_revoke(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        buyer = _create_user("buyer@test.com", tier_slug="main")
        course = _create_course()

        from content.models import CourseAccess
        CourseAccess.objects.create(
            user=buyer,
            course=course,
            access_type='purchased',
        )
        connection.close()

        context = _auth_context_at_width(browser, "admin@test.com", 1280)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/access/",
            wait_until="domcontentloaded",
        )

        records = page.get_by_test_id('access-records')
        assert records.get_by_text('buyer@test.com', exact=True).count() == 1
        assert records.get_by_test_id('revoke-btn').count() == 0
        expect(
            records.get_by_text(
                'Purchased - cannot revoke',
                exact=True,
            )
        ).to_have_count(1)

    @browser_journey
    def test_empty_enrollments_keep_form_and_one_shared_empty_state(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        course = _create_course()

        context = _auth_context_at_width(browser, "admin@test.com", 1280)
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/enrollments/",
            wait_until="domcontentloaded",
        )

        expect(page.get_by_test_id('enroll-form')).to_be_visible()
        expect(page.get_by_test_id('studio-empty-state-fresh')).to_have_count(1)
        expect(
            page.get_by_text('No enrollments for this course yet.', exact=True)
        ).to_have_count(1)
        assert page.get_by_test_id('enrollments-records').count() == 0

    @browser_journey
    def test_non_staff_cannot_open_enrollment_or_access_lists(
        self, django_server, browser,
    ):
        _clear_state()
        _ensure_tiers()
        member = _create_user("member@test.com", tier_slug="free")
        student = _create_user("student@test.com", tier_slug="main")
        course = _create_course()

        from content.models import CourseAccess, Enrollment
        Enrollment.objects.create(user=student, course=course, source='admin')
        CourseAccess.objects.create(
            user=student,
            course=course,
            access_type='purchased',
        )
        connection.close()

        context = _auth_context_at_width(browser, member.email, 1280)
        page = context.new_page()
        for path in ('enrollments/', 'access/'):
            response = page.goto(
                f"{django_server}/studio/courses/{course.pk}/{path}",
                wait_until="domcontentloaded",
            )
            assert response.status == 403
            assert page.get_by_text('student@test.com', exact=True).count() == 0
