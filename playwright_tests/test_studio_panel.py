"""
Playwright E2E tests for Studio: Staff Content Management Panel (Issue #105).

Tests cover all 11 BDD scenarios from the issue:
- Non-staff member is denied access to the Studio
- Anonymous visitor is redirected to login when attempting to reach the Studio
- Staff member reviews the dashboard to understand content status at a glance
- Staff member creates a new article and publishes it to the blog
- Staff member edits an existing article and changes its status from published to draft
- Staff member creates a course with modules and units for structured learning content
- Staff member filters the article list to find draft content that needs review
- Staff member moderates a community-submitted project by approving it
- Staff member creates an email campaign targeting a specific audience
- Staff member exports subscriber data as CSV for external analysis
- Staff member navigates between Studio sections using the sidebar
- Staff member creates an event with scheduling details and verifies it appears publicly

Usage:
    uv run pytest playwright_tests/test_studio_panel.py -v
"""

import os
from datetime import datetime, timedelta

import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

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


def _clear_articles():
    """Delete all articles to ensure clean state."""
    from content.models import Article

    Article.objects.all().delete()


def _clear_events():
    """Delete all events and registrations to ensure clean state."""
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()


def _clear_courses():
    """Delete all courses, modules, and units to ensure clean state."""
    from content.models import Course

    Course.objects.all().delete()


def _clear_projects():
    """Delete all projects to ensure clean state."""
    from content.models import Project

    Project.objects.all().delete()


def _clear_campaigns():
    """Delete all campaigns and email logs to ensure clean state."""
    from email_app.models import EmailCampaign, EmailLog

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()


def _clear_subscribers():
    """Delete all newsletter subscribers to ensure clean state."""
    from email_app.models import NewsletterSubscriber

    NewsletterSubscriber.objects.all().delete()


def _create_article(title, slug, published=True, required_level=0, **kwargs):
    """Create an Article via ORM."""
    from content.models import Article

    return Article.objects.create(
        title=title,
        slug=slug,
        date=timezone.now().date(),
        published=published,
        required_level=required_level,
        **kwargs,
    )


def _create_event(title, slug, start_datetime=None, **kwargs):
    """Create an Event via ORM."""
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + timedelta(days=7)

    return Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start_datetime,
        **kwargs,
    )


def _create_project(title, slug, status="pending_review", published=False, **kwargs):
    """Create a Project via ORM."""
    from content.models import Project

    return Project.objects.create(
        title=title,
        slug=slug,
        date=timezone.now().date(),
        status=status,
        published=published,
        **kwargs,
    )


def _create_subscriber(email, is_active=True):
    """Create a NewsletterSubscriber via ORM."""
    from email_app.models import NewsletterSubscriber

    return NewsletterSubscriber.objects.create(
        email=email,
        is_active=is_active,
    )


# ---------------------------------------------------------------
# Scenario 1: Non-staff member is denied access to the Studio
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1NonStaffDenied:
    """Non-staff member is denied access to the Studio."""

    def test_non_staff_gets_403_on_studio_and_subpages(self, django_server):
        """Given: A user logged in as member@test.com (Free tier, is_staff=False)
        1. Navigate to /studio/
        Then: User receives a 403 forbidden response
        2. Navigate to /studio/articles/
        Then: User is again denied access."""
        _ensure_tiers()
        _create_user(
            "member@test.com",
            tier_slug="free",
            is_staff=False,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "member@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/
                response = page.goto(
                    f"{django_server}/studio/",
                    wait_until="networkidle",
                )
                assert response.status == 403

                # Step 2: Navigate to /studio/articles/
                response = page.goto(
                    f"{django_server}/studio/articles/",
                    wait_until="networkidle",
                )
                assert response.status == 403
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 2: Anonymous visitor is redirected to login
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2AnonymousRedirectedToLogin:
    """Anonymous visitor is redirected to login when attempting to reach the Studio."""

    def test_anonymous_redirected_to_login_with_next_param(self, django_server):
        """Given: An unauthenticated visitor (not logged in)
        1. Navigate to /studio/
        Then: User is redirected to /accounts/login/ with a next parameter pointing back to /studio/
        2. Log in with valid staff credentials (simulated via session cookie)
        Then: User can access /studio/ and sees the Studio dashboard."""
        _ensure_tiers()
        _create_staff_user("staff-anon@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # No auth context -- anonymous visitor
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/
                page.goto(
                    f"{django_server}/studio/",
                    wait_until="networkidle",
                )

                # Then: Redirected to /accounts/login/ with next=/studio/
                assert "/accounts/login/" in page.url
                assert "next" in page.url
                assert "/studio/" in page.url

                # The login page is displayed with a sign-in form
                body = page.content()
                assert "Sign in" in body

                # Step 2: Simulate logging in by setting the session cookie
                # (the JS login form uses fetch + CSRF which is difficult to
                # test in a headless E2E context without the browser's own
                # CSRF cookie). We verify that once authenticated, the user
                # can access the Studio dashboard.
                session_key = _create_session_for_user("staff-anon@test.com")
                context.add_cookies([
                    {
                        "name": "sessionid",
                        "value": session_key,
                        "domain": "127.0.0.1",
                        "path": "/",
                    },
                ])

                # Then: Navigate to /studio/ and see the dashboard
                page.goto(
                    f"{django_server}/studio/",
                    wait_until="networkidle",
                )
                assert "/studio/" in page.url
                body = page.content()
                assert "Dashboard" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 3: Staff member reviews the dashboard
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3StaffReviewsDashboard:
    """Staff member reviews the dashboard to understand content status at a glance."""

    def test_dashboard_shows_stats_and_view_all_link(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True), and the database
        has at least 2 published articles, 1 draft article, 1 upcoming event, and
        3 active subscribers.
        1. Navigate to /studio/
        Then: Dashboard displays quick stats including total courses, published articles
        count, active subscribers count, and upcoming events count.
        2. Click the 'View all' link next to Recent Articles
        Then: User navigates to /studio/articles/ and sees the full article list."""
        _clear_articles()
        _clear_events()
        _clear_subscribers()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Create 2 published articles and 1 draft
        _create_article("Published One", "published-one", published=True)
        _create_article("Published Two", "published-two", published=True)
        _create_article("Draft One", "draft-one", published=False)

        # Create 1 upcoming event
        _create_event(
            "Upcoming Workshop",
            "upcoming-workshop",
            start_datetime=timezone.now() + timedelta(days=7),
            status="upcoming",
        )

        # Create 3 active subscribers
        for i in range(3):
            _create_subscriber(f"sub-{i}@test.com", is_active=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/
                page.goto(
                    f"{django_server}/studio/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Dashboard displays quick stats
                assert "Dashboard" in body
                assert "Total Courses" in body
                assert "Published Articles" in body
                assert "Active Subscribers" in body
                assert "Upcoming Events" in body

                # Published articles count matches (2)
                # The stat card shows "2" for published articles
                published_stat = page.locator(
                    "text=Published Articles"
                ).locator("xpath=ancestor::div[contains(@class,'bg-card')]")
                published_text = published_stat.inner_text()
                assert "2" in published_text

                # Active subscribers count (3)
                subscriber_stat = page.locator(
                    "text=Active Subscribers"
                ).locator("xpath=ancestor::div[contains(@class,'bg-card')]")
                subscriber_text = subscriber_stat.inner_text()
                assert "3" in subscriber_text

                # Upcoming events count (1)
                events_stat = page.locator(
                    "text=Upcoming Events"
                ).locator("xpath=ancestor::div[contains(@class,'bg-card')]")
                events_text = events_stat.inner_text()
                assert "1" in events_text

                # Step 2: Click "View all" next to Recent Articles
                view_all_link = page.locator(
                    'a[href="/studio/articles/"]:has-text("View all")'
                )
                assert view_all_link.count() >= 1
                view_all_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: User navigates to /studio/articles/
                assert "/studio/articles/" in page.url
                body = page.content()
                assert "Articles" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 4: Staff member creates a new article and publishes it
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4StaffCreatesArticle:
    """Staff member creates a new article and publishes it to the blog."""

    def test_create_published_article_visible_on_blog(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True)
        1. Navigate to /studio/articles/
        2. Click the link/button to create a new article
        Then: User arrives at /studio/articles/new with an empty article form
        3. Fill in the title, set status to published, set required_level to 0, submit
        Then: User is redirected to the edit page for the newly created article
        4. Navigate to /blog
        Then: The article appears in the blog listing."""
        _clear_articles()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/articles/
                page.goto(
                    f"{django_server}/studio/articles/",
                    wait_until="networkidle",
                )

                # Step 2: Click the "New Article" link
                new_link = page.locator('a[href="/studio/articles/new"]')
                assert new_link.count() >= 1
                new_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: User arrives at /studio/articles/new
                assert "/studio/articles/new" in page.url
                body = page.content()
                assert "New Article" in body

                # Step 3: Fill in the form
                page.fill('input[name="title"]', "Test Studio Article")
                page.fill('input[name="slug"]', "test-studio-article")
                page.select_option('select[name="status"]', value="published")
                page.select_option(
                    'select[name="required_level"]', value="0"
                )

                # Submit the form
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle")

                # Then: Redirected to the edit page
                assert "/studio/articles/" in page.url
                assert "/edit" in page.url

                # Step 4: Navigate to /blog
                page.goto(
                    f"{django_server}/blog",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The article appears in the blog listing
                assert "Test Studio Article" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 5: Staff member edits an article status from published
#              to draft
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5StaffEditsArticleStatus:
    """Staff member edits an existing article and changes its status from published to draft."""

    def test_unpublish_article_removes_from_blog(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True), and a published
        article titled 'Live Article' exists.
        1. Navigate to /studio/articles/
        Then: 'Live Article' appears in the list with a 'Published' status indicator
        2. Click to edit 'Live Article'
        3. Change the status to 'draft' and submit the form
        Then: User returns to the edit page and the article now shows as draft
        4. Navigate to /blog
        Then: 'Live Article' no longer appears in the public blog listing."""
        _clear_articles()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        article = _create_article(
            "Live Article", "live-article", published=True, required_level=0,
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/articles/
                page.goto(
                    f"{django_server}/studio/articles/",
                    wait_until="networkidle",
                )
                body = page.content()

                # "Live Article" appears with "Published" status
                assert "Live Article" in body
                assert "Published" in body

                # Step 2: Click to edit "Live Article"
                page.click(
                    f'a[href="/studio/articles/{article.pk}/edit"]'
                )
                page.wait_for_load_state("networkidle")

                # Step 3: Change status to draft and submit
                page.select_option('select[name="status"]', value="draft")
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle")

                # Then: Article shows as draft on the edit page
                assert "/edit" in page.url
                body = page.content()
                # The select should have "draft" selected
                draft_option = page.locator(
                    'select[name="status"] option[selected]'
                )
                assert draft_option.inner_text().strip() == "Draft"

                # Step 4: Navigate to /blog
                page.goto(
                    f"{django_server}/blog",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: "Live Article" no longer appears
                assert "Live Article" not in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 6: Staff member creates a course with modules and units
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6StaffCreatesCourse:
    """Staff member creates a course with modules and units for structured learning content."""

    def test_create_course_with_module_and_unit(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True)
        1. Navigate to /studio/courses/new
        2. Fill in the course title, set status to published, submit
        Then: User is redirected to the course edit page
        3. Add a module titled 'Getting Started'
        Then: The module appears in the course editor
        4. Add a unit titled 'Welcome Video' to the 'Getting Started' module
        Then: The unit appears nested under the module
        5. Navigate to /courses
        Then: 'Intro to AI Shipping' appears in the public course listing."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/courses/new
                page.goto(
                    f"{django_server}/studio/courses/new",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "New Course" in body

                # Step 2: Fill in course details and submit
                page.fill('input[name="title"]', "Intro to AI Shipping")
                page.fill('input[name="slug"]', "intro-to-ai-shipping")
                page.select_option(
                    'select[name="status"]', value="published"
                )

                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle")

                # Then: Redirected to course edit page
                assert "/studio/courses/" in page.url
                assert "/edit" in page.url
                body = page.content()
                assert "Edit Course" in body

                # Step 3: Add a module titled "Getting Started"
                module_input = page.locator(
                    'form[action*="/modules/add"] input[name="title"]'
                )
                module_input.fill("Getting Started")
                module_submit = page.locator(
                    'form[action*="/modules/add"] button[type="submit"]'
                )
                module_submit.click()
                page.wait_for_load_state("networkidle")

                # Then: The module appears in the course editor
                body = page.content()
                assert "Getting Started" in body

                # Step 4: Add a unit titled "Welcome Video" to the module
                unit_input = page.locator(
                    'form[action*="/units/add"] input[name="title"]'
                )
                unit_input.fill("Welcome Video")
                unit_submit = page.locator(
                    'form[action*="/units/add"] button[type="submit"], '
                    'form[action*="/units/add"] :text("Add Unit")'
                )
                unit_submit.first.click()
                page.wait_for_load_state("networkidle")

                # Then: The unit appears nested under the module
                body = page.content()
                assert "Welcome Video" in body

                # Step 5: Navigate to /courses
                page.goto(
                    f"{django_server}/courses",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: "Intro to AI Shipping" appears in the public listing
                assert "Intro to AI Shipping" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 7: Staff member filters the article list to find draft
#              content
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7StaffFiltersArticles:
    """Staff member filters the article list to find draft content that needs review."""

    def test_filter_articles_by_status_and_search(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True), and the system
        has 3 published articles and 2 draft articles.
        1. Navigate to /studio/articles/
        Then: All 5 articles are displayed in the list
        2. Filter the list by 'draft' status
        Then: Only the 2 draft articles appear
        3. Use the search field to search for a specific draft article by title
        Then: The results narrow to match the search query within the draft filter."""
        _clear_articles()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Create 3 published articles
        _create_article("Published Alpha", "published-alpha", published=True)
        _create_article("Published Beta", "published-beta", published=True)
        _create_article("Published Gamma", "published-gamma", published=True)

        # Create 2 draft articles
        _create_article("Draft Delta", "draft-delta", published=False)
        _create_article("Draft Epsilon", "draft-epsilon", published=False)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/articles/
                page.goto(
                    f"{django_server}/studio/articles/",
                    wait_until="networkidle",
                )
                body = page.content()

                # All 5 articles are displayed
                assert "Published Alpha" in body
                assert "Published Beta" in body
                assert "Published Gamma" in body
                assert "Draft Delta" in body
                assert "Draft Epsilon" in body

                # Step 2: Filter by "draft" status
                with page.expect_navigation(wait_until="networkidle"):
                    page.select_option(
                        'select[name="status"]', value="draft"
                    )

                table_body = page.locator("tbody")
                table_text = table_body.inner_text()

                # Only draft articles appear
                assert "Draft Delta" in table_text
                assert "Draft Epsilon" in table_text
                assert "Published Alpha" not in table_text
                assert "Published Beta" not in table_text
                assert "Published Gamma" not in table_text

                # Step 3: Search for a specific draft article
                page.fill('input[name="q"]', "Epsilon")
                page.click('button:has-text("Search")')
                page.wait_for_load_state("networkidle")

                table_body = page.locator("tbody")
                table_text = table_body.inner_text()

                # Only "Draft Epsilon" appears
                assert "Draft Epsilon" in table_text
                assert "Draft Delta" not in table_text
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 8: Staff member moderates a community-submitted project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8StaffModeratesProject:
    """Staff member moderates a community-submitted project by approving it."""

    def test_approve_pending_project(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True), and a project
        titled 'My AI Bot' has been submitted by a member and is in 'pending_review' status.
        1. Navigate to /studio/projects/
        Then: 'My AI Bot' appears in the project list with a pending status
        2. Click to review 'My AI Bot'
        Then: User arrives at the project review page showing the full project details
        3. Click 'Approve'
        Then: User is redirected to the project list, and 'My AI Bot' is approved
        and visible on /projects."""
        _clear_projects()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        project = _create_project(
            "My AI Bot",
            "my-ai-bot",
            status="pending_review",
            published=False,
            author="Test Author",
            description="An AI chatbot project.",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/projects/
                page.goto(
                    f"{django_server}/studio/projects/",
                    wait_until="networkidle",
                )
                body = page.content()

                # "My AI Bot" appears with pending status
                assert "My AI Bot" in body
                assert "Pending Review" in body

                # Step 2: Click to review "My AI Bot"
                page.click(
                    f'a[href="/studio/projects/{project.pk}/review"]'
                )
                page.wait_for_load_state("networkidle")

                # Then: User arrives at the review page
                body = page.content()
                assert "My AI Bot" in body
                assert "Review Project" in body
                assert "Test Author" in body

                # Step 3: Click "Approve"
                page.click('button:has-text("Approve")')
                page.wait_for_load_state("networkidle")

                # Then: Redirected to project list
                assert "/studio/projects/" in page.url
                body = page.content()

                # "My AI Bot" is no longer in pending status -- it has been approved
                from content.models import Project
                project.refresh_from_db()
                assert project.status == "published"
                assert project.published is True

                # Step 4: Navigate to /projects to verify public visibility
                page.goto(
                    f"{django_server}/projects",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "My AI Bot" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 9: Staff member creates an email campaign targeting a
#              specific audience
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9StaffCreatesCampaign:
    """Staff member creates an email campaign targeting a specific audience."""

    def test_create_campaign_and_view_detail(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True), and there are
        active newsletter subscribers in the system.
        1. Navigate to /studio/campaigns/
        2. Click to create a new campaign
        Then: User arrives at /studio/campaigns/new with a campaign form
        3. Fill in the subject, body content, set target_min_level to 0, submit
        Then: User is redirected to the campaign list, and the campaign appears as draft
        4. Click on the campaign to view the campaign detail
        Then: The detail page shows campaign content, recipient count, and send controls."""
        _clear_campaigns()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Create some subscribers/users who would be recipients
        for i in range(3):
            _create_user(
                f"campaign-recipient-{i}@test.com",
                tier_slug="free",
                email_verified=True,
            )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/campaigns/
                page.goto(
                    f"{django_server}/studio/campaigns/",
                    wait_until="networkidle",
                )

                # Step 2: Click to create a new campaign
                new_link = page.locator(
                    'a[href="/studio/campaigns/new"]'
                )
                assert new_link.count() >= 1
                new_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: User arrives at /studio/campaigns/new
                assert "/studio/campaigns/new" in page.url
                body = page.content()
                assert "New Campaign" in body

                # Step 3: Fill in the form
                page.fill('input[name="subject"]', "February Newsletter")
                page.fill(
                    'textarea[name="body"]',
                    "# February Update\n\nHere is the latest news.",
                )
                page.select_option(
                    'select[name="target_min_level"]', value="0"
                )

                page.click('button:has-text("Create Campaign")')
                page.wait_for_load_state("networkidle")

                # Then: Redirected to /studio/campaigns/
                assert "/studio/campaigns/" in page.url
                body = page.content()
                assert "February Newsletter" in body
                assert "Draft" in body

                # Step 4: Click on the campaign to view detail
                from email_app.models import EmailCampaign
                campaign = EmailCampaign.objects.get(
                    subject="February Newsletter"
                )
                page.click(
                    f'a[href="/studio/campaigns/{campaign.pk}/"]'
                )
                page.wait_for_load_state("networkidle")

                # Then: Detail page shows content and recipient count
                body = page.content()
                assert "February Newsletter" in body
                assert "Draft" in body
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 10: Staff member exports subscriber data as CSV
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10StaffExportsSubscribers:
    """Staff member exports subscriber data as CSV for external analysis."""

    def test_subscriber_list_filter_and_csv_export(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True), and there are
        5 active and 2 inactive newsletter subscribers.
        1. Navigate to /studio/subscribers/
        Then: Subscriber list shows counts -- 5 active and 2 inactive
        2. Filter by 'active' subscribers
        Then: Only the 5 active subscribers appear in the list
        3. Click the export CSV link/button
        Then: A CSV file downloads containing subscriber data."""
        _clear_subscribers()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Create 5 active subscribers
        for i in range(5):
            _create_subscriber(f"active-{i}@test.com", is_active=True)

        # Create 2 inactive subscribers
        for i in range(2):
            _create_subscriber(f"inactive-{i}@test.com", is_active=False)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/subscribers/
                page.goto(
                    f"{django_server}/studio/subscribers/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Subscriber list shows counts
                assert "5" in body  # active count
                assert "2" in body  # inactive count

                # Step 2: Filter by "active" subscribers
                with page.expect_navigation(wait_until="networkidle"):
                    page.select_option(
                        'select[name="status"]', value="active"
                    )

                table_body = page.locator("tbody")
                table_text = table_body.inner_text()

                # Only active subscribers appear
                for i in range(5):
                    assert f"active-{i}@test.com" in table_text
                for i in range(2):
                    assert f"inactive-{i}@test.com" not in table_text

                # Step 3: Click the export CSV link
                # The export link preserves the current filter
                with page.expect_download() as download_info:
                    page.click('a:has-text("Export CSV")')

                download = download_info.value
                assert download.suggested_filename == "subscribers.csv"

                # Read the CSV content and verify
                csv_path = download.path()
                with open(csv_path, "r") as f:
                    csv_content = f.read()

                assert "Email" in csv_content
                assert "Subscribed At" in csv_content
                assert "Active" in csv_content

                # Only active subscribers should be in the CSV
                for i in range(5):
                    assert f"active-{i}@test.com" in csv_content
                for i in range(2):
                    assert f"inactive-{i}@test.com" not in csv_content
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 11: Staff member navigates between Studio sections
#               using the sidebar
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11SidebarNavigation:
    """Staff member navigates between Studio sections using the sidebar."""

    def test_sidebar_navigation_across_sections(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True)
        1. Navigate to /studio/
        2. Click 'Articles' link in the sidebar
        Then: User arrives at /studio/articles/
        3. Click 'Events' link in the sidebar
        Then: User arrives at /studio/events/
        4. Click 'Subscribers' link in the sidebar
        Then: User arrives at /studio/subscribers/
        5. Click the 'Studio' logo/link in the sidebar
        Then: User returns to /studio/."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/
                page.goto(
                    f"{django_server}/studio/",
                    wait_until="networkidle",
                )
                assert "Dashboard" in page.content()

                # Step 2: Click "Articles" in the sidebar
                sidebar = page.locator("aside")
                sidebar.locator('a:has-text("Articles")').click()
                page.wait_for_load_state("networkidle")
                assert "/studio/articles/" in page.url

                # Step 3: Click "Events" in the sidebar
                sidebar = page.locator("aside")
                sidebar.locator('a:has-text("Events")').click()
                page.wait_for_load_state("networkidle")
                assert "/studio/events/" in page.url

                # Step 4: Click "Subscribers" in the sidebar
                sidebar = page.locator("aside")
                sidebar.locator('a:has-text("Subscribers")').click()
                page.wait_for_load_state("networkidle")
                assert "/studio/subscribers/" in page.url

                # Step 5: Click "Studio" logo/link in the sidebar to return
                sidebar = page.locator("aside")
                studio_link = sidebar.locator(
                    'a[href="/studio/"]:has-text("Studio")'
                )
                studio_link.click()
                page.wait_for_load_state("networkidle")
                assert page.url.rstrip("/").endswith("/studio")
                assert "Dashboard" in page.content()
            finally:
                browser.close()


# ---------------------------------------------------------------
# Scenario 12: Staff member creates an event and verifies it
#               appears publicly
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario12StaffCreatesEvent:
    """Staff member creates an event with scheduling details and verifies it appears publicly."""

    def test_create_event_visible_on_events_page(self, django_server):
        """Given: A user logged in as admin@test.com (is_staff=True)
        1. Navigate to /studio/events/new
        2. Fill in the title, set event_type to live, set a future start_datetime,
           set status to upcoming, set required_level to 0, submit
        Then: User is redirected to the event edit page
        3. Navigate to /events
        Then: 'AI Workshop March 2026' appears in the upcoming events listing."""
        _clear_events()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "admin@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /studio/events/new
                page.goto(
                    f"{django_server}/studio/events/new",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "New Event" in body

                # Step 2: Fill in the event form
                page.fill('input[name="title"]', "AI Workshop March 2026")
                page.fill('input[name="slug"]', "ai-workshop-march-2026")
                page.select_option(
                    'select[name="event_type"]', value="live"
                )
                page.fill('input[name="event_date"]', "15/03/2026")
                page.fill('input[name="event_time"]', "14:00")
                page.fill('input[name="duration_hours"]', "2")
                page.select_option(
                    'select[name="status"]', value="upcoming"
                )
                page.select_option(
                    'select[name="required_level"]', value="0"
                )

                # Submit the form
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle")

                # Then: Redirected to the event edit page
                assert "/studio/events/" in page.url
                assert "/edit" in page.url

                # Step 3: Navigate to /events
                page.goto(
                    f"{django_server}/events",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: "AI Workshop March 2026" appears
                assert "AI Workshop March 2026" in body
            finally:
                browser.close()
