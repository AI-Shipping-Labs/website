"""
Playwright E2E tests for Course Cohorts (Issue #81).

Tests cover all 11 BDD scenarios from the issue:
- Main member discovers an upcoming cohort and enrolls from the course page
- Enrolled member unenrolls from a cohort
- Member cannot enroll when a cohort is full
- Free member sees the cohort but cannot enroll due to tier restriction
- Anonymous visitor browses a course with cohorts and sees the sign-up path
- Enrolled cohort member accesses a drip-locked unit and sees the unlock date
- Enrolled cohort member accesses a unit after the drip date has passed
- Non-cohort member accesses a drip-scheduled unit without restriction
- Main member enrolls in a free course cohort without tier issues
- Course with no active cohorts shows the syllabus without cohort section
- Admin creates a new cohort for a course through Django admin

Usage:
    uv run pytest playwright_tests/test_course_cohorts.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import DJANGO_BASE_URL


# Allow Django ORM calls from within sync_playwright (which runs an
# event loop internally). Without this, Django 6 raises
# SynchronousOnlyOperation when we create sessions inside test methods.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


VIEWPORT = {"width": 1280, "height": 720}

DEFAULT_PASSWORD = "TestPass123!"


def _ensure_tiers():
    """Ensure membership tiers exist."""
    from payments.models import Tier

    TIERS = [
        {"slug": "free", "name": "Free", "level": 0},
        {"slug": "basic", "name": "Basic", "level": 10},
        {"slug": "main", "name": "Main", "level": 20},
        {"slug": "premium", "name": "Premium", "level": 30},
    ]
    for tier_data in TIERS:
        Tier.objects.get_or_create(
            slug=tier_data["slug"], defaults=tier_data
        )


def _create_user(email, tier_slug="free", password=DEFAULT_PASSWORD):
    """Create a user with the given tier."""
    from accounts.models import User
    from payments.models import Tier

    _ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"email_verified": True},
    )
    user.set_password(password)
    tier = Tier.objects.get(slug=tier_slug)
    user.tier = tier
    user.email_verified = True
    user.save()
    return user


def _create_admin_user(email="admin@test.com", password=DEFAULT_PASSWORD):
    """Create a superuser for admin tests."""
    from accounts.models import User

    _ensure_tiers()
    user, created = User.objects.get_or_create(
        email=email,
        defaults={
            "email_verified": True,
            "is_staff": True,
            "is_superuser": True,
        },
    )
    user.set_password(password)
    user.is_staff = True
    user.is_superuser = True
    user.email_verified = True
    user.save()
    return user


def _create_session_for_user(email):
    """Create a Django session for the given user and return the session key."""
    from django.contrib.sessions.backends.db import SessionStore
    from django.contrib.auth import (
        SESSION_KEY,
        BACKEND_SESSION_KEY,
        HASH_SESSION_KEY,
    )
    from accounts.models import User

    user = User.objects.get(email=email)
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = (
        "django.contrib.auth.backends.ModelBackend"
    )
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    return session.session_key


def _auth_context(browser, email):
    """Create an authenticated browser context for the given user."""
    session_key = _create_session_for_user(email)
    context = browser.new_context(viewport=VIEWPORT)
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


def _clear_courses():
    """Delete all courses, modules, units, cohorts, enrollments, and progress."""
    from content.models import Course, UserCourseProgress, Cohort, CohortEnrollment

    CohortEnrollment.objects.all().delete()
    UserCourseProgress.objects.all().delete()
    Cohort.objects.all().delete()
    Course.objects.all().delete()


def _create_course(
    title,
    slug,
    description="",
    required_level=0,
    status="published",
    is_free=False,
    instructor_name="",
    tags=None,
):
    """Create a Course via ORM."""
    from content.models import Course

    if tags is None:
        tags = []

    course = Course(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status=status,
        is_free=is_free,
        instructor_name=instructor_name,
        tags=tags,
    )
    course.save()
    return course


def _create_module(course, title, sort_order=1):
    """Create a Module within a course."""
    from content.models import Module

    module = Module(
        course=course,
        title=title,
        sort_order=sort_order,
    )
    module.save()
    return module


def _create_unit(
    module,
    title,
    sort_order=1,
    video_url="",
    body="",
    homework="",
    is_preview=False,
    available_after_days=None,
):
    """Create a Unit within a module."""
    from content.models import Unit

    unit = Unit(
        module=module,
        title=title,
        sort_order=sort_order,
        video_url=video_url,
        body=body,
        homework=homework,
        is_preview=is_preview,
        available_after_days=available_after_days,
    )
    unit.save()
    return unit


def _create_cohort(
    course,
    name,
    start_date,
    end_date,
    is_active=True,
    max_participants=None,
):
    """Create a Cohort for a course."""
    from content.models import Cohort

    cohort = Cohort(
        course=course,
        name=name,
        start_date=start_date,
        end_date=end_date,
        is_active=is_active,
        max_participants=max_participants,
    )
    cohort.save()
    return cohort


def _enroll_user_in_cohort(user, cohort):
    """Enroll a user in a cohort."""
    from content.models import CohortEnrollment

    enrollment, created = CohortEnrollment.objects.get_or_create(
        cohort=cohort,
        user=user,
    )
    return enrollment


# ---------------------------------------------------------------
# Scenario 1: Main member discovers an upcoming cohort and enrolls
#              from the course page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1MainMemberDiscoversCohortAndEnrolls:
    """Main member discovers an upcoming cohort and enrolls from the course page."""

    def test_main_member_sees_cohort_info_and_enrolls(self, django_server):
        """Given a Main-tier user, a published course with required_level=20,
        and an active cohort 'March 2026' with 30 max_participants starting
        March 1, 2026. Navigate to /courses, click the course, verify cohort
        info, enroll, and verify the button changes to Enrolled."""
        _clear_courses()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")

        _create_cohort(
            course=course,
            name="March 2026",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=30,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses
                page.goto(
                    f"{django_server}/courses",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "LLM Engineering" in body

                # Step 2: Click on "LLM Engineering"
                course_link = page.locator(
                    'a[href="/courses/llm-engineering"]'
                ).first
                course_link.click()
                page.wait_for_load_state("networkidle")

                assert "/courses/llm-engineering" in page.url

                body = page.content()

                # Then: Course detail page shows "Next cohort" with name and date
                assert "Next cohort" in body
                assert "March 2026" in body
                assert "March 1, 2026" in body

                # Then: Shows "30 of 30 spots remaining"
                assert "30 of 30 spots remaining" in body

                # Step 3: Click the "Enroll" button
                enroll_btn = page.locator(
                    'button[data-action="enroll"]'
                )
                assert enroll_btn.count() >= 1
                enroll_btn.first.click()

                # The JS calls fetch() then window.location.reload().
                # Wait for the unenroll button to appear after reload.
                enrolled_btn = page.locator(
                    'button[data-action="unenroll"]'
                )
                enrolled_btn.wait_for(state="visible", timeout=10000)

                body = page.content()

                # Then: Button changes to "Enrolled"
                assert enrolled_btn.count() >= 1
                assert "Enrolled" in enrolled_btn.first.inner_text()

                # Then: Spots remaining decreases to "29 of 30 spots remaining"
                assert "29 of 30 spots remaining" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Enrolled member unenrolls from a cohort
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2EnrolledMemberUnenrolls:
    """Enrolled member unenrolls from a cohort."""

    def test_enrolled_member_unenrolls(self, django_server):
        """Given a Main-tier user already enrolled in the March 2026 cohort
        of LLM Engineering. Navigate to the course page, verify Enrolled
        button, click to unenroll, verify button changes back to Enroll."""
        _clear_courses()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")

        cohort = _create_cohort(
            course=course,
            name="March 2026",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=30,
        )
        _enroll_user_in_cohort(user, cohort)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/llm-engineering
                page.goto(
                    f"{django_server}/courses/llm-engineering",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The cohort card shows an "Enrolled" button
                enrolled_btn = page.locator(
                    'button[data-action="unenroll"]'
                )
                assert enrolled_btn.count() >= 1
                assert "Enrolled" in enrolled_btn.first.inner_text()

                # Step 2: Click the "Enrolled" button to unenroll
                enrolled_btn.first.click()

                # The JS calls fetch() then window.location.reload().
                # Wait for the enroll button to appear after reload.
                enroll_btn = page.locator(
                    'button[data-action="enroll"]'
                )
                enroll_btn.wait_for(state="visible", timeout=10000)

                body = page.content()

                # Then: Button changes back to "Enroll"
                assert enroll_btn.count() >= 1
                assert "Enroll" in enroll_btn.first.inner_text()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Member cannot enroll when a cohort is full
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3CohortFullCannotEnroll:
    """Member cannot enroll when a cohort is full."""

    def test_full_cohort_shows_full_message(self, django_server):
        """Given a Main-tier user and a cohort with max_participants=1 that
        already has 1 enrolled user. The cohort card shows 'Cohort is full'
        instead of an Enroll button."""
        _clear_courses()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")

        cohort = _create_cohort(
            course=course,
            name="March 2026",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=1,
        )

        # Fill the cohort with another user
        other_user = _create_user("other@test.com", tier_slug="main")
        _enroll_user_in_cohort(other_user, cohort)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/llm-engineering
                page.goto(
                    f"{django_server}/courses/llm-engineering",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Shows "Cohort is full" instead of an Enroll button
                assert "Cohort is full" in body

                # Then: No Enroll button is shown
                enroll_btn = page.locator(
                    'button[data-action="enroll"]'
                )
                assert enroll_btn.count() == 0
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Free member sees the cohort but cannot enroll
#              due to tier restriction
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4FreeMemberCannotEnroll:
    """Free member sees the cohort but cannot enroll due to tier restriction."""

    def test_free_member_sees_cohort_no_enroll_button(self, django_server):
        """Given a Free-tier user and a course with required_level=20.
        The cohort info is visible but no Enroll button is shown.
        An upgrade CTA links to /pricing."""
        _clear_courses()
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")

        _create_cohort(
            course=course,
            name="March 2026",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=30,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/llm-engineering
                page.goto(
                    f"{django_server}/courses/llm-engineering",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The cohort name and start date are visible
                assert "March 2026" in body
                assert "March 1, 2026" in body

                # Then: No Enroll button is shown
                enroll_btn = page.locator(
                    'button[data-action="enroll"]'
                )
                assert enroll_btn.count() == 0

                # Then: Upgrade CTA links to /pricing
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1
                href = pricing_link.first.get_attribute("href")
                assert "/pricing" in href
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Anonymous visitor browses a course with cohorts and
#              sees the sign-up path
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5AnonymousVisitorSeesCohortAndPricing:
    """Anonymous visitor browses a course with cohorts and sees the sign-up path."""

    def test_anonymous_sees_cohort_info_and_pricing_cta(self, django_server):
        """Given an anonymous visitor and a course with required_level=20
        and an active cohort. The cohort info is visible, no Enroll button
        is shown, and a View Pricing CTA links to /pricing."""
        _clear_courses()
        _ensure_tiers()

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")

        _create_cohort(
            course=course,
            name="March 2026",
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            max_participants=30,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/llm-engineering
                page.goto(
                    f"{django_server}/courses/llm-engineering",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Cohort info is visible
                assert "March 2026" in body
                assert "March 1, 2026" in body

                # Then: No Enroll button
                enroll_btn = page.locator(
                    'button[data-action="enroll"]'
                )
                assert enroll_btn.count() == 0

                # Then: A "View Pricing" CTA is visible
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1

                # Step 2: Click the "View Pricing" link
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: Lands on /pricing
                assert "/pricing" in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Enrolled cohort member accesses a drip-locked unit
#              and sees the unlock date
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6DripLockedUnit:
    """Enrolled cohort member accesses a drip-locked unit and sees the unlock date."""

    def test_drip_locked_unit_shows_unlock_date(self, django_server):
        """Given a Main-tier user enrolled in a cohort starting January 1, 2030,
        and a unit with available_after_days=14. The unit page shows the
        unlock date (January 15, 2030), hides the lesson content, and
        has a 'Back to Course' link."""
        _clear_courses()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="Drip Course",
            slug="drip-course",
            description="A course with drip schedule.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(
            mod, "Drip Locked Lesson", sort_order=1,
            video_url="https://www.youtube.com/watch?v=test123",
            body="# Secret Lesson\nThis should be hidden.",
            homework="# Homework\nDo the task.",
            available_after_days=14,
        )

        cohort = _create_cohort(
            course=course,
            name="Future Cohort",
            start_date=datetime.date(2030, 1, 1),
            end_date=datetime.date(2030, 6, 1),
        )
        _enroll_user_in_cohort(user, cohort)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the drip-locked unit page
                page.goto(
                    f"{django_server}/courses/drip-course/1/1",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Shows the date when the lesson will become available
                assert "January 15, 2030" in body

                # Then: The lesson content (body_html, homework_html) is NOT
                # visible in the rendered page. The title appears in the
                # <title> tag and gated header, but the actual lesson text
                # and homework should be absent from the visible content.
                # Check that the rendered body text is not in the main content.
                main_content = page.locator("main")
                main_text = main_content.inner_text()
                assert "This should be hidden" not in main_text
                assert "Do the task" not in main_text

                # The video player should not be rendered
                video_iframe = page.locator("iframe")
                assert video_iframe.count() == 0

                # Then: A "Back to Course" link is available
                back_link = page.locator(
                    'a:has-text("Back to Course")'
                )
                assert back_link.count() >= 1

                # Step 2: Click "Back to Course"
                back_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: Returns to the course detail page
                assert "/courses/drip-course" in page.url
                # Make sure we are NOT on a unit page
                assert "/1/1" not in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Enrolled cohort member accesses a unit after the
#              drip date has passed
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7DripUnlockedUnit:
    """Enrolled cohort member accesses a unit after the drip date has passed."""

    def test_drip_unit_accessible_after_date_passes(self, django_server):
        """Given a Main-tier user enrolled in a cohort that started January 1, 2020
        and a unit with available_after_days=14. The lesson content is fully
        accessible and the user can mark the unit as completed."""
        _clear_courses()
        _ensure_tiers()
        user = _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="Past Drip Course",
            slug="past-drip-course",
            description="A course with past drip schedule.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(
            mod, "Unlocked Lesson", sort_order=1,
            video_url="https://www.youtube.com/watch?v=test456",
            body="# Unlocked Content\nThis lesson is available.",
            homework="# Homework\nComplete the assignment.",
            available_after_days=14,
        )

        cohort = _create_cohort(
            course=course,
            name="Past Cohort",
            start_date=datetime.date(2020, 1, 1),
            end_date=datetime.date(2020, 6, 1),
        )
        _enroll_user_in_cohort(user, cohort)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the unit page
                page.goto(
                    f"{django_server}/courses/past-drip-course/1/1",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Lesson content is fully accessible
                assert "Unlocked Content" in body
                assert "This lesson is available" in body

                # Video player present
                assert "test456" in body or "video-player" in body

                # Homework visible
                assert "Homework" in body
                assert "Complete the assignment" in body

                # Then: User can mark the unit as completed
                mark_btn = page.locator("#mark-complete-btn")
                assert mark_btn.count() >= 1
                assert "Mark as completed" in mark_btn.inner_text()

                mark_btn.click()
                page.wait_for_timeout(1000)

                assert "Completed" in mark_btn.inner_text()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Non-cohort member accesses a drip-scheduled unit
#              without restriction
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8NonCohortMemberAccessesDripUnit:
    """Non-cohort member accesses a drip-scheduled unit without restriction."""

    def test_non_cohort_member_accesses_drip_unit_freely(self, django_server):
        """Given a Main-tier user NOT enrolled in any cohort and a course
        with a unit that has available_after_days=14. The lesson content
        is fully accessible because drip scheduling only applies to
        cohort members."""
        _clear_courses()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="Drip No Cohort Course",
            slug="drip-no-cohort-course",
            description="A course with drip schedule.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(
            mod, "Drip Unit No Restriction", sort_order=1,
            body="# Freely Accessible\nNo cohort needed.",
            homework="# Homework\nDo it now.",
            available_after_days=14,
        )
        _create_unit(
            mod, "Second Unit", sort_order=2,
            body="# Second\nAnother unit.",
        )

        # Create a cohort but DO NOT enroll the user
        _create_cohort(
            course=course,
            name="Some Cohort",
            start_date=datetime.date(2030, 1, 1),
            end_date=datetime.date(2030, 6, 1),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the drip-scheduled unit
                page.goto(
                    f"{django_server}/courses/drip-no-cohort-course/1/1",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Content is fully accessible
                assert "Freely Accessible" in body
                assert "No cohort needed" in body

                # Homework visible
                assert "Homework" in body
                assert "Do it now" in body

                # Then: User can mark as completed
                mark_btn = page.locator("#mark-complete-btn")
                assert mark_btn.count() >= 1
                assert "Mark as completed" in mark_btn.inner_text()

                mark_btn.click()
                page.wait_for_timeout(1000)
                assert "Completed" in mark_btn.inner_text()

                # Then: Can navigate to the next unit
                next_btn = page.locator('a:has-text("Next:")')
                assert next_btn.count() >= 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Main member enrolls in a free course cohort
#              without tier issues
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9FreeCourseEnrollment:
    """Main member enrolls in a free course cohort without tier issues."""

    def test_free_tier_user_enrolls_in_free_course_cohort(self, django_server):
        """Given a Free-tier user and a published free course 'Intro to AI'
        with required_level=0 and an active cohort 'Spring 2026'. The
        cohort card shows an Enroll button, and clicking it enrolls the user."""
        _clear_courses()
        _ensure_tiers()
        _create_user("free-user@test.com", tier_slug="free")

        course = _create_course(
            title="Intro to AI",
            slug="intro-to-ai",
            description="Introduction to AI.",
            required_level=0,
            is_free=True,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Intro\nWelcome.")

        _create_cohort(
            course=course,
            name="Spring 2026",
            start_date=datetime.date(2026, 4, 1),
            end_date=datetime.date(2026, 7, 1),
            max_participants=50,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free-user@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/intro-to-ai
                page.goto(
                    f"{django_server}/courses/intro-to-ai",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Cohort card shows "Spring 2026" with Enroll button
                assert "Spring 2026" in body

                enroll_btn = page.locator(
                    'button[data-action="enroll"]'
                )
                assert enroll_btn.count() >= 1

                # Step 2: Click the "Enroll" button
                enroll_btn.first.click()

                # The JS calls fetch() then window.location.reload().
                # Wait for the enrolled button to appear after reload.
                enrolled_btn = page.locator(
                    'button[data-action="unenroll"]'
                )
                enrolled_btn.wait_for(state="visible", timeout=10000)

                body = page.content()

                # Then: Shows "Enrolled"
                assert enrolled_btn.count() >= 1
                assert "Enrolled" in enrolled_btn.first.inner_text()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Course with no active cohorts shows the syllabus
#               without cohort section
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10NoCohortsSyllabus:
    """Course with no active cohorts shows the syllabus without cohort section."""

    def test_no_active_cohorts_no_cohort_section(self, django_server):
        """Given a Main-tier user and a course with all cohorts inactive.
        The course detail page shows the syllabus but no 'Next cohort'
        section. The user can still access individual units."""
        _clear_courses()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")
        _create_unit(mod, "Lesson 2", sort_order=2, body="# Lesson 2\nMore content.")

        # Create inactive cohort (should not be displayed)
        _create_cohort(
            course=course,
            name="Old Cohort",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 6, 1),
            is_active=False,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "main@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/llm-engineering
                page.goto(
                    f"{django_server}/courses/llm-engineering",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Syllabus shows modules and units
                assert "Module 1" in body
                assert "Lesson 1" in body
                assert "Lesson 2" in body

                # Then: No "Next cohort" section is displayed
                assert "Next cohort" not in body

                # Then: User can click into individual units
                lesson_link = page.locator(
                    'a[href="/courses/llm-engineering/1/1"]'
                )
                assert lesson_link.count() >= 1

                lesson_link.first.click()
                page.wait_for_load_state("networkidle")

                # Navigated to the unit page
                assert "/courses/llm-engineering/1/1" in page.url
                body = page.content()
                assert "Lesson 1" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Admin creates a new cohort for a course through
#               Django admin
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11AdminCreatesCohort:
    """Admin creates a new cohort for a course through Django admin."""

    def test_admin_creates_cohort_and_it_appears_on_course_page(
        self, django_server
    ):
        """Given a staff/superuser. Navigate to admin, create a cohort
        for 'LLM Engineering', then verify it appears on the course
        detail page."""
        _clear_courses()
        _ensure_tiers()
        _create_admin_user("admin@test.com")

        course = _create_course(
            title="LLM Engineering",
            slug="llm-engineering",
            description="Learn LLM engineering.",
            required_level=20,
        )
        mod = _create_module(course, "Module 1", sort_order=1)
        _create_unit(mod, "Lesson 1", sort_order=1, body="# Lesson 1\nContent.")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /admin/content/cohort/add/
                page.goto(
                    f"{django_server}/admin/content/cohort/add/",
                    wait_until="networkidle",
                )
                body = page.content()

                # The add form should be present
                assert "Add cohort" in body.lower() or "cohort" in body.lower()

                # Step 2: Select the course
                course_select = page.locator("#id_course")
                course_select.select_option(str(course.pk))

                # Step 3: Enter cohort name
                page.locator("#id_name").fill("Summer 2026")

                # Step 4: Set start_date and end_date
                page.locator("#id_start_date").fill("2026-06-01")
                page.locator("#id_end_date").fill("2026-09-01")

                # Step 5: Set max_participants
                page.locator("#id_max_participants").fill("25")

                # Step 6: Save the cohort
                page.locator('input[name="_save"]').click()
                page.wait_for_load_state("networkidle")

                # Then: Lands on the cohort list page
                assert "/admin/content/cohort/" in page.url
                body = page.content()
                assert "Summer 2026" in body

                # Step 7: Navigate to /courses/llm-engineering
                page.goto(
                    f"{django_server}/courses/llm-engineering",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Course detail page shows the new cohort
                assert "Next cohort" in body
                assert "Summer 2026" in body
                assert "June 1, 2026" in body
                assert "25 of 25 spots remaining" in body
            finally:
                browser.close()
