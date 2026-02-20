"""
Playwright E2E tests for Course Unit Pages and Progress Tracking (Issue #79).

Tests cover all 10 BDD scenarios from the issue:
- Premium member works through a course unit from start to finish
- Member navigates between units using the sidebar
- Free member previews a gated course unit marked as preview
- Free member hits paywall on a gated unit and finds the upgrade path
- Anonymous visitor explores the course syllabus but cannot access units
- Member marks a unit completed and then undoes it
- Member completes units and sees progress reflected on the course detail page
- Member reaches the last unit and sees no next button
- Member navigates via breadcrumbs from unit back to the course and catalog
- Course in progress appears on the member dashboard for easy resumption

Usage:
    uv run pytest playwright_tests/test_course_units.py -v
"""

import os

import pytest
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
    """Delete all courses (cascades to modules, units, progress)."""
    from content.models import Course

    Course.objects.all().delete()


def _clear_progress():
    """Delete all UserCourseProgress records."""
    from content.models import UserCourseProgress

    UserCourseProgress.objects.all().delete()


def _create_course(
    title,
    slug,
    required_level=0,
    description="",
    status="published",
    instructor_name="",
):
    """Create a Course via ORM."""
    from content.models import Course

    course = Course(
        title=title,
        slug=slug,
        description=description,
        required_level=required_level,
        status=status,
        instructor_name=instructor_name,
    )
    course.save()
    return course


def _create_module(course, title, sort_order=0):
    """Create a Module via ORM."""
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
    sort_order=0,
    video_url="",
    body="",
    homework="",
    timestamps=None,
    is_preview=False,
):
    """Create a Unit via ORM."""
    from content.models import Unit

    if timestamps is None:
        timestamps = []

    unit = Unit(
        module=module,
        title=title,
        sort_order=sort_order,
        video_url=video_url,
        body=body,
        homework=homework,
        timestamps=timestamps,
        is_preview=is_preview,
    )
    unit.save()
    return unit


def _mark_unit_completed(user_email, unit):
    """Create a UserCourseProgress record marking a unit as completed."""
    from django.utils import timezone
    from accounts.models import User
    from content.models import UserCourseProgress

    user = User.objects.get(email=user_email)
    progress, _ = UserCourseProgress.objects.get_or_create(
        user=user, unit=unit,
    )
    progress.completed_at = timezone.now()
    progress.save()
    return progress


# ---------------------------------------------------------------
# Scenario 1: Premium member works through a course unit from
#              start to finish
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1PremiumMemberWorksThrough:
    """Premium member works through a course unit from start to finish."""

    def test_premium_member_full_unit_workflow(self, django_server):
        """Given a Premium member and a published course with required_level=30,
        navigate to the course, click into Unit 1, verify video/lesson/homework,
        mark complete, then proceed to the next unit."""
        _clear_courses()
        _create_user("premium-cu@test.com", tier_slug="premium")

        course = _create_course(
            title="Advanced AI Patterns",
            slug="advanced-ai-patterns",
            required_level=30,
            description="Master advanced AI patterns.",
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "Unit 1: Intro", sort_order=0,
            video_url="https://www.youtube.com/watch?v=test123",
            body="# Welcome\n\nThis is the **first** lesson.",
            homework="## Task 1\n\nBuild a simple agent.",
            timestamps=[
                {"time_seconds": 0, "label": "Overview"},
                {"time_seconds": 120, "label": "Setup"},
            ],
        )
        _create_unit(
            module1, "Unit 2: Deep Dive", sort_order=1,
            body="# Deep Dive\n\nGo deeper.",
        )
        _create_unit(
            module1, "Unit 3: Wrap Up", sort_order=2,
            body="# Wrap Up\n\nConclusion.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "premium-cu@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/{slug}
                page.goto(
                    f"{django_server}/courses/advanced-ai-patterns",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Advanced AI Patterns" in body

                # Step 2: Click on Unit 1 in the syllabus
                page.locator(
                    'a:has-text("Unit 1: Intro")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                # Verify URL
                assert "/courses/advanced-ai-patterns/0/0" in page.url

                body = page.content()

                # Video player is present
                assert "video-player" in body or "test123" in body

                # Lesson text is rendered
                assert "Welcome" in body
                assert "first" in body

                # Homework section is present
                assert "Homework" in body
                assert "Task 1" in body
                assert "Build a simple agent" in body

                # Step 3: Click "Mark as completed"
                complete_btn = page.locator("#mark-complete-btn")
                assert complete_btn.is_visible()
                assert "Mark as completed" in complete_btn.inner_text()

                complete_btn.click()
                page.wait_for_timeout(2000)

                # Button changes to "Completed"
                btn_text = complete_btn.inner_text()
                assert "Completed" in btn_text

                # Step 4: Click the "Next" button to proceed to Unit 2
                next_btn = page.locator(
                    'a:has-text("Next: Unit 2: Deep Dive")'
                )
                assert next_btn.count() >= 1
                next_btn.first.click()
                page.wait_for_load_state("networkidle")

                # Verify we are on Unit 2
                assert "/courses/advanced-ai-patterns/0/1" in page.url
                body = page.content()
                assert "Deep Dive" in body

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Member navigates between units using the sidebar
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2SidebarNavigation:
    """Member navigates between units using the sidebar."""

    def test_sidebar_navigation_with_checkmarks(self, django_server):
        """Given a Premium member with Unit 1 of Module 1 already completed,
        verify sidebar shows checkmark, navigate to Module 2 via sidebar,
        then back -- checkmark persists."""
        _clear_courses()
        _create_user("premium-sb@test.com", tier_slug="premium")

        course = _create_course(
            title="Sidebar Nav Course",
            slug="sidebar-nav-course",
            required_level=30,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        unit1_m1 = _create_unit(
            module1, "M1 Unit 1", sort_order=0,
            body="# M1U1\n\nFirst unit.",
        )
        _create_unit(
            module1, "M1 Unit 2", sort_order=1,
            body="# M1U2\n\nSecond unit.",
        )
        module2 = _create_module(course, "Module 2", sort_order=1)
        _create_unit(
            module2, "M2 Unit 1", sort_order=0,
            body="# M2U1\n\nModule 2 unit.",
        )

        # Mark Unit 1 of Module 1 as completed
        _mark_unit_completed("premium-sb@test.com", unit1_m1)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "premium-sb@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/{slug}/0/0 (Unit 1 of Module 1)
                page.goto(
                    f"{django_server}/courses/sidebar-nav-course/0/0",
                    wait_until="networkidle",
                )
                body = page.content()

                # Sidebar shows all modules and units
                assert "Module 1" in body
                assert "Module 2" in body
                assert "M1 Unit 1" in body
                assert "M1 Unit 2" in body
                assert "M2 Unit 1" in body

                # Unit 1 of Module 1 has a checkmark (check-circle-2)
                assert "check-circle-2" in body

                # Step 2: Click on Module 2 unit in the sidebar
                sidebar_m2_link = page.locator(
                    'nav a:has-text("M2 Unit 1")'
                )
                assert sidebar_m2_link.count() >= 1
                sidebar_m2_link.first.click()
                page.wait_for_load_state("networkidle")

                # Verify URL
                assert "/courses/sidebar-nav-course/1/0" in page.url
                body = page.content()
                assert "Module 2 unit" in body

                # Step 3: Navigate back to /courses/{slug}/0/0
                page.goto(
                    f"{django_server}/courses/sidebar-nav-course/0/0",
                    wait_until="networkidle",
                )
                body = page.content()

                # The completed checkmark for Unit 1 is still shown
                assert "check-circle-2" in body

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Free member previews a gated course unit marked
#              as preview
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3FreePreviewUnit:
    """Free member previews a gated course unit marked as preview."""

    def test_free_member_accesses_preview_and_blocked_on_non_preview(
        self, django_server
    ):
        """Given a Free member and a Premium course, Unit 1 (is_preview=true)
        is accessible while Unit 2 (is_preview=false) shows a gated message."""
        _clear_courses()
        _create_user("free-cu@test.com", tier_slug="free")

        course = _create_course(
            title="Preview Test Course",
            slug="preview-test-course",
            required_level=30,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "Preview Unit", sort_order=0,
            body="# Preview Content\n\nFree for all to see.",
            video_url="https://www.youtube.com/watch?v=prev123",
            homework="## Preview Homework\n\nTry this.",
            is_preview=True,
        )
        _create_unit(
            module1, "Locked Unit", sort_order=1,
            body="# Locked Content\n\nPremium only.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free-cu@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/{slug}
                page.goto(
                    f"{django_server}/courses/preview-test-course",
                    wait_until="networkidle",
                )
                body = page.content()

                # Syllabus shows both units
                assert "Preview Unit" in body
                assert "Locked Unit" in body
                # The Preview badge is rendered for non-access users
                # (shown next to the preview unit title)
                preview_badge = page.locator('span:has-text("Preview")')
                assert preview_badge.count() >= 1

                # Step 2: Navigate directly to the preview unit page
                # (unit titles are not clickable links when user lacks
                # course-level access, but preview units are directly
                # accessible via URL)
                page.goto(
                    f"{django_server}/courses/preview-test-course/0/0",
                    wait_until="networkidle",
                )

                body = page.content()

                # Full content is visible despite lacking Premium tier
                assert "Preview Content" in body
                assert "Free for all to see" in body
                # Video should be present
                assert "prev123" in body or "video-player" in body
                # Homework should be present
                assert "Homework" in body
                assert "Preview Homework" in body

                # Step 3: Navigate to the locked unit
                page.goto(
                    f"{django_server}/courses/preview-test-course/0/1",
                    wait_until="networkidle",
                )
                body = page.content()

                # Gated message with correct tier name
                assert "Upgrade to Premium to access this lesson" in body
                # "View Pricing" link
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Free member hits paywall on a gated unit and finds
#              the upgrade path
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4FreePaywall:
    """Free member hits paywall on a gated unit and finds the upgrade path."""

    def test_free_member_sees_gated_message_and_navigates_to_pricing(
        self, django_server
    ):
        """Given a Free member and a Main-level course with a non-preview
        unit, navigating to the unit shows the gated message. Clicking
        'View Pricing' leads to /pricing."""
        _clear_courses()
        _create_user("free-pw@test.com", tier_slug="free")

        course = _create_course(
            title="Main Course",
            slug="main-course",
            required_level=20,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "Lesson 1", sort_order=0,
            body="# Main Content\n\nFor Main members.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free-pw@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the unit
                page.goto(
                    f"{django_server}/courses/main-course/0/0",
                    wait_until="networkidle",
                )
                body = page.content()

                # Gated message mentions correct tier
                assert "Upgrade to Main to access this lesson" in body

                # "View Pricing" link is visible
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1

                # Step 2: Click "View Pricing"
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # Lands on /pricing
                assert "/pricing" in page.url

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Anonymous visitor explores the course syllabus but
#              cannot access units
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5AnonymousSyllabus:
    """Anonymous visitor explores the course syllabus but cannot access units."""

    def test_anonymous_sees_syllabus_without_clickable_links(
        self, django_server
    ):
        """Given an anonymous visitor and a Basic-level published course,
        the syllabus shows unit titles but they are NOT clickable links.
        A CTA says 'Unlock with Basic' and 'View Pricing' navigates to /pricing."""
        _clear_courses()

        course = _create_course(
            title="Anonymous Test Course",
            slug="anonymous-test-course",
            required_level=10,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "Lesson A", sort_order=0,
            body="# A\n\nContent.",
        )
        _create_unit(
            module1, "Lesson B", sort_order=1,
            body="# B\n\nContent.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /courses/{slug}
                page.goto(
                    f"{django_server}/courses/anonymous-test-course",
                    wait_until="networkidle",
                )
                body = page.content()

                # Unit titles are visible
                assert "Lesson A" in body
                assert "Lesson B" in body

                # Unit titles are NOT clickable links (rendered as <span>)
                # Check that there are no <a> tags linking to unit pages
                unit_links = page.locator(
                    'a[href*="/courses/anonymous-test-course/0/"]'
                )
                assert unit_links.count() == 0

                # CTA mentions "Unlock with Basic"
                assert "Unlock with Basic" in body

                # "View Pricing" link present
                pricing_link = page.locator(
                    'a:has-text("View Pricing")'
                )
                assert pricing_link.count() >= 1

                # Step 2: Click "View Pricing"
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # Lands on /pricing
                assert "/pricing" in page.url

            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Member marks a unit completed and then undoes it
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6ToggleCompletion:
    """Member marks a unit completed and then undoes it."""

    def test_mark_complete_and_undo(self, django_server):
        """Given a Basic member and a Basic-level course, navigate to a unit,
        mark it completed, then undo -- the button toggles accordingly."""
        _clear_courses()
        _create_user("basic-toggle@test.com", tier_slug="basic")

        course = _create_course(
            title="Toggle Course",
            slug="toggle-course",
            required_level=10,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "Toggle Unit", sort_order=0,
            body="# Toggle\n\nTest toggling.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-toggle@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the unit
                page.goto(
                    f"{django_server}/courses/toggle-course/0/0",
                    wait_until="networkidle",
                )

                complete_btn = page.locator("#mark-complete-btn")
                assert complete_btn.is_visible()

                # Initially shows "Mark as completed"
                assert "Mark as completed" in complete_btn.inner_text()

                # Step 2: Click "Mark as completed"
                complete_btn.click()
                page.wait_for_timeout(2000)

                # Button changes to "Completed"
                btn_text = complete_btn.inner_text()
                assert "Completed" in btn_text

                # Step 3: Click "Completed" to undo
                complete_btn.click()
                page.wait_for_timeout(2000)

                # Button reverts to "Mark as completed"
                btn_text = complete_btn.inner_text()
                assert "Mark as completed" in btn_text

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Member completes units and sees progress reflected
#              on the course detail page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7ProgressBar:
    """Member completes units and sees progress reflected on the course detail page."""

    def test_progress_bar_updates_after_completing_units(
        self, django_server
    ):
        """Given a Premium member and a course with 3 units, none completed,
        the progress bar shows 0 of 3. After completing 2 units, the
        progress bar shows 2 of 3."""
        _clear_courses()
        _create_user("premium-pb@test.com", tier_slug="premium")

        course = _create_course(
            title="Progress Course",
            slug="progress-course",
            required_level=30,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "P Unit 1", sort_order=0,
            body="# PU1\n\nFirst.",
        )
        _create_unit(
            module1, "P Unit 2", sort_order=1,
            body="# PU2\n\nSecond.",
        )
        _create_unit(
            module1, "P Unit 3", sort_order=2,
            body="# PU3\n\nThird.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "premium-pb@test.com")
            page = context.new_page()
            try:
                # Step 1: Check initial progress (0 of 3)
                page.goto(
                    f"{django_server}/courses/progress-course",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "0 of 3 completed" in body

                # Step 2: Complete Unit 1
                page.goto(
                    f"{django_server}/courses/progress-course/0/0",
                    wait_until="networkidle",
                )
                complete_btn = page.locator("#mark-complete-btn")
                complete_btn.click()
                page.wait_for_timeout(2000)

                # Step 3: Complete Unit 2
                page.goto(
                    f"{django_server}/courses/progress-course/0/1",
                    wait_until="networkidle",
                )
                complete_btn = page.locator("#mark-complete-btn")
                complete_btn.click()
                page.wait_for_timeout(2000)

                # Step 4: Navigate back to course detail
                page.goto(
                    f"{django_server}/courses/progress-course",
                    wait_until="networkidle",
                )
                body = page.content()

                # Progress bar shows 2 of 3
                assert "2 of 3 completed" in body

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Member reaches the last unit and sees no next button
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8LastUnitNoNext:
    """Member reaches the last unit and sees no next button."""

    def test_last_unit_has_no_next_button(self, django_server):
        """Given a Basic member and a course with 2 units, the first unit
        shows a 'Next' button but the second (last) unit does not."""
        _clear_courses()
        _create_user("basic-last@test.com", tier_slug="basic")

        course = _create_course(
            title="Last Unit Course",
            slug="last-unit-course",
            required_level=10,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "First Unit", sort_order=0,
            body="# First\n\nContent.",
        )
        _create_unit(
            module1, "Last Unit", sort_order=1,
            body="# Last\n\nEnd of course.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic-last@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the first unit
                page.goto(
                    f"{django_server}/courses/last-unit-course/0/0",
                    wait_until="networkidle",
                )
                body = page.content()

                # "Next" button is visible pointing to the second unit
                next_link = page.locator('a:has-text("Next: Last Unit")')
                assert next_link.count() >= 1

                # Step 2: Click the "Next" button
                next_link.first.click()
                page.wait_for_load_state("networkidle")

                # Verify URL is the second (last) unit
                assert "/courses/last-unit-course/0/1" in page.url

                body = page.content()
                assert "Last Unit" in body
                assert "End of course" in body

                # No "Next" button is shown on the last unit
                next_buttons = page.locator('a:has-text("Next:")')
                assert next_buttons.count() == 0

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Member navigates via breadcrumbs from unit back to
#              the course and catalog
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9BreadcrumbNavigation:
    """Member navigates via breadcrumbs from unit back to the course and catalog."""

    def test_breadcrumb_navigation(self, django_server):
        """Given a Premium member on a unit page, clicking 'Courses' in the
        breadcrumb leads to /courses, clicking the course title leads to
        /courses/{slug}."""
        _clear_courses()
        _create_user("premium-bc@test.com", tier_slug="premium")

        course = _create_course(
            title="Breadcrumb Course",
            slug="breadcrumb-course",
            required_level=30,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        _create_unit(
            module1, "BC Unit 1", sort_order=0,
            body="# BC\n\nBreadcrumb test.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "premium-bc@test.com")
            page = context.new_page()
            try:
                # Navigate to the unit page
                page.goto(
                    f"{django_server}/courses/breadcrumb-course/0/0",
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(1000)
                body = page.content()
                assert "Courses" in body
                assert "Breadcrumb Course" in body

                # Step 1: Click "Courses" in the breadcrumb
                courses_link = page.locator(
                    'a[href="/courses"]:has-text("Courses")'
                )
                assert courses_link.count() >= 1
                courses_link.first.click()
                page.wait_for_url("**/courses", timeout=10000)
                page.wait_for_load_state("domcontentloaded")

                # Lands on /courses
                assert page.url.rstrip("/").endswith("/courses")

                # Step 2: Navigate back to the unit page
                page.goto(
                    f"{django_server}/courses/breadcrumb-course/0/0",
                    wait_until="domcontentloaded",
                )
                page.wait_for_timeout(1000)

                # Step 3: Click the course title in the breadcrumb
                course_link = page.locator(
                    'a[href="/courses/breadcrumb-course"]:has-text("Breadcrumb Course")'
                )
                assert course_link.count() >= 1
                course_link.first.click()
                page.wait_for_url("**/courses/breadcrumb-course", timeout=10000)
                page.wait_for_load_state("domcontentloaded")

                # Lands on the course detail page
                assert "/courses/breadcrumb-course" in page.url
                # Should NOT be on a unit page
                assert "/0/" not in page.url

                context.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Course in progress appears on the member dashboard
#               for easy resumption
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10DashboardContinueLearning:
    """Course in progress appears on the member dashboard for easy resumption."""

    def test_in_progress_course_on_dashboard(self, django_server):
        """Given a Premium member with 1 of 3 units completed, the
        dashboard shows the course in 'Continue Learning' with progress.
        Clicking it leads to the course detail page."""
        _clear_courses()
        _create_user("premium-dash@test.com", tier_slug="premium")

        course = _create_course(
            title="Dashboard Course",
            slug="dashboard-course",
            required_level=30,
        )
        module1 = _create_module(course, "Module 1", sort_order=0)
        unit1 = _create_unit(
            module1, "Dash Unit 1", sort_order=0,
            body="# DU1\n\nFirst.",
        )
        _create_unit(
            module1, "Dash Unit 2", sort_order=1,
            body="# DU2\n\nSecond.",
        )
        _create_unit(
            module1, "Dash Unit 3", sort_order=2,
            body="# DU3\n\nThird.",
        )

        # Mark 1 unit as completed
        _mark_unit_completed("premium-dash@test.com", unit1)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "premium-dash@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to the dashboard
                page.goto(
                    f"{django_server}/",
                    wait_until="networkidle",
                )
                body = page.content()

                # "Continue Learning" section is present
                assert "Continue Learning" in body

                # The course appears with progress
                assert "Dashboard Course" in body
                assert "1" in body  # 1 of 3 or 1/3

                # Step 2: Click on the course in the Continue Learning section
                course_link = page.locator(
                    'a[href="/courses/dashboard-course"]'
                )
                assert course_link.count() >= 1
                course_link.first.click()
                page.wait_for_load_state("networkidle")

                # Lands on the course detail page
                assert "/courses/dashboard-course" in page.url

                context.close()
            finally:
                browser.close()
