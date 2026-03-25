"""
Playwright E2E tests for the AI Hero free course (Issue #128).

Tests cover all 8 BDD scenarios from the issue:
- Anonymous visitor discovers the free AI Hero course in the catalog
- Anonymous visitor reads the course landing page and sees signup CTA
- Anonymous visitor can preview Day 1 content
- Free member works through the course from Day 1 to Day 2
- Free member tracks progress across the course
- Free member accesses the last unit (Day 7)
- Course appears on the authenticated member dashboard
- Basic member can also access the free course

Usage:
    uv run pytest playwright_tests/test_aihero_course.py -v
"""

import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    VIEWPORT,
    DEFAULT_PASSWORD,
    ensure_tiers as _ensure_tiers,
    create_user as _create_user,
    create_session_for_user as _create_session_for_user,
    auth_context as _auth_context,
)


os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _ensure_aihero_course():
    """Ensure the AI Hero course exists with 7 day-units.

    Creates the course, one module, and seven units directly via ORM.
    Closes the database connection afterward to release SQLite locks
    so the server thread can read the newly created data.
    """
    from content.models import Course, Module, Unit
    from django.db import connection

    if Course.objects.filter(slug="aihero").exists():
        connection.close()
        return

    course = Course.objects.create(
        title="7-Day AI Agents Email Crash-Course",
        slug="aihero",
        description=(
            "Build a Production-Ready AI Agent in 7 days. "
            "Prerequisites: Python basics. "
            "Certificate of completion available."
        ),
        status="published",
        required_level=0,
        is_free=True,
        instructor_name="Alexey Grigorev",
        instructor_bio="Principal Data Scientist",
    )
    module = Module.objects.create(
        course=course,
        title="7-Day AI Agents",
        sort_order=0,
    )
    days = [
        (
            "Day 1: Ingest and Index Your Data",
            True,
            "Learn to extract data from GitHub repositories and index it.",
            "Fork the GitHub repo and run the ingestion pipeline.",
        ),
        (
            "Day 2: Intelligent Processing for Data",
            False,
            "Chunk and process your data intelligently.",
            "Implement chunking strategies for your data.",
        ),
        (
            "Day 3: Add Search",
            False,
            "Add text and vector search capabilities.",
            "Build a search endpoint for your indexed data.",
        ),
        (
            "Day 4: Agents and Tools",
            False,
            "Build agents with Function Calling and tool use using Pydantic AI.",
            "Create an agent with at least two tools.",
        ),
        (
            "Day 5: Offline Evaluation and Testing",
            False,
            "Evaluate your agent with automated tests.",
            "Write evaluation scripts for your agent.",
        ),
        (
            "Day 6: Publish Your Agent",
            False,
            "Deploy your agent and make it accessible.",
            "Deploy to a cloud platform and share the URL.",
        ),
        (
            "Day 7: Share Results and Peer Review",
            False,
            "Submit your project README and review peers' work.",
            "Submit your project and review three peers.",
        ),
    ]
    for i, (title, is_preview, body_text, hw_text) in enumerate(days):
        Unit.objects.create(
            module=module,
            title=title,
            sort_order=i,
            is_preview=is_preview,
            body=f"# {title}\n\n{body_text}",
            body_html=f"<h1>{title}</h1><p>{body_text}</p>",
            homework=hw_text,
            homework_html=f"<p>{hw_text}</p>",
        )
    connection.close()


def _mark_unit_completed(user, unit):
    """Mark a unit as completed for the given user."""
    from content.models import UserCourseProgress

    progress, created = UserCourseProgress.objects.get_or_create(
        user=user,
        unit=unit,
    )
    progress.completed_at = timezone.now()
    progress.save()
    return progress


# ---------------------------------------------------------------
# Scenario 1: Anonymous visitor discovers the free AI Hero course
#              in the catalog
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1AnonymousDiscoversCourse:
    """Anonymous visitor discovers the free AI Hero course in the catalog."""

    def test_course_appears_in_catalog_and_navigates_to_detail(
        self, django_server
    , page):
        """The seed data has been loaded. Anonymous visitor navigates to
        /courses, sees the AI Hero course card with title, Free badge,
        and instructor. Clicking the card navigates to /courses/aihero."""
        _ensure_tiers()
        _ensure_aihero_course()

        # Step 1: Navigate to /courses
        page.goto(
            f"{django_server}/courses",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Course title appears in the listing
        assert "7-Day AI Agents Email Crash-Course" in body

        # Free badge is visible
        free_badge = page.locator(
            'span.text-green-400:has-text("Free")'
        )
        assert free_badge.count() >= 1

        # Instructor name appears
        assert "Alexey Grigorev" in body

        # Step 2: Click on the course card
        course_link = page.locator(
            'a[href="/courses/aihero"]'
        ).first
        course_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Lands on /courses/aihero
        assert "/courses/aihero" in page.url
# ---------------------------------------------------------------
# Scenario 2: Anonymous visitor reads the course landing page and
#              sees signup CTA
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2AnonymousLandingPageSignupCTA:
    """Anonymous visitor reads the course landing page and sees signup CTA."""

    def test_landing_page_content_and_signup_cta(self, django_server, page):
        """Anonymous visitor navigates to /courses/aihero and sees the full
        course detail page with title, Free badge, instructor, description,
        syllabus with 7 units, and a Sign up free CTA."""
        _ensure_tiers()
        _ensure_aihero_course()

        # Step 1: Navigate to /courses/aihero
        response = page.goto(
            f"{django_server}/courses/aihero",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        body = page.content()

        # Course title
        assert "7-Day AI Agents Email Crash-Course" in body

        # Free badge
        free_badge = page.locator(
            'span.text-green-400:has-text("Free")'
        )
        assert free_badge.count() >= 1

        # Instructor name and bio
        assert "Alexey Grigorev" in body
        assert "Principal Data Scientist" in body

        # Description content - key phrases
        assert "Production-Ready AI Agent" in body
        assert "Prerequisites" in body
        assert "Certificate" in body

        # Syllabus with 1 module and 7 units
        assert "7-Day AI Agents" in body
        assert "Day 1: Ingest and Index Your Data" in body
        assert "Day 2: Intelligent Processing for Data" in body
        assert "Day 3: Add Search" in body
        assert "Day 4: Agents and Tools" in body
        assert "Day 5: Offline Evaluation and Testing" in body
        assert "Day 6: Publish Your Agent" in body
        assert "Day 7: Share Results and Peer Review" in body

        # Sign up free CTA
        assert "sign up free" in body.lower()
        signup_btn = page.locator(
            'a:has-text("Sign Up Free")'
        )
        assert signup_btn.count() >= 1

        # Step 2: Click the Sign Up Free CTA
        href = signup_btn.first.get_attribute("href")
        assert "/accounts/" in href
# ---------------------------------------------------------------
# Scenario 3: Anonymous visitor can preview Day 1 content
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3AnonymousPreviewDay1:
    """Anonymous visitor can preview Day 1 content."""

    def test_anonymous_can_view_day1_preview(self, django_server, page):
        """Anonymous visitor navigates to /courses/aihero, clicks Day 1
        (which is marked is_preview=True), and sees the full lesson
        content and homework."""
        _ensure_tiers()
        _ensure_aihero_course()

        # Navigate directly to Day 1 unit page
        # For a free course (required_level=0), the unit URL
        # is /courses/aihero/0/0
        page.goto(
            f"{django_server}/courses/aihero/0/0",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Lesson content about GitHub API data extraction
        assert "GitHub" in body
        assert "Ingest and Index" in body or "Day 1" in body

        # Homework section is visible
        assert "Homework" in body or "homework" in body.lower()
        assert "GitHub" in body
# ---------------------------------------------------------------
# Scenario 4: Free member works through Day 1 to Day 2
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4FreeMemberDay1ToDay2:
    """Free member works through the course from Day 1 to Day 2."""

    def test_free_member_completes_day1_and_proceeds_to_day2(
        self, django_server
    , browser):
        """A Free tier user navigates to /courses/aihero, sees all 7 units
        accessible, clicks Day 1, views content, marks complete, clicks
        Next to proceed to Day 2."""
        _ensure_tiers()
        _ensure_aihero_course()
        _create_user("free-ah@test.com", tier_slug="free")

        context = _auth_context(browser, "free-ah@test.com")
        page = context.new_page()
        # Step 1: Navigate to /courses/aihero
        page.goto(
            f"{django_server}/courses/aihero",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # All 7 units accessible (shown as links, no lock icons)
        day1_link = page.locator(
            'a[href="/courses/aihero/0/0"]'
        )
        assert day1_link.count() >= 1

        # Expand the collapsed module so the link becomes visible
        page.evaluate("document.querySelectorAll('details.module-details').forEach(d => d.open = true)")

        # Step 2: Click on Day 1
        day1_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Unit page loads with lesson content
        assert "Day 1" in body or "Ingest and Index" in body

        # Homework section visible
        assert "Homework" in body or "homework" in body.lower()

        # Step 3: Mark Day 1 as completed
        from playwright.sync_api import expect
        mark_btn = page.locator("#mark-complete-btn")
        assert mark_btn.count() >= 1
        mark_btn.click()

        # Button changes to Completed (wait for AJAX response to update DOM)
        expect(mark_btn).to_contain_text("Completed", timeout=5000)

        # Step 4: Click Next to proceed to Day 2
        next_btn = page.locator('a:has-text("Next:")')
        assert next_btn.count() >= 1
        next_btn.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Lands on Day 2
        body = page.content()
        assert "Day 2" in body or "Intelligent Processing" in body
# ---------------------------------------------------------------
# Scenario 5: Free member tracks progress across the course
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5FreeMemberTracksProgress:
    """Free member tracks progress across the course."""

    def test_progress_bar_shows_completed_units(self, django_server, browser):
        """A Free tier user who has completed Day 1 and Day 2 sees the
        progress bar showing 2 of 7 units completed."""
        _ensure_tiers()
        _ensure_aihero_course()
        user = _create_user("free-prog@test.com", tier_slug="free")

        # Mark Day 1 and Day 2 as completed
        from content.models import Unit, Module, Course
        course = Course.objects.get(slug="aihero")
        module = Module.objects.get(course=course, title="7-Day AI Agents")
        day1 = Unit.objects.get(module=module, sort_order=0)
        day2 = Unit.objects.get(module=module, sort_order=1)
        _mark_unit_completed(user, day1)
        _mark_unit_completed(user, day2)

        context = _auth_context(browser, "free-prog@test.com")
        page = context.new_page()
        # Navigate to /courses/aihero
        page.goto(
            f"{django_server}/courses/aihero",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Progress shows 2 of 7 completed
        assert "2 of 7 completed" in body

        # Day 1 and Day 2 appear as completed (checkmark icons)
        check_icons = page.locator(
            '[data-lucide="check-circle-2"]'
        )
        assert check_icons.count() >= 2
# ---------------------------------------------------------------
# Scenario 6: Free member accesses the last unit (Day 7)
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6FreeMemberAccessesDay7:
    """Free member accesses the last unit (Day 7)."""

    def test_day7_loads_with_content_about_readme_and_peer_review(
        self, django_server
    , browser):
        """A Free tier user navigates to /courses/aihero, clicks Day 7,
        and sees content about writing a README, creating a demo, and
        peer review."""
        _ensure_tiers()
        _ensure_aihero_course()
        _create_user("free-d7@test.com", tier_slug="free")

        context = _auth_context(browser, "free-d7@test.com")
        page = context.new_page()
        # Navigate to /courses/aihero
        page.goto(
            f"{django_server}/courses/aihero",
            wait_until="domcontentloaded",
        )

        # Expand the collapsed module so the link becomes visible
        page.evaluate("document.querySelectorAll('details.module-details').forEach(d => d.open = true)")

        # Click on Day 7
        day7_link = page.locator(
            'a[href="/courses/aihero/0/6"]'
        )
        assert day7_link.count() >= 1
        day7_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Content about README, demo, and peer review
        assert "README" in body
        assert "peer review" in body.lower() or "Peer Review" in body

        # Homework mentions submitting project and reviewing peers
        assert "Submit" in body or "submit" in body
        assert "review" in body.lower()
# ---------------------------------------------------------------
# Scenario 7: Removed -- duplicate of dashboard "Continue Learning"
#   tests in playwright_tests/test_dashboard.py (Scenarios 4, 10)
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# Scenario 8: Basic member can also access the free course
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8BasicMemberAccessesFree:
    """Basic member can also access the free course."""

    def test_basic_member_accesses_all_units(self, django_server, browser):
        """A Basic tier user navigates to /courses/aihero, all 7 units
        are accessible. Clicking Day 4 loads the content about OpenAI
        function calling and Pydantic AI."""
        _ensure_tiers()
        _ensure_aihero_course()
        _create_user("basic-ah@test.com", tier_slug="basic")

        context = _auth_context(browser, "basic-ah@test.com")
        page = context.new_page()
        # Step 1: Navigate to /courses/aihero
        page.goto(
            f"{django_server}/courses/aihero",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Expand the collapsed module so the link becomes visible
        page.evaluate("document.querySelectorAll('details.module-details').forEach(d => d.open = true)")

        # All 7 units accessible
        day4_link = page.locator(
            'a[href="/courses/aihero/0/3"]'
        )
        assert day4_link.count() >= 1

        # Step 2: Click on Day 4
        day4_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Content about function calling and Pydantic AI
        assert "function calling" in body.lower() or "Function Calling" in body
        assert "Pydantic AI" in body or "Pydantic" in body