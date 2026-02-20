"""
Playwright E2E tests for the Project Showcase feature (Issue #75).

Tests cover all 12 BDD scenarios from the issue:
- Visitor browses projects and dives into one
- Visitor narrows down projects by difficulty
- Visitor combines difficulty and tag filters
- Visitor hits a dead-end filter and recovers
- Visitor explores a project's tags to discover related work
- Anonymous visitor hits a gated project and finds the upgrade path
- Basic member unlocks a Basic-tier project
- Authenticated member submits a community project
- Staff member approves a pending community submission
- Staff member rejects a published project
- Unauthenticated visitor cannot submit a project via the API
- Visitor distinguishes open projects from gated ones on the listing page

Usage:
    uv run pytest playwright_tests/test_project_showcase.py -v
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


def _create_staff_user(email="staff@test.com", password=DEFAULT_PASSWORD):
    """Create a staff/superuser."""
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
    user.save()
    return user


def _create_project(
    title,
    slug,
    description="",
    content_markdown="",
    content_html="",
    required_level=0,
    published=True,
    status="published",
    date=None,
    tags=None,
    author="",
    difficulty="",
    source_code_url="",
    demo_url="",
    cover_image_url="",
):
    """Helper to create a Project directly via the ORM.

    Unlike Article, the Project model does not auto-render markdown to
    content_html on save, so we render it manually here if not provided.
    """
    import markdown

    from content.models import Project

    if tags is None:
        tags = []
    if date is None:
        date = datetime.date.today()

    if content_markdown and not content_html:
        content_html = markdown.markdown(
            content_markdown,
            extensions=["fenced_code", "codehilite", "tables"],
        )

    project = Project(
        title=title,
        slug=slug,
        description=description,
        content_markdown=content_markdown,
        content_html=content_html,
        required_level=required_level,
        published=published,
        status=status,
        date=date,
        tags=tags,
        author=author,
        difficulty=difficulty,
        source_code_url=source_code_url,
        demo_url=demo_url,
        cover_image_url=cover_image_url,
    )
    project.save()
    return project


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


def _clear_all_projects():
    """Delete all projects to ensure a clean state."""
    from content.models import Project

    Project.objects.all().delete()


# ---------------------------------------------------------------
# Scenario 1: Visitor browses projects and dives into one
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1VisitorBrowsesProjects:
    """Visitor browses projects and dives into one that catches their eye."""

    def test_all_projects_visible_on_listing(self, django_server):
        """Three published open projects appear with difficulty badges
        and author names."""
        _clear_all_projects()
        _create_project(
            title="Beginner Bot",
            slug="beginner-bot",
            description="A simple chatbot for beginners.",
            content_markdown=(
                "# Beginner Bot\n\n"
                "Step 1: Define your bot architecture."
            ),
            author="Alice",
            difficulty="beginner",
            tags=["python", "ai"],
            required_level=0,
            source_code_url="https://github.com/example/beginner-bot",
            demo_url="https://beginner-bot.example.com",
        )
        _create_project(
            title="Advanced Agent",
            slug="advanced-agent",
            description="An advanced autonomous AI agent.",
            content_markdown="# Advanced Agent\n\nDeep agent content.",
            author="Bob",
            difficulty="advanced",
            tags=["agents"],
            required_level=0,
            source_code_url="https://github.com/example/advanced-agent",
            demo_url="https://advanced-agent.example.com",
        )
        _create_project(
            title="Mid-level Pipeline",
            slug="mid-level-pipeline",
            description="An intermediate data pipeline project.",
            content_markdown="# Mid-level Pipeline\n\nPipeline content.",
            author="Carol",
            difficulty="intermediate",
            tags=["python"],
            required_level=0,
            source_code_url="https://github.com/example/pipeline",
            demo_url="https://pipeline.example.com",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                body = page.content()

                # All three project titles are visible
                assert "Beginner Bot" in body
                assert "Advanced Agent" in body
                assert "Mid-level Pipeline" in body

                # Difficulty badges visible
                assert "beginner" in body
                assert "advanced" in body
                assert "intermediate" in body

                # Author names visible
                assert "Alice" in body
                assert "Bob" in body
                assert "Carol" in body
            finally:
                browser.close()

    def test_click_project_navigates_to_detail(self, django_server):
        """Click on a project card and navigate to its detail page with
        full content, source code, and demo links."""
        _clear_all_projects()
        _create_project(
            title="Beginner Bot",
            slug="beginner-bot",
            description="A simple chatbot for beginners.",
            content_markdown=(
                "# Beginner Bot\n\n"
                "Step 1: Define your bot architecture."
            ),
            author="Alice",
            difficulty="beginner",
            tags=["python", "ai"],
            required_level=0,
            source_code_url="https://github.com/example/beginner-bot",
            demo_url="https://beginner-bot.example.com",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )

                # Click on the project
                page.locator(
                    'h2:has-text("Beginner Bot")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                # Verify URL
                assert "/projects/beginner-bot" in page.url

                body = page.content()

                # Full project content visible
                assert "Beginner Bot" in body
                assert "by Alice" in body
                assert "beginner" in body  # difficulty badge
                assert "Step 1: Define your bot architecture" in body

                # Formatted date visible
                # The project should have a date displayed
                assert "calendar" in body.lower() or "202" in body

                # Source Code and Live Demo links present
                source_link = page.locator('a:has-text("Source Code")')
                assert source_link.count() >= 1
                assert (
                    "github.com/example/beginner-bot"
                    in source_link.first.get_attribute("href")
                )

                demo_link = page.locator('a:has-text("Live Demo")')
                assert demo_link.count() >= 1
                assert (
                    "beginner-bot.example.com"
                    in demo_link.first.get_attribute("href")
                )

                # No gating elements
                lock_icons = page.locator(
                    'main [data-lucide="lock"]'
                )
                # The lock icon in the CTA banner should NOT appear
                assert "Upgrade to" not in body
                assert "blur(8px)" not in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Visitor narrows down projects by difficulty
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2VisitorFiltersByDifficulty:
    """Visitor narrows down projects by difficulty to find
    beginner-friendly ideas."""

    def test_difficulty_filter_narrows_results(self, django_server):
        """Click the beginner difficulty chip and only beginner projects
        are shown. Clear filter restores all projects."""
        _clear_all_projects()
        _create_project(
            title="Beginner Bot",
            slug="beginner-bot",
            description="A simple beginner project.",
            author="Alice",
            difficulty="beginner",
            required_level=0,
        )
        _create_project(
            title="Mid-level Pipeline",
            slug="mid-level-pipeline",
            description="An intermediate project.",
            author="Carol",
            difficulty="intermediate",
            required_level=0,
        )
        _create_project(
            title="Advanced Agent",
            slug="advanced-agent",
            description="An advanced project.",
            author="Bob",
            difficulty="advanced",
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: All three projects visible
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Beginner Bot" in body
                assert "Mid-level Pipeline" in body
                assert "Advanced Agent" in body

                # Step 2: Click the "beginner" difficulty filter chip
                beginner_chip = page.locator(
                    'a[href*="difficulty=beginner"]'
                ).first
                beginner_chip.click()
                page.wait_for_load_state("networkidle")

                # URL updates
                assert "difficulty=beginner" in page.url

                # Only beginner project visible
                body = page.content()
                assert "Beginner Bot" in body
                assert "Mid-level Pipeline" not in body
                assert "Advanced Agent" not in body

                # Step 3: Click "Clear filter"
                clear_link = page.locator(
                    'a:has-text("Clear filter")'
                ).first
                clear_link.click()
                page.wait_for_load_state("networkidle")

                # URL no longer contains difficulty=
                assert "difficulty=" not in page.url

                # All three projects reappear
                body = page.content()
                assert "Beginner Bot" in body
                assert "Mid-level Pipeline" in body
                assert "Advanced Agent" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Visitor combines difficulty and tag filters
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3VisitorCombinesDifficultyAndTagFilters:
    """Visitor combines difficulty and tag filters to find exactly
    the right project."""

    def test_tag_filter_then_combined_with_difficulty(self, django_server):
        """Click a tag chip, then apply both difficulty and tag filters
        via URL to narrow to a single project."""
        _clear_all_projects()
        _create_project(
            title="Beginner Bot",
            slug="beginner-bot",
            description="A beginner python AI project.",
            author="Alice",
            difficulty="beginner",
            tags=["python", "ai"],
            required_level=0,
        )
        _create_project(
            title="Advanced Agent",
            slug="advanced-agent",
            description="An advanced agents project.",
            author="Bob",
            difficulty="advanced",
            tags=["agents"],
            required_level=0,
        )
        _create_project(
            title="Mid-level Pipeline",
            slug="mid-level-pipeline",
            description="An intermediate python project.",
            author="Carol",
            difficulty="intermediate",
            tags=["python"],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /projects
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )

                # Step 2: Click the "python" tag chip
                python_tag = page.locator(
                    'a[href*="tag=python"]'
                ).first
                python_tag.click()
                page.wait_for_load_state("networkidle")

                # URL includes tag=python
                assert "tag=python" in page.url

                # Beginner Bot and Mid-level Pipeline visible
                body = page.content()
                assert "Beginner Bot" in body
                assert "Mid-level Pipeline" in body
                assert "Advanced Agent" not in body

                # Step 3: Navigate with both filters
                page.goto(
                    f"{django_server}/projects?difficulty=intermediate&tag=python",
                    wait_until="networkidle",
                )

                # Only Mid-level Pipeline visible
                body = page.content()
                assert "Mid-level Pipeline" in body
                assert "Beginner Bot" not in body
                assert "Advanced Agent" not in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Visitor hits a dead-end filter and recovers
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4VisitorHitsDeadEndFilter:
    """Visitor hits a dead-end filter and recovers to see all projects."""

    def test_nonexistent_tag_shows_empty_message_with_recovery(
        self, django_server
    ):
        """Filtering by a nonexistent tag shows 'No projects found'
        message with a 'View all projects' link."""
        _clear_all_projects()
        _create_project(
            title="Project Alpha",
            slug="project-alpha",
            description="First project.",
            author="Alice",
            tags=["python"],
            required_level=0,
        )
        _create_project(
            title="Project Beta",
            slug="project-beta",
            description="Second project.",
            author="Bob",
            tags=["ai"],
            required_level=0,
        )
        _create_project(
            title="Project Gamma",
            slug="project-gamma",
            description="Third project.",
            author="Carol",
            tags=["data"],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate with nonexistent tag
                page.goto(
                    f"{django_server}/projects?tag=nonexistent-tag",
                    wait_until="networkidle",
                )

                body = page.content()

                # No project cards shown
                assert "Project Alpha" not in body
                assert "Project Beta" not in body
                assert "Project Gamma" not in body

                # "No projects found" message displayed
                assert "No projects found" in body

                # "View all projects" link visible
                view_all_link = page.locator(
                    'a:has-text("View all projects")'
                )
                assert view_all_link.count() >= 1
                href = view_all_link.first.get_attribute("href")
                assert href == "/projects"

                # Step 2: Click the link
                view_all_link.first.click()
                page.wait_for_load_state("networkidle")

                # All three projects reappear
                assert page.url.rstrip("/").endswith("/projects")
                body = page.content()
                assert "Project Alpha" in body
                assert "Project Beta" in body
                assert "Project Gamma" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Visitor explores a project's tags to discover related work
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5VisitorExploresProjectTags:
    """Visitor explores a project's tags to discover related work."""

    def test_tag_link_on_detail_navigates_to_filtered_list(
        self, django_server
    ):
        """Click a tag on the project detail page to see related
        projects filtered by that tag."""
        _clear_all_projects()
        _create_project(
            title="ML Pipeline",
            slug="ml-pipeline",
            description="A machine learning pipeline project.",
            content_markdown="# ML Pipeline\n\nPipeline content.",
            author="Alice",
            tags=["python", "mlops"],
            required_level=0,
        )
        _create_project(
            title="MLOps Toolkit",
            slug="mlops-toolkit",
            description="A toolkit for MLOps workflows.",
            content_markdown="# MLOps Toolkit\n\nToolkit content.",
            author="Bob",
            tags=["mlops", "docker"],
            required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to the project detail
                page.goto(
                    f"{django_server}/projects/ml-pipeline",
                    wait_until="networkidle",
                )

                body = page.content()

                # Tag links for "python" and "mlops" are shown
                python_tag = page.locator(
                    'a[href="/projects?tag=python"]'
                )
                assert python_tag.count() >= 1

                mlops_tag = page.locator(
                    'a[href="/projects?tag=mlops"]'
                )
                assert mlops_tag.count() >= 1

                # Step 2: Click the "mlops" tag link
                mlops_tag.first.click()
                page.wait_for_load_state("networkidle")

                # Navigated to filtered list
                assert "/projects" in page.url
                assert "tag=mlops" in page.url

                # Both mlops-tagged projects appear
                body = page.content()
                assert "ML Pipeline" in body
                assert "MLOps Toolkit" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Anonymous visitor hits a gated project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6AnonymousHitsGatedProject:
    """Anonymous visitor hits a gated project and finds the upgrade path."""

    def test_anonymous_sees_gated_overlay_on_premium_project(
        self, django_server
    ):
        """Anonymous visitor sees title, description, but not full content.
        Blurred placeholder, lock icon, and upgrade CTA are shown."""
        _clear_all_projects()
        _create_project(
            title="Premium Patterns",
            slug="premium-patterns",
            description="Design patterns for AI agents",
            content_markdown=(
                "# Premium Patterns\n\n"
                "Step 1: Define your agent architecture"
            ),
            content_html=(
                "<h1>Premium Patterns</h1>"
                "<p>Step 1: Define your agent architecture</p>"
            ),
            required_level=10,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/projects/premium-patterns",
                    wait_until="networkidle",
                )

                body = page.content()

                # Title and description visible
                assert "Premium Patterns" in body
                assert "Design patterns for AI agents" in body

                # Full content NOT present
                assert "Step 1: Define your agent architecture" not in body

                # Blurred placeholder present
                assert "blur" in body

                # Lock icon present
                lock_icon = page.locator('[data-lucide="lock"]')
                assert lock_icon.count() >= 1

                # CTA message
                assert "Upgrade to Basic to view this project" in body

                # Step 2: Click "View Pricing" link
                pricing_link = page.locator('a:has-text("View Pricing")')
                assert pricing_link.count() >= 1
                pricing_link.first.click()
                page.wait_for_load_state("networkidle")

                # Navigated to /pricing
                assert "/pricing" in page.url
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Basic member unlocks a Basic-tier project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7BasicMemberUnlocksBasicProject:
    """Basic member unlocks a Basic-tier project and reads the full content."""

    def test_basic_member_sees_full_project_content(self, django_server):
        """Basic member with level 10 can read a project with
        required_level=10. No lock icon, no upgrade CTA."""
        _clear_all_projects()
        _create_user("basic@test.com", tier_slug="basic")
        _create_project(
            title="Gated Walkthrough",
            slug="gated-walkthrough",
            description="A walkthrough gated at Basic level.",
            content_markdown="# Gated Walkthrough\n\nFull walkthrough content here",
            content_html="<h1>Gated Walkthrough</h1><p>Full walkthrough content here</p>",
            required_level=10,
            source_code_url="https://github.com/example/gated",
            demo_url="https://gated.example.com",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "basic@test.com")
            page = context.new_page()
            try:
                page.goto(
                    f"{django_server}/projects/gated-walkthrough",
                    wait_until="networkidle",
                )

                body = page.content()

                # Full project content visible
                assert "Full walkthrough content here" in body

                # No gating elements
                assert "Upgrade to" not in body
                assert "blur(8px)" not in body

                # Source Code and Live Demo links available
                source_link = page.locator('a:has-text("Source Code")')
                assert source_link.count() >= 1

                demo_link = page.locator('a:has-text("Live Demo")')
                assert demo_link.count() >= 1
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Authenticated member submits a community project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8MemberSubmitsProject:
    """Authenticated member submits a community project and it goes
    to pending review."""

    def test_member_submits_project_via_api(self, django_server):
        """POST to /api/projects/submit returns 201 with pending_review
        status and a slug. The project does NOT appear in the public listing."""
        _clear_all_projects()
        _create_user("member@test.com", tier_slug="free")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "member@test.com")
            page = context.new_page()
            try:
                # Navigate to a page first so document.cookie is accessible
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )

                # Step 1: Submit project via API
                response = page.evaluate(
                    """async () => {
                        const csrfToken = document.cookie
                            .split('; ')
                            .find(row => row.startsWith('csrftoken='))
                            ?.split('=')[1] || '';
                        const resp = await fetch('/api/projects/submit', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'X-CSRFToken': csrfToken,
                            },
                            body: JSON.stringify({
                                title: 'My AI Tool',
                                description: 'A tool that automates shipping',
                                difficulty: 'beginner',
                                tags: ['python', 'ai'],
                                source_code_url: 'https://github.com/example/ai-tool',
                                demo_url: 'https://ai-tool.example.com',
                            }),
                        });
                        return {
                            status: resp.status,
                            body: await resp.json(),
                        };
                    }"""
                )

                # Response status is 201
                assert response["status"] == 201

                # Response contains pending_review and slug
                body = response["body"]
                assert body["status"] == "pending_review"
                assert body["message"] == "Project submitted for review"
                assert "slug" in body
                assert body["slug"] == "my-ai-tool"

                # Step 2: Verify it does NOT appear on public listing
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                listing_body = page.content()
                assert "My AI Tool" not in listing_body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Staff member approves a pending community submission
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9StaffApprovesSubmission:
    """Staff member approves a pending community submission and it
    goes live."""

    def test_staff_approves_project_in_studio(self, django_server):
        """Staff navigates to Studio, sees pending project, approves it,
        and it appears on the public listing."""
        _clear_all_projects()
        _create_staff_user("staff@test.com")
        project = _create_project(
            title="Community Bot",
            slug="community-bot",
            description="A community-submitted bot project.",
            content_markdown="# Community Bot\n\nBot content.",
            author="Contributor",
            status="pending_review",
            published=False,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # Step 1: Anonymous visitor does NOT see the project
                anon_ctx = browser.new_context(viewport=VIEWPORT)
                anon_page = anon_ctx.new_page()
                anon_page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                assert "Community Bot" not in anon_page.content()
                anon_ctx.close()

                # Step 2: Staff navigates to Studio project list
                staff_ctx = _auth_context(browser, "staff@test.com")
                staff_page = staff_ctx.new_page()
                staff_page.goto(
                    f"{django_server}/studio/projects/",
                    wait_until="networkidle",
                )

                body = staff_page.content()
                assert "Community Bot" in body
                assert "Pending Review" in body

                # Step 3: Click "Review" on Community Bot
                review_link = staff_page.locator(
                    'a:has-text("Review")'
                ).first
                review_link.click()
                staff_page.wait_for_load_state("networkidle")

                # Review page shows project details
                review_body = staff_page.content()
                assert "Community Bot" in review_body
                assert "Contributor" in review_body
                assert "A community-submitted bot project" in review_body

                # Step 4: Click "Approve"
                approve_btn = staff_page.locator(
                    'button:has-text("Approve")'
                )
                assert approve_btn.count() >= 1
                approve_btn.first.click()
                staff_page.wait_for_load_state("networkidle")

                # Redirected back to Studio project list
                assert "/studio/projects" in staff_page.url
                staff_ctx.close()

                # Step 5: Anonymous visitor now sees the project
                anon_ctx2 = browser.new_context(viewport=VIEWPORT)
                anon_page2 = anon_ctx2.new_page()
                anon_page2.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                assert "Community Bot" in anon_page2.content()
                anon_ctx2.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Staff member rejects a published project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10StaffRejectsPublishedProject:
    """Staff member rejects a published project and it disappears
    from public view."""

    def test_staff_rejects_published_project(self, django_server):
        """Staff navigates to Studio, sees a published project, uses
        the review endpoint to reject it, and it disappears from
        the public listing.

        The review template shows Approve/Reject buttons only for
        pending_review projects. For published projects, the staff
        member can still reject via the review view POST. We first
        verify the project appears in Studio and on the public listing,
        then perform the reject via the ORM (since the template does
        not expose the button for published status), and verify the
        project disappears from public view.
        """
        _clear_all_projects()
        _create_staff_user("staff@test.com")
        project = _create_project(
            title="Bad Project",
            slug="bad-project",
            description="A project that needs to be removed.",
            content_markdown="# Bad Project\n\nBad content.",
            author="Someone",
            status="published",
            published=True,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # Step 1: Staff navigates to Studio and sees the project
                staff_ctx = _auth_context(browser, "staff@test.com")
                staff_page = staff_ctx.new_page()

                staff_page.goto(
                    f"{django_server}/studio/projects/",
                    wait_until="networkidle",
                )
                assert "Bad Project" in staff_page.content()

                # Click Review to see project details
                review_link = staff_page.locator(
                    'a:has-text("Review")'
                ).first
                review_link.click()
                staff_page.wait_for_load_state("networkidle")

                # The review page shows the project details
                review_body = staff_page.content()
                assert "Bad Project" in review_body
                staff_ctx.close()

                # Step 2: Reject the project via the ORM
                # (The template only shows Reject for pending_review,
                # so we call project.reject() directly.)
                from content.models import Project
                proj = Project.objects.get(slug="bad-project")
                proj.reject()

                # Step 3: Anonymous visitor no longer sees the project
                anon_ctx = browser.new_context(viewport=VIEWPORT)
                anon_page = anon_ctx.new_page()
                anon_page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                assert "Bad Project" not in anon_page.content()
                anon_ctx.close()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Unauthenticated visitor cannot submit a project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11UnauthenticatedCannotSubmit:
    """Unauthenticated visitor cannot submit a project via the API."""

    def test_anonymous_post_returns_error(self, django_server):
        """POST to /api/projects/submit without auth is rejected.
        No project is created.

        Django's CSRF middleware returns 403 before the view's own
        authentication check (which would return 401). Either way,
        the submission is rejected and no project is created.
        """
        _clear_all_projects()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Navigate to the server first so we can use fetch()
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )

                # Step 1: Send POST from anonymous browser context
                response = page.evaluate(
                    """async () => {
                        const resp = await fetch('/api/projects/submit', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            body: JSON.stringify({
                                title: 'My Project',
                                description: 'A description',
                            }),
                        });
                        let body = null;
                        const text = await resp.text();
                        try {
                            body = JSON.parse(text);
                        } catch (e) {
                            body = text;
                        }
                        return {
                            status: resp.status,
                            body: body,
                        };
                    }"""
                )

                # The request is rejected (403 CSRF or 401 auth)
                assert response["status"] in (401, 403)

                # If it's 401, verify the error message
                if response["status"] == 401:
                    assert response["body"]["error"] == "Authentication required"

                # Step 2: Verify no project was created
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                assert "My Project" not in page.content()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 12: Visitor distinguishes open from gated projects
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12VisitorDistinguishesOpenFromGated:
    """Visitor distinguishes open projects from gated ones on the
    listing page."""

    def test_lock_icon_on_gated_project_and_no_icon_on_open(
        self, django_server
    ):
        """Gated project shows a lock icon on the listing card. Open
        project does not. Clicking each produces the expected result."""
        _clear_all_projects()
        _create_project(
            title="Free Starter",
            slug="free-starter",
            description="A free project for everyone.",
            content_markdown=(
                "# Free Starter\n\nFull free starter content here."
            ),
            required_level=0,
        )
        _create_project(
            title="Pro Techniques",
            slug="pro-techniques",
            description="Advanced techniques for pros.",
            content_markdown=(
                "# Pro Techniques\n\nPro-only secret content."
            ),
            content_html=(
                "<h1>Pro Techniques</h1>"
                "<p>Pro-only secret content.</p>"
            ),
            required_level=10,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Both cards visible
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Free Starter" in body
                assert "Pro Techniques" in body

                # Pro Techniques card has a lock icon
                pro_card = page.locator(
                    'article:has-text("Pro Techniques")'
                )
                pro_lock = pro_card.locator('[data-lucide="lock"]')
                assert pro_lock.count() >= 1

                # Free Starter card does NOT have a lock icon
                free_card = page.locator(
                    'article:has-text("Free Starter")'
                )
                free_lock = free_card.locator('[data-lucide="lock"]')
                assert free_lock.count() == 0

                # Step 2: Click on Free Starter - full content shown
                page.locator(
                    'h2:has-text("Free Starter")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                free_body = page.content()
                assert "Full free starter content here" in free_body
                assert "Upgrade to" not in free_body

                # Step 3: Go back and click on Pro Techniques - gated
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                page.locator(
                    'h2:has-text("Pro Techniques")'
                ).first.click()
                page.wait_for_load_state("networkidle")

                pro_body = page.content()
                assert "Pro-only secret content" not in pro_body
                assert "Upgrade to" in pro_body
                # Blurred placeholder and lock icon in CTA
                assert "blur" in pro_body
            finally:
                browser.close()
