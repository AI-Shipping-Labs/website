"""
Playwright E2E tests for Course Admin CRUD (Issue #80).

Tests cover all 11 BDD scenarios from the issue:
- Staff member creates a new course and finds it in the course list
- Staff member edits a course and changes its status from draft to published
- Staff member builds a course structure by adding modules and units
- Staff member edits a unit with lesson content and marks it as preview
- Staff member reorders modules to restructure the course curriculum
- Staff member filters the course list to find only draft courses
- Staff member searches for a specific course by title
- Staff member creates a free lead-magnet course accessible to all members
- Non-staff user is denied access to the Studio course management
- Staff member sees an empty state when no courses exist yet
- Published course with modules and units is browsable by a member on the public site

Usage:
    uv run pytest playwright_tests/test_course_admin.py -v
"""

import json
import os

import pytest

from playwright_tests.conftest import (
    DJANGO_BASE_URL,
    VIEWPORT,
    DEFAULT_PASSWORD,
    ensure_tiers as _ensure_tiers,
    create_user as _create_user,
    create_staff_user as _create_staff_user,
    create_session_for_user as _create_session_for_user,
    auth_context as _auth_context,
)


os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_courses():
    """Delete all courses, modules, units, and progress to ensure clean state."""
    from content.models import Course, UserCourseProgress

    UserCourseProgress.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_course(
    title,
    slug,
    description="",
    cover_image_url="",
    instructor_name="",
    instructor_bio="",
    required_level=0,
    status="published",
    is_free=False,
    discussion_url="",
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
        cover_image_url=cover_image_url,
        instructor_name=instructor_name,
        instructor_bio=instructor_bio,
        required_level=required_level,
        status=status,
        is_free=is_free,
        discussion_url=discussion_url,
        tags=tags,
    )
    course.save()
    connection.close()
    return course


def _create_module(course, title, sort_order=1):
    """Create a Module within a course."""
    from content.models import Module
    from django.utils.text import slugify

    module = Module(
        course=course,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
    )
    module.save()
    connection.close()
    return module


def _create_unit(
    module,
    title,
    sort_order=1,
    video_url="",
    body="",
    homework="",
    is_preview=False,
):
    """Create a Unit within a module."""
    from content.models import Unit
    from django.utils.text import slugify

    unit = Unit(
        module=module,
        title=title,
        slug=slugify(title),
        sort_order=sort_order,
        video_url=video_url,
        body=body,
        homework=homework,
        is_preview=is_preview,
    )
    unit.save()
    connection.close()
    return unit


# ---------------------------------------------------------------
# Scenario 1: Staff member creates a new course and finds it in
#              the course list
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1StaffCreatesNewCourse:
    """Staff member creates a new course and finds it in the course list."""

    def test_studio_course_create_url_removed_and_course_appears_in_list(
        self, django_server
    , browser):
        """The /studio/courses/new URL has been removed (#152). Verify it
        returns 404. Then create a course via ORM and confirm it appears
        in the Studio course list with correct status and access level."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        # Step 1: Verify /studio/courses/new returns 404
        response = page.goto(
            f"{django_server}/studio/courses/new",
            wait_until="domcontentloaded",
        )
        assert response.status == 404

        # Step 2: Verify the course list page no longer has a "New Course" button
        page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Courses" in body
        new_btn = page.locator('a:has-text("New Course")')
        assert new_btn.count() == 0

        # Step 3: Create a course via ORM and verify it appears in the list
        _create_course(
            title="AI Engineering Fundamentals",
            slug="ai-engineering-fundamentals",
            description="Learn to build AI apps",
            instructor_name="Alexey Grigorev",
            status="draft",
            required_level=10,
            tags=["ai", "engineering"],
        )

        page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "AI Engineering Fundamentals" appears in the course list
        assert "AI Engineering Fundamentals" in body

        # Status shows "Draft"
        assert "Draft" in body

        # Access level shows "Level 10"
        assert "Level 10" in body
# ---------------------------------------------------------------
# Scenario 2: Staff member edits a course and changes its status
#              from draft to published
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2StaffEditsCourseStatusChange:
    """Staff member edits a course and changes its status from draft
    to published."""

    def test_staff_edits_course_publishes_and_appears_on_public_listing(
        self, django_server
    , browser):
        """A staff user navigates to /studio/courses/, clicks on a draft
        course to edit it, changes the status to Published, updates the
        description, and submits. The course then appears on the public
        /courses page."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        course = _create_course(
            title="Intro to LLMs",
            slug="intro-to-llms",
            description="An introduction to large language models.",
            status="draft",
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/courses/
        page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Intro to LLMs" in body

        # Step 2: Click on "Intro to LLMs" to edit it
        edit_link = page.locator(
            f'a[href*="/studio/courses/{course.pk}/edit"]'
        ).first
        edit_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: The edit form is pre-populated with existing data
        # Scope to the course form (not the module add form)
        course_form = page.locator('form:has(button:has-text("Save Changes"))')
        title_input = course_form.locator('input[name="title"]')
        assert title_input.input_value() == "Intro to LLMs"

        # Step 3: Change status to Published
        page.select_option('select[name="status"]', "published")

        # Step 4: Update the description
        page.fill(
            'textarea[name="description"]',
            "A comprehensive introduction to large language models",
        )

        # Step 5: Submit the form
        page.click('button:has-text("Save Changes")')
        page.wait_for_load_state("domcontentloaded")

        # Then: The changes are saved and user stays on edit page
        assert "/edit" in page.url
        body = page.content()
        assert "Edit Course" in body

        # Step 6: Navigate to /courses (the public listing)
        page.goto(
            f"{django_server}/courses",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "Intro to LLMs" appears in the public course catalog
        assert "Intro to LLMs" in body
# ---------------------------------------------------------------
# Scenario 3: Staff member builds a course structure by adding
#              modules and units
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3StaffBuildsModulesAndUnits:
    """Staff member builds a course structure by adding modules
    and units."""

    def test_staff_adds_modules_and_units_to_course(
        self, django_server
    , browser):
        """A staff user on the course edit page adds two modules and
        one unit per module, verifying each appears after adding."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        course = _create_course(
            title="Python for AI",
            slug="python-for-ai",
            description="A course on Python for AI.",
            status="draft",
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to the course edit page
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Modules" in body

        # Step 2: Type "Getting Started" in the module title field
        # and click "Add Module"
        module_input = page.locator(
            'form[action*="/modules/add"] input[name="title"]'
        )
        module_input.fill("Getting Started")
        page.click('button:has-text("Add Module")')
        page.wait_for_load_state("domcontentloaded")

        # Then: "Getting Started" module appears
        body = page.content()
        assert "Getting Started" in body

        # Step 3: Within the "Getting Started" module, add a unit
        # Find the unit form inside the Getting Started module
        unit_input = page.locator(
            'input[name="title"][placeholder*="New unit"]'
        ).first
        unit_input.fill("Setting Up Your Environment")
        page.locator('button:has-text("Add Unit")').first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: "Setting Up Your Environment" appears as a unit
        body = page.content()
        assert "Setting Up Your Environment" in body

        # Step 4: Add a second module "Data Structures"
        module_input = page.locator(
            'form[action*="/modules/add"] input[name="title"]'
        )
        module_input.fill("Data Structures")
        page.click('button:has-text("Add Module")')
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Data Structures" in body

        # Step 5: Add a unit "Lists and Dictionaries" to the
        # "Data Structures" module. It will be the second module
        # card, so get the last unit form.
        unit_inputs = page.locator(
            'input[name="title"][placeholder*="New unit"]'
        )
        unit_inputs.last.fill("Lists and Dictionaries")
        page.locator('button:has-text("Add Unit")').last.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: The course now has 2 modules, each with 1 unit
        body = page.content()
        assert "Getting Started" in body
        assert "Setting Up Your Environment" in body
        assert "Data Structures" in body
        assert "Lists and Dictionaries" in body
# ---------------------------------------------------------------
# Scenario 4: Staff member edits a unit with lesson content and
#              marks it as preview
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4StaffEditsUnitWithContent:
    """Staff member edits a unit with lesson content and marks it
    as preview."""

    def test_staff_edits_unit_and_marks_as_preview(
        self, django_server
    , browser):
        """A staff user navigates to the course edit page, clicks Edit
        next to a unit, fills in video URL, body, homework, checks the
        preview checkbox, saves, and sees the Preview badge on return."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        course = _create_course(
            title="Setup Course",
            slug="setup-course",
            description="A course about setup.",
            status="draft",
        )
        module = _create_module(course, "Module 1", sort_order=1)
        unit = _create_unit(module, "Setup Lesson", sort_order=1)

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to the course edit page
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Setup Lesson" in body

        # Step 2: Click "Edit" next to the "Setup Lesson" unit
        edit_link = page.locator(
            f'a[href*="/studio/units/{unit.pk}/edit"]'
        )
        assert edit_link.count() >= 1
        edit_link.first.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User lands on the unit edit form with breadcrumb
        assert f"/studio/units/{unit.pk}/edit" in page.url
        body = page.content()
        assert "Edit Unit" in body

        # Breadcrumb navigation shows course title
        assert "Setup Course" in body

        # Step 3: Enter a video URL, body, and homework
        page.fill(
            'input[name="video_url"]',
            "https://www.youtube.com/watch?v=test123",
        )
        page.fill(
            'textarea[name="body"]',
            "# Setting Up\nThis is the lesson content for setup.",
        )
        page.fill(
            'textarea[name="homework"]',
            "# Homework\nComplete the setup exercise.",
        )

        # Step 4: Check the "Preview unit" checkbox
        page.check('input[name="is_preview"]')

        # Step 5: Click "Save Unit"
        page.click('button:has-text("Save Unit")')
        page.wait_for_load_state("domcontentloaded")

        # Then: User is redirected back to the course edit page
        assert f"/studio/courses/{course.pk}/edit" in page.url

        # Step 6: The "Setup Lesson" unit now shows a "Preview" badge
        body = page.content()
        assert "Setup Lesson" in body
        preview_badge = page.locator(
            'span:has-text("Preview")'
        )
        assert preview_badge.count() >= 1
# ---------------------------------------------------------------
# Scenario 5: Staff member reorders modules to restructure the
#              course curriculum
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5StaffReordersModules:
    """Staff member reorders modules to restructure the course
    curriculum."""

    def test_staff_reorders_modules_via_api(
        self, django_server
    , browser):
        """A staff user on a course with 3 modules sends a reorder
        request via the API, then refreshes the page to verify the
        new order is persisted."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        course = _create_course(
            title="Reorder Course",
            slug="reorder-course",
            description="A course for testing reordering.",
            status="draft",
        )
        mod_basics = _create_module(course, "Basics", sort_order=0)
        mod_intermediate = _create_module(
            course, "Intermediate", sort_order=1,
        )
        mod_advanced = _create_module(course, "Advanced", sort_order=2)

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to the course edit page
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Basics" in body
        assert "Intermediate" in body
        assert "Advanced" in body

        # Step 2: Send reorder request via the API
        # Move "Advanced" to 0, "Basics" to 1, "Intermediate" to 2
        reorder_payload = json.dumps([
            {"id": mod_advanced.pk, "sort_order": 0},
            {"id": mod_basics.pk, "sort_order": 1},
            {"id": mod_intermediate.pk, "sort_order": 2},
        ])

        # Use page.evaluate to send a fetch request
        result = page.evaluate(
            """async (payload) => {
                const csrfToken = document.querySelector(
                    'input[name="csrfmiddlewaretoken"]'
                )?.value || '';
                const resp = await fetch(
                    '/api/admin/modules/reorder',
                    {
                        method: 'PUT',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken,
                        },
                        body: payload,
                    }
                );
                return {status: resp.status, body: await resp.json()};
            }""",
            reorder_payload,
        )

        assert result["status"] == 200
        assert result["body"]["status"] == "ok"

        # Then: Refresh the page to verify the new order
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Verify order by checking the module cards in sequence
        # The modules should appear as: Advanced, Basics, Intermediate
        module_cards = page.locator(
            'div[data-module-id]'
        )
        assert module_cards.count() == 3

        first_module_text = module_cards.nth(0).inner_text()
        second_module_text = module_cards.nth(1).inner_text()
        third_module_text = module_cards.nth(2).inner_text()

        assert "Advanced" in first_module_text
        assert "Basics" in second_module_text
        assert "Intermediate" in third_module_text
# ---------------------------------------------------------------
# Scenario 6: Staff member filters the course list to find only
#              draft courses
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6StaffFiltersByStatus:
    """Staff member filters the course list to find only draft courses."""

    def test_staff_filters_by_draft_status(
        self, django_server
    , browser):
        """Staff navigates to /studio/courses/, sees all courses, selects
        'Draft' from the status filter, sees only draft courses, then
        clears the filter to see all again."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        _create_course(
            title="Published Course",
            slug="published-course",
            status="published",
        )
        _create_course(
            title="Draft Course",
            slug="draft-course",
            status="draft",
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/courses/
        page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: All courses (draft and published) are listed
        assert "Published Course" in body
        assert "Draft Course" in body

        # Step 2: Select "Draft" from the status filter dropdown
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.select_option('select[name="status"]', "draft")

        body = page.content()

        # Then: Only draft courses are shown
        assert "Draft Course" in body
        # Published courses are hidden
        table_body = page.locator("tbody")
        table_text = table_body.inner_text()
        assert "Published Course" not in table_text

        # Step 3: Select "All statuses" to clear the filter
        with page.expect_navigation(wait_until="domcontentloaded"):
            page.select_option('select[name="status"]', "")

        body = page.content()

        # Then: All courses are visible again
        assert "Published Course" in body
        assert "Draft Course" in body
# ---------------------------------------------------------------
# Scenario 7: Staff member searches for a specific course by title
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7StaffSearchesByTitle:
    """Staff member searches for a specific course by title."""

    def test_staff_searches_and_finds_matching_course(
        self, django_server
    , browser):
        """Staff navigates to /studio/courses/, types 'Python' in the
        search field, sees only the matching course, then clears the
        search to see all courses."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        _create_course(
            title="Python Basics",
            slug="python-basics",
            status="published",
        )
        _create_course(
            title="AI Engineering",
            slug="ai-engineering",
            status="published",
        )
        _create_course(
            title="Web Development",
            slug="web-development",
            status="published",
        )

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/courses/
        page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Python Basics" in body
        assert "AI Engineering" in body
        assert "Web Development" in body

        # Step 2: Type "Python" in the search field and submit
        page.fill('input[name="q"]', "Python")
        page.click('button:has-text("Search")')
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Then: Only "Python Basics" appears
        table_body = page.locator("tbody")
        table_text = table_body.inner_text()
        assert "Python Basics" in table_text
        assert "AI Engineering" not in table_text
        assert "Web Development" not in table_text

        # Step 3: Clear the search field and submit
        page.fill('input[name="q"]', "")
        page.click('button:has-text("Search")')
        page.wait_for_load_state("domcontentloaded")

        body = page.content()

        # Then: All three courses are visible again
        assert "Python Basics" in body
        assert "AI Engineering" in body
        assert "Web Development" in body
# ---------------------------------------------------------------
# Scenario 8: Staff member creates a free lead-magnet course
#              accessible to all members
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8StaffCreatesFreeCourse:
    """Staff member creates a free lead-magnet course accessible
    to all members."""

    def test_studio_course_create_url_returns_404_and_free_course_visible(
        self, django_server
    , browser):
        """The /studio/courses/new URL has been removed (#152). Verify it
        returns 404. Then create a free course via ORM and verify it
        appears on the public /courses listing."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()

        # Step 1: Verify /studio/courses/new returns 404
        response = page.goto(
            f"{django_server}/studio/courses/new",
            wait_until="domcontentloaded",
        )
        assert response.status == 404

        # Step 2: Create a free course via ORM
        _create_course(
            title="Free AI Starter Kit",
            slug="free-ai-starter-kit",
            required_level=0,
            is_free=True,
            status="published",
        )

        # Step 3: Navigate to /courses (public listing)
        page.goto(
            f"{django_server}/courses",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "Free AI Starter Kit" appears in the catalog
        assert "Free AI Starter Kit" in body
# ---------------------------------------------------------------
# Scenario 9: Non-staff user is denied access to the Studio
#              course management
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9NonStaffDeniedAccess:
    """Non-staff user is denied access to the Studio course management."""

    def test_non_staff_member_cannot_access_studio_courses(
        self, django_server
    , browser):
        """A Main-tier non-staff user navigating to /studio/courses/ is
        denied access. Trying /studio/courses/new directly is also denied."""
        _clear_courses()
        _ensure_tiers()
        _create_user("member@test.com", tier_slug="main")

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/courses/
        response = page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )

        # Then: Denied access -- either redirect to login or 403
        is_redirected = "/accounts/login" in page.url
        is_forbidden = response.status == 403

        assert is_redirected or is_forbidden, (
            f"Expected redirect to login or 403, "
            f"got status={response.status} url={page.url}"
        )

        # The user should NOT see the course management UI
        body = page.content()
        assert "New Course" not in body

        # Step 2: Try to access /studio/courses/new directly
        # This URL was removed (#152), so it returns 404 for everyone
        response = page.goto(
            f"{django_server}/studio/courses/new",
            wait_until="domcontentloaded",
        )

        assert response.status == 404, (
            f"Expected 404 for removed URL, "
            f"got status={response.status} url={page.url}"
        )
# ---------------------------------------------------------------
# Scenario 10: Staff member sees an empty state when no courses
#               exist yet
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10EmptyStateCourseList:
    """Staff member sees an empty state when no courses exist yet."""

    def test_staff_sees_empty_state_message(
        self, django_server
    , browser):
        """With no courses in the system, a staff user sees the
        'No courses found' message. The 'New Course' button has been
        removed since content creation now happens via GitHub sync (#152)."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")

        context = _auth_context(browser, "staff@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/courses/
        page.goto(
            f"{django_server}/studio/courses/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "No courses found" message is shown
        assert "No courses found" in body

        # Then: "New Course" button is no longer present (#152)
        new_course_btn = page.locator(
            'a:has-text("New Course")'
        )
        assert new_course_btn.count() == 0
# ---------------------------------------------------------------
# Scenario 11: Published course with modules and units is
#               browsable by a member on the public site
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11PublishedCourseBrowsableByMember:
    """Published course with modules and units is browsable by a
    member on the public site."""

    def test_member_browses_published_course_with_syllabus(
        self, django_server
    , browser):
        """A staff user creates and publishes a course with a module
        and unit. Then a Basic-tier member logs in and browses the
        course catalog and detail page, seeing the syllabus."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("staff@test.com")
        _create_user("basic@test.com", tier_slug="basic")

        # Create a published course with module and unit via ORM
        # (simulating what staff would have created)
        course = _create_course(
            title="AI Workshop",
            slug="ai-workshop",
            description="A hands-on AI workshop.",
            status="published",
            required_level=10,
            instructor_name="Alexey Grigorev",
        )
        module = _create_module(course, "Week 1", sort_order=1)
        _create_unit(
            module, "Introduction", sort_order=1,
            body="# Welcome\nThis is the introduction.",
        )


        # Step 1: Log in as basic@test.com (Basic tier)
        context = _auth_context(browser, "basic@test.com")
        page = context.new_page()
        # Step 2: Navigate to /courses
        page.goto(
            f"{django_server}/courses",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "AI Workshop" appears in the public course listing
        assert "AI Workshop" in body

        # Step 3: Click on "AI Workshop"
        workshop_link = page.locator(
            'a[href="/courses/ai-workshop"]'
        ).first
        workshop_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: The course detail page shows the syllabus
        assert "/courses/ai-workshop" in page.url
        body = page.content()

        # Module title and unit title are visible
        assert "Week 1" in body
        assert "Introduction" in body

        # Instructor name
        assert "Alexey Grigorev" in body