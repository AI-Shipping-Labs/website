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

Note: the original Scenario 12 ("Staff member creates an event...")
was removed in commit 004fcc5 (closes #166). Events now sync from
the content repo and the create-event view no longer exists.

Usage:
    uv run pytest playwright_tests/test_studio_panel.py -v
"""

import os
from datetime import timedelta

import pytest
from django.utils import timezone

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

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection


def _clear_articles():
    """Delete all articles to ensure clean state."""
    from content.models import Article

    Article.objects.all().delete()
    connection.close()


def _clear_events():
    """Delete all events and registrations to ensure clean state."""
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _clear_courses():
    """Delete all courses, modules, and units to ensure clean state."""
    from content.models import Course

    Course.objects.all().delete()
    connection.close()


def _clear_projects():
    """Delete all projects to ensure clean state."""
    from content.models import Project

    Project.objects.all().delete()
    connection.close()


def _clear_campaigns():
    """Delete all campaigns and email logs to ensure clean state."""
    from email_app.models import EmailCampaign, EmailLog

    EmailLog.objects.all().delete()
    EmailCampaign.objects.all().delete()
    connection.close()


def _clear_subscribers():
    """Reset User-backed newsletter state to ensure clean subscriber counts."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    User.objects.all().update(
        unsubscribed=True,
        email_preferences={"newsletter": False},
    )
    connection.close()


def _create_article(title, slug, published=True, required_level=0, **kwargs):
    """Create an Article via ORM."""
    from content.models import Article

    connection.close()
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

    connection.close()
    return Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start_datetime,
        **kwargs,
    )


def _create_project(title, slug, status="pending_review", published=False, **kwargs):
    """Create a Project via ORM."""
    from content.models import Project

    connection.close()
    return Project.objects.create(
        title=title,
        slug=slug,
        date=timezone.now().date(),
        status=status,
        published=published,
        **kwargs,
    )


def _create_newsletter_user(email, is_active=True):
    """Create a User with canonical newsletter subscription state."""
    user = _create_user(
        email,
        email_verified=True,
        unsubscribed=not is_active,
    )
    user.email_preferences["newsletter"] = bool(is_active)
    user.save(update_fields=["email_preferences"])
    connection.close()
    return user


def _studio_summary_metric(page, label):
    summary = page.locator('section:has(h2:has-text("Summary"))')
    return summary.locator("div.bg-card").filter(has_text=label).first


# ---------------------------------------------------------------
# Scenario 1: Non-staff member is denied access to the Studio
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1NonStaffDenied:
    """Non-staff member is denied access to the Studio."""

    def test_non_staff_gets_403_on_studio_and_subpages(self, django_server, browser):
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

        context = _auth_context(browser, "member@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/
        response = page.goto(
            f"{django_server}/studio/",
            wait_until="domcontentloaded",
        )
        assert response.status == 403

        # Step 2: Navigate to /studio/articles/
        response = page.goto(
            f"{django_server}/studio/articles/",
            wait_until="domcontentloaded",
        )
        assert response.status == 403
# ---------------------------------------------------------------
# Scenario 2: Anonymous visitor is redirected to login
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2AnonymousRedirectedToLogin:
    """Anonymous visitor is redirected to login when attempting to reach the Studio."""

    def test_anonymous_redirected_to_login_with_next_param(self, django_server, page):
        """Given: An unauthenticated visitor (not logged in)
        1. Navigate to /studio/
        Then: User is redirected to /accounts/login/ with a next parameter pointing back to /studio/
        2. Log in with valid staff credentials (simulated via session cookie)
        Then: User can access /studio/ and sees the Studio dashboard."""
        _ensure_tiers()
        _create_staff_user("staff-anon@test.com")

        # No auth context -- anonymous visitor
        # Step 1: Navigate to /studio/
        page.goto(
            f"{django_server}/studio/",
            wait_until="domcontentloaded",
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
        page.context.add_cookies([
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
            wait_until="domcontentloaded",
        )
        assert "/studio/" in page.url
        body = page.content()
        assert "Dashboard" in body
# ---------------------------------------------------------------
# Scenario 3: Staff member reviews the dashboard
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario3StaffReviewsDashboard:
    """Staff member reviews the dashboard to understand content status at a glance."""

    def test_dashboard_shows_stats_and_view_all_link(self, django_server, browser):
        """Given: A user logged in as admin@test.com (is_staff=True), and the database
        has at least 2 published articles, 1 draft article, 1 upcoming event, and
        3 active subscribers.
        1. Navigate to /studio/
        Then: Dashboard displays current summary metrics for courses, articles,
        subscribers, and events.
        2. Use the Studio article navigation link
        Then: User navigates to /studio/articles/ and sees the full article list."""
        _clear_articles()
        _clear_events()
        _clear_courses()
        _clear_subscribers()
        _ensure_tiers()
        admin = _create_staff_user("admin@test.com")
        admin.unsubscribed = True
        admin.email_preferences["newsletter"] = False
        admin.save(update_fields=["unsubscribed", "email_preferences"])
        connection.close()

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
            _create_newsletter_user(f"sub-{i}@test.com", is_active=True)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/
        page.goto(
            f"{django_server}/studio/",
            wait_until="domcontentloaded",
        )
        # Dashboard displays quick stats
        page.get_by_role("heading", name="Dashboard").wait_for()
        summary = page.locator('section:has(h2:has-text("Summary"))')
        assert summary.is_visible()

        courses_stat = _studio_summary_metric(page, "Courses")
        assert "0" in courses_stat.inner_text()
        assert "0 published" in courses_stat.inner_text()

        articles_stat = _studio_summary_metric(page, "Articles")
        assert "2" in articles_stat.inner_text()
        assert "3 total" in articles_stat.inner_text()

        subscriber_stat = _studio_summary_metric(page, "Subscribers")
        assert "3" in subscriber_stat.inner_text()

        events_stat = _studio_summary_metric(page, "Events")
        assert "1" in events_stat.inner_text()
        assert "1 total" in events_stat.inner_text()

        # Step 2: Click the current Studio article-management link.
        articles_link = page.locator('a[href="/studio/articles/"]').first
        assert articles_link.count() == 1
        articles_link.click()
        page.wait_for_load_state("domcontentloaded")

        # Then: User navigates to /studio/articles/
        assert "/studio/articles/" in page.url
        body = page.content()
        assert "Articles" in body
# ---------------------------------------------------------------
# Scenario 4: Staff member creates a new article and publishes it
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4StaffCreatesArticle:
    """Staff member creates a new article and publishes it to the blog."""

    def test_article_create_url_removed_and_article_visible_on_blog(self, django_server, browser):
        """The /studio/articles/new URL has been removed (#152). Verify it
        returns 404. Then create an article via ORM and verify it appears
        on the blog listing."""
        _clear_articles()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Step 1: Verify /studio/articles/new returns 404
        response = page.goto(
            f"{django_server}/studio/articles/new",
            wait_until="domcontentloaded",
        )
        assert response.status == 404

        # Step 2: Verify the article list no longer has a "New Article" link
        page.goto(
            f"{django_server}/studio/articles/",
            wait_until="domcontentloaded",
        )
        new_link = page.locator('a[href="/studio/articles/new"]')
        assert new_link.count() == 0

        # Step 3: Create an article via ORM
        _create_article(
            "Test Studio Article", "test-studio-article",
            published=True, required_level=0,
        )

        # Step 4: Navigate to /blog and verify the article appears
        page.goto(
            f"{django_server}/blog",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Test Studio Article" in body
# ---------------------------------------------------------------
# Scenario 5: Staff member edits an article status from published
#              to draft
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario5StaffEditsArticleStatus:
    """Staff member edits an existing article and changes its status from published to draft."""

    def test_unpublish_article_removes_from_blog(self, django_server, browser):
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

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/articles/
        page.goto(
            f"{django_server}/studio/articles/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # "Live Article" appears with "Published" status
        assert "Live Article" in body
        assert "Published" in body

        # Step 2: Click to edit "Live Article"
        page.click(
            f'a[href="/studio/articles/{article.pk}/edit"]'
        )
        page.wait_for_load_state("domcontentloaded")

        # Step 3: Change status to draft and submit
        page.select_option('select[name="status"]', value="draft")
        page.click('button[type="submit"]')
        page.wait_for_load_state("domcontentloaded")

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
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "Live Article" no longer appears
        assert "Live Article" not in body
# ---------------------------------------------------------------
# Scenario 6: Staff member creates a course with modules and units
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario6StaffCreatesCourse:
    """Staff member creates a course with modules and units for structured learning content."""

    def test_course_create_url_removed_and_course_with_modules_visible(self, django_server, browser):
        """The /studio/courses/new URL has been removed (#152). Verify it
        returns 404. Then create a course via ORM, add modules and units
        via the Studio edit page, and verify it appears publicly."""
        _clear_courses()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()

        # Step 1: Verify /studio/courses/new returns 404
        response = page.goto(
            f"{django_server}/studio/courses/new",
            wait_until="domcontentloaded",
        )
        assert response.status == 404

        # Step 2: Create a course via ORM
        from django.db import connection

        from content.models import Course

        course = Course(
            title="Intro to AI Shipping",
            slug="intro-to-ai-shipping",
            status="published",
        )
        course.save()
        connection.close()

        # Step 3: Navigate to the course edit page and add a module
        page.goto(
            f"{django_server}/studio/courses/{course.pk}/edit",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "Edit Course" in body

        module_input = page.locator(
            'form[action*="/modules/add"] input[name="title"]'
        )
        module_input.fill("Getting Started")
        module_submit = page.locator(
            'form[action*="/modules/add"] button[type="submit"]'
        )
        module_submit.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Getting Started" in body

        # Step 4: Expand the module disclosure (compact UI #491) and add
        # a unit titled "Welcome Video" to the module.
        page.locator('[data-testid="module-summary"]').first.click()
        unit_input = page.locator(
            'form[action*="/units/add"] input[name="title"]'
        )
        unit_input.fill("Welcome Video")
        unit_submit = page.locator(
            'form[action*="/units/add"] button[type="submit"], '
            'form[action*="/units/add"] :text("Add Unit")'
        )
        unit_submit.first.click()
        page.wait_for_load_state("domcontentloaded")

        body = page.content()
        assert "Welcome Video" in body

        # Step 5: Navigate to /courses
        page.goto(
            f"{django_server}/courses",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # Then: "Intro to AI Shipping" appears in the public listing
        assert "Intro to AI Shipping" in body
# ---------------------------------------------------------------
# Scenario 7: Staff member filters the article list to find draft
#              content
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario7StaffFiltersArticles:
    """Staff member filters the article list to find draft content that needs review."""

    def test_filter_articles_by_status_and_search(self, django_server, browser):
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

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/articles/
        page.goto(
            f"{django_server}/studio/articles/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # All 5 articles are displayed
        assert "Published Alpha" in body
        assert "Published Beta" in body
        assert "Published Gamma" in body
        assert "Draft Delta" in body
        assert "Draft Epsilon" in body

        # Step 2: Filter by "draft" status
        with page.expect_navigation(wait_until="domcontentloaded"):
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
        page.wait_for_load_state("domcontentloaded")

        table_body = page.locator("tbody")
        table_text = table_body.inner_text()

        # Only "Draft Epsilon" appears
        assert "Draft Epsilon" in table_text
        assert "Draft Delta" not in table_text
# ---------------------------------------------------------------
# Scenario 8: Staff member moderates a community-submitted project
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario8StaffModeratesProject:
    """Staff member moderates a community-submitted project by approving it."""

    def test_approve_pending_project(self, django_server, browser):
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

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/projects/
        page.goto(
            f"{django_server}/studio/projects/",
            wait_until="domcontentloaded",
        )
        body = page.content()

        # "My AI Bot" appears with pending status
        assert "My AI Bot" in body
        assert "Pending Review" in body

        # Step 2: Click to review "My AI Bot"
        page.click(
            f'a[href="/studio/projects/{project.pk}/review"]'
        )
        page.wait_for_load_state("domcontentloaded")

        # Then: User arrives at the review page
        body = page.content()
        assert "My AI Bot" in body
        assert "Review Project" in body
        assert "Test Author" in body

        # Step 3: Click "Approve"
        page.click('button:has-text("Approve")')
        page.wait_for_load_state("domcontentloaded")

        # Then: Redirected to project list
        assert "/studio/projects/" in page.url
        body = page.content()

        # "My AI Bot" is no longer in pending status -- it has been approved
        project.refresh_from_db()
        assert project.status == "published"
        assert project.published is True

        # Step 4: Navigate to /projects to verify public visibility
        page.goto(
            f"{django_server}/projects",
            wait_until="domcontentloaded",
        )
        body = page.content()
        assert "My AI Bot" in body
# ---------------------------------------------------------------
# Scenario 9: Staff member creates an email campaign targeting a
#              specific audience
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario9StaffCreatesCampaign:
    """Staff member creates an email campaign targeting a specific audience."""

    def test_create_campaign_and_view_detail(self, django_server, browser):
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

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/campaigns/
        page.goto(
            f"{django_server}/studio/campaigns/",
            wait_until="domcontentloaded",
        )

        # Step 2: Click to create a new campaign
        new_link = page.locator(
            'a[href="/studio/campaigns/new"]'
        )
        assert new_link.count() >= 1
        new_link.first.click()
        page.wait_for_load_state("domcontentloaded")

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

        page.click('button:has-text("Save as Draft")')
        page.wait_for_load_state("domcontentloaded")

        # Then: Redirected to the new campaign's detail page
        # (issue #292: the create flow now lands on the detail page
        # rather than the list).
        from email_app.models import EmailCampaign
        campaign = EmailCampaign.objects.get(
            subject="February Newsletter"
        )
        assert f"/studio/campaigns/{campaign.pk}/" in page.url
        body = page.content()
        assert "February Newsletter" in body
        assert "Draft" in body
# ---------------------------------------------------------------
# Scenario 10: Staff member exports subscriber data as CSV
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario10StaffExportsSubscribers:
    """Staff member exports user/subscriber data as CSV for external analysis.

    Page moved from ``/studio/subscribers/`` to ``/studio/users/`` in #271.
    The default filter chip is "All", while the Subscribers chip still narrows
    the table and export to newsletter-subscribed users.
    """

    def test_user_list_filter_and_csv_export(self, django_server, browser):
        """Given: A user logged in as admin@test.com (is_staff=True), and there are
        5 subscribed and 2 unsubscribed contacts.
        1. Navigate to /studio/users/
        Then: With the default "All" chip, all 7 contact rows appear.
        2. Switch to the "Subscribers" chip
        Then: Only the 5 subscribed users appear.
        3. Switch back to the "All" chip
        Then: All 7 contact rows are listed again.
        4. Click the export CSV link/button
        Then: A CSV file downloads with the locked column set including Slack."""
        _clear_subscribers()
        _ensure_tiers()
        admin = _create_staff_user("admin@test.com")
        admin.unsubscribed = True
        admin.email_preferences["newsletter"] = False
        admin.save(update_fields=["unsubscribed", "email_preferences"])
        connection.close()

        # Create 5 active subscribers as User rows.
        for i in range(5):
            _create_newsletter_user(f"active-{i}@test.com", is_active=True)

        # Create 2 unsubscribed contacts.
        for i in range(2):
            _create_newsletter_user(f"inactive-{i}@test.com", is_active=False)

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/users/ -- default chip is All.
        page.goto(
            f"{django_server}/studio/users/",
            wait_until="domcontentloaded",
        )

        all_chip = page.locator('a[data-filter="all"]')
        assert "bg-accent" in (all_chip.get_attribute("class") or "")
        table_body = page.locator("tbody")
        table_text = table_body.inner_text()
        for i in range(5):
            assert f"active-{i}@test.com" in table_text
        for i in range(2):
            assert f"inactive-{i}@test.com" in table_text

        # Step 2: Switch to the "Subscribers" chip.
        page.locator('a[data-filter="subscribers"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "filter=subscribers" in page.url

        table_body = page.locator("tbody")
        table_text = table_body.inner_text()
        for i in range(5):
            assert f"active-{i}@test.com" in table_text
        for i in range(2):
            assert f"inactive-{i}@test.com" not in table_text

        # Step 3: Switch back to the "All" chip.
        page.locator('a[data-filter="all"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "filter=all" in page.url

        table_body = page.locator("tbody")
        table_text = table_body.inner_text()
        for i in range(5):
            assert f"active-{i}@test.com" in table_text
        for i in range(2):
            assert f"inactive-{i}@test.com" in table_text

        # Step 4: Click the export CSV link -- it inherits the current filter.
        export_href = page.locator('a:has-text("Export CSV")').get_attribute("href")
        assert "filter=all" in export_href
        assert "slack=any" in export_href
        with page.expect_download() as download_info:
            page.click('a:has-text("Export CSV")')

        download = download_info.value
        assert download.suggested_filename.startswith("aishippinglabs-contacts-")

        csv_path = download.path()
        with open(csv_path, encoding="utf-8") as f:
            csv_content = f.read()

        assert csv_content.splitlines()[0] == (
            "email,tier,tags,email_verified,unsubscribed,date_joined,last_login,slack"
        )
        assert "Never checked" in csv_content

        # All 7 contact rows are in the CSV (filter=all).
        for i in range(5):
            assert f"active-{i}@test.com" in csv_content
        for i in range(2):
            assert f"inactive-{i}@test.com" in csv_content
# ---------------------------------------------------------------
# Scenario 11: Staff member navigates between Studio sections
#               using the sidebar
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario11SidebarNavigation:
    """Staff member navigates between Studio sections using the sidebar."""

    def test_sidebar_navigation_across_sections(self, django_server, browser):
        """Given: A user logged in as admin@test.com (is_staff=True)
        1. Navigate to /studio/
        2. Click 'Articles' link in the sidebar
        Then: User arrives at /studio/articles/
        3. Click 'Events' link in the sidebar
        Then: User arrives at /studio/events/
        4. Click 'Users' link in the sidebar (renamed from 'Subscribers' in #271)
        Then: User arrives at /studio/users/
        5. Click the 'Studio' logo/link in the sidebar
        Then: User returns to /studio/."""
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        page = context.new_page()
        # Step 1: Navigate to /studio/
        page.goto(
            f"{django_server}/studio/",
            wait_until="domcontentloaded",
        )
        assert "Dashboard" in page.content()

        # Step 2: Click "Articles" in the sidebar
        sidebar = page.locator("aside")
        sidebar.locator('a:has-text("Articles")').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/articles/" in page.url

        # Step 3: Click "Events" in the sidebar
        sidebar = page.locator("aside")
        sidebar.locator('a:has-text("Events")').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/events/" in page.url

        # Step 4: Click "Users" in the sidebar
        sidebar = page.locator("aside")
        sidebar.locator('a:has-text("Users")').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/users/" in page.url

        # Step 5: Click "Studio" logo/link in the sidebar to return
        sidebar = page.locator("aside")
        studio_link = sidebar.locator(
            'a[href="/studio/"]:has-text("Studio")'
        )
        studio_link.click()
        page.wait_for_load_state("domcontentloaded")
        assert page.url.rstrip("/").endswith("/studio")
        assert "Dashboard" in page.content()
# ---------------------------------------------------------------
# Scenario 12 (deleted): Staff member creates an event via
# /studio/events/new and verifies it appears publicly. Removed in
# commit 004fcc5 (closes #166): events now sync from the content
# repo and the create-event view was removed.
# ---------------------------------------------------------------
