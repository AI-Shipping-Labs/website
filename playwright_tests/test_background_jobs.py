"""
Playwright E2E tests for Background Job Infrastructure (Issue #93).

Tests cover all 11 BDD scenarios from the issue:
- Staff reviews queued jobs in Django admin
- Staff monitors successful job history in Django admin
- Staff investigates a failed job to diagnose errors
- Staff views recurring job schedules to verify automation is configured
- Staff triggers a manual content sync from the sync dashboard
- Staff reviews sync history for a specific content source
- Staff triggers sync for all content sources at once
- Staff sees helpful empty state when no content sources are configured
- Non-staff user is denied access to the sync dashboard
- Anonymous visitor cannot access job admin pages
- Staff navigates between job monitoring views to build a full picture

Usage:
    uv run pytest playwright_tests/test_background_jobs.py -v
"""

import os
import uuid

import pytest
from django.utils import timezone
from playwright.sync_api import sync_playwright

from playwright_tests.conftest import DJANGO_BASE_URL


# Allow Django ORM calls from within sync_playwright (which runs an
# event loop internally). Without this, Django raises
# SynchronousOnlyOperation when we make ORM calls inside a
# sync_playwright() context.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


VIEWPORT = {"width": 1280, "height": 720}
DEFAULT_PASSWORD = "TestPass123!"
ADMIN_PASSWORD = "adminpass123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _create_staff_user(email="admin@test.com", password=ADMIN_PASSWORD):
    """Create a staff / superuser."""
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


def _login_admin_via_browser(page, base_url, email, password=ADMIN_PASSWORD):
    """Log in an admin user via the Django admin login page."""
    page.goto(f"{base_url}/admin/login/", wait_until="networkidle")
    page.fill("#id_username", email)
    page.fill("#id_password", password)
    page.click('input[type="submit"]')
    page.wait_for_load_state("networkidle")


def _clear_django_q_tables():
    """Clear all Django-Q task tables (OrmQ, Success, Failure, Schedule)."""
    from django_q.models import OrmQ, Success, Failure, Schedule

    OrmQ.objects.all().delete()
    Success.objects.all().delete()
    Failure.objects.all().delete()
    Schedule.objects.all().delete()


def _create_successful_task(func_name, result=None, args=None):
    """Create a Success entry to simulate a completed task."""
    from django_q.models import Success

    task_id = uuid.uuid4().hex
    task = Success.objects.create(
        id=task_id,
        name=f"test-{func_name}",
        func=func_name,
        args=args or (),
        kwargs={},
        result=result or "ok",
        started=timezone.now(),
        stopped=timezone.now(),
        success=True,
    )
    return task


def _create_failed_task(func_name, result=None, args=None):
    """Create a Failure entry to simulate a failed task."""
    from django_q.models import Failure

    task_id = uuid.uuid4().hex
    task = Failure.objects.create(
        id=task_id,
        name=f"test-failed-{func_name}",
        func=func_name,
        args=args or ("arg1", "arg2"),
        kwargs={"key": "value"},
        result=result or "ValueError: something went wrong\nTraceback (most recent call last):\n  File \"jobs/tasks/example.py\", line 10, in broken_task\n    raise ValueError(\"something went wrong\")\nValueError: something went wrong",
        started=timezone.now(),
        stopped=timezone.now(),
        success=False,
    )
    return task


def _clear_content_sources():
    """Delete all content sources and sync logs."""
    from integrations.models import ContentSource

    ContentSource.objects.all().delete()


def _seed_content_sources():
    """Seed the four default content sources."""
    from integrations.models import ContentSource

    sources_data = [
        {
            "repo_name": "AI-Shipping-Labs/blog",
            "content_type": "article",
            "is_private": False,
        },
        {
            "repo_name": "AI-Shipping-Labs/courses",
            "content_type": "course",
            "is_private": True,
        },
        {
            "repo_name": "AI-Shipping-Labs/resources",
            "content_type": "resource",
            "is_private": False,
        },
        {
            "repo_name": "AI-Shipping-Labs/projects",
            "content_type": "project",
            "is_private": False,
        },
    ]
    created_sources = []
    for sd in sources_data:
        source, _ = ContentSource.objects.get_or_create(
            repo_name=sd["repo_name"],
            defaults={
                "content_type": sd["content_type"],
                "is_private": sd["is_private"],
            },
        )
        created_sources.append(source)
    return created_sources


def _create_sync_log(source, status="success", items_created=0,
                     items_updated=0, items_deleted=0, errors=None):
    """Create a SyncLog entry."""
    from integrations.models import SyncLog

    log = SyncLog.objects.create(
        source=source,
        status=status,
        items_created=items_created,
        items_updated=items_updated,
        items_deleted=items_deleted,
        errors=errors or [],
    )
    if status != "running":
        log.finished_at = timezone.now()
        log.save()
    return log


def _run_setup_schedules():
    """Run the setup_schedules management command."""
    from django.core.management import call_command

    call_command("setup_schedules")


# ---------------------------------------------------------------------------
# Scenario 1: Staff reviews queued jobs in Django admin
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario1StaffReviewsQueuedJobs:
    """Staff reviews queued jobs in Django admin.

    Given: A staff user logged in as admin@test.com (superuser)
    1. Navigate to /admin/django_q/ormq/
    Then: The queued tasks page loads and the staff user can see
          the list of pending jobs (or an empty list with no errors)
    2. Navigate to /admin/
    Then: The Django-Q section is visible in the admin index with links
          to Queued tasks, Successful tasks, Failed tasks, and
          Scheduled tasks
    """

    def test_staff_views_queued_tasks_and_admin_index(self, django_server):
        _clear_django_q_tables()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/django_q/ormq/
                page.goto(
                    f"{django_server}/admin/django_q/ormq/",
                    wait_until="networkidle",
                )

                # Then: The queued tasks page loads without errors
                assert page.url.rstrip("/").endswith("/admin/django_q/ormq")
                body = page.content()
                # Page should contain the model name or a message
                # indicating the list (empty or populated)
                assert "Queued tasks" in body or "ormq" in body.lower() or "0 " in body

                # The page loaded successfully (not a 500 error)
                assert "Server Error" not in body

                # Step 2: Navigate to /admin/
                page.goto(
                    f"{django_server}/admin/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The Django-Q section is visible with links
                assert "Django Q" in body or "django_q" in body.lower()

                # Check for links to the four task management pages
                # Django admin shows model names as links
                queued_link = page.locator(
                    'a[href="/admin/django_q/ormq/"]'
                )
                success_link = page.locator(
                    'a[href="/admin/django_q/success/"]'
                )
                failure_link = page.locator(
                    'a[href="/admin/django_q/failure/"]'
                )
                schedule_link = page.locator(
                    'a[href="/admin/django_q/schedule/"]'
                )

                assert queued_link.count() >= 1, (
                    "Queued tasks link not found in admin index"
                )
                assert success_link.count() >= 1, (
                    "Successful tasks link not found in admin index"
                )
                assert failure_link.count() >= 1, (
                    "Failed tasks link not found in admin index"
                )
                assert schedule_link.count() >= 1, (
                    "Scheduled tasks link not found in admin index"
                )

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 2: Staff monitors successful job history in Django admin
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario2StaffMonitorsSuccessfulJobs:
    """Staff monitors successful job history in Django admin.

    Given: A staff user logged in as admin@test.com (superuser) and a
           health check task has been executed successfully
    1. Navigate to /admin/django_q/success/
    Then: The successful tasks list shows at least one entry with the
          function name, timestamp, and result
    2. Click on the health check task entry
    Then: The detail page shows the task name, function path, time taken,
          and the returned result
    """

    def test_staff_views_successful_task_list_and_detail(self, django_server):
        _clear_django_q_tables()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Create a successful health check task entry
        task = _create_successful_task(
            func_name="jobs.tasks.healthcheck.health_check",
            result={"status": "ok", "timestamp": "2026-02-26T10:00:00"},
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/django_q/success/
                page.goto(
                    f"{django_server}/admin/django_q/success/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: At least one entry visible with function name
                assert "health_check" in body or "healthcheck" in body

                # The page loads without error
                assert "Server Error" not in body

                # Step 2: Navigate to the health check task detail page
                # Django admin uses the pk (id field) in the URL
                page.goto(
                    f"{django_server}/admin/django_q/success/{task.id}/change/",
                    wait_until="networkidle",
                )

                # Then: The detail page shows task information
                detail_body = page.content()

                # Function path should be visible
                assert "health_check" in detail_body or "healthcheck" in detail_body

                # The detail page loaded successfully
                assert "Server Error" not in detail_body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 3: Staff investigates a failed job to diagnose errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario3StaffInvestigatesFailedJob:
    """Staff investigates a failed job to diagnose errors.

    Given: A staff user logged in as admin@test.com (superuser) and a
           task has failed with an error
    1. Navigate to /admin/django_q/failure/
    Then: The failed tasks list shows the failing task with its function
          name and timestamp
    2. Click on the failed task entry
    Then: The detail page shows the full error traceback, the function
          name, arguments passed, and the time the failure occurred
    Then: The staff user has enough information to diagnose and fix
          the problem
    """

    def test_staff_views_failed_task_list_and_detail(self, django_server):
        _clear_django_q_tables()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Create a failed task entry
        task = _create_failed_task(
            func_name="jobs.tasks.example.broken_task",
            result=(
                "ValueError: something went wrong\n"
                "Traceback (most recent call last):\n"
                '  File "jobs/tasks/example.py", line 10, in broken_task\n'
                '    raise ValueError("something went wrong")\n'
                "ValueError: something went wrong"
            ),
            args=("user_id_42", "send_email"),
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/django_q/failure/
                page.goto(
                    f"{django_server}/admin/django_q/failure/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: The failed tasks list shows the failing task
                assert "broken_task" in body or "example" in body

                # Page loads without error
                assert "Server Error" not in body

                # Step 2: Navigate to the failed task detail page
                page.goto(
                    f"{django_server}/admin/django_q/failure/{task.id}/change/",
                    wait_until="networkidle",
                )

                # Then: The detail page shows error information
                detail_body = page.content()

                # Function name visible
                assert "broken_task" in detail_body or "example" in detail_body

                # Error traceback content visible
                assert "ValueError" in detail_body or "something went wrong" in detail_body

                # Arguments are present in the detail
                assert "user_id_42" in detail_body or "send_email" in detail_body

                # The detail page loaded successfully
                assert "Server Error" not in detail_body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 4: Staff views recurring job schedules to verify automation
#              is configured
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario4StaffViewsRecurringSchedules:
    """Staff views recurring job schedules to verify automation is configured.

    Given: A staff user logged in as admin@test.com (superuser) and
           `setup_schedules` has been run
    1. Navigate to /admin/django_q/schedule/
    Then: The schedule list shows at least 3 recurring jobs: health-check
          (every 15 min), cleanup-webhook-logs (daily at 3 AM), and
          event-reminders (every 15 min)
    2. Click on the health-check schedule entry
    Then: The detail page shows the cron expression `*/15 * * * *`, the
          function path `jobs.tasks.healthcheck.health_check`, and repeats
          set to forever (-1)
    """

    def test_staff_views_schedules_after_setup(self, django_server):
        _clear_django_q_tables()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Run setup_schedules to register recurring jobs
        _run_setup_schedules()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/django_q/schedule/
                page.goto(
                    f"{django_server}/admin/django_q/schedule/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: At least 3 recurring jobs are shown
                assert "health-check" in body
                assert "cleanup-webhook-logs" in body
                assert "event-reminders" in body

                # Page loads without error
                assert "Server Error" not in body

                # Step 2: Click on the health-check schedule entry
                health_check_link = page.locator(
                    'a:has-text("health-check")'
                ).first
                health_check_link.click()
                page.wait_for_load_state("networkidle")

                # Then: Detail page shows the cron expression, function
                # path, and repeats
                detail_body = page.content()

                # Cron expression
                assert "*/15 * * * *" in detail_body

                # Function path
                assert "jobs.tasks.healthcheck.health_check" in detail_body

                # Repeats set to -1 (forever)
                assert "-1" in detail_body

                # Detail page loaded successfully
                assert "Server Error" not in detail_body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 5: Staff triggers a manual content sync from the sync dashboard
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario5StaffTriggersManualSync:
    """Staff triggers a manual content sync from the sync dashboard.

    Given: A staff user logged in as admin@test.com (superuser) and at
           least one content source is configured
    1. Navigate to /admin/sync/
    Then: The Content Sync dashboard loads showing all configured content
          sources with their last sync time and status
    2. Click the "Sync Now" button next to a content source
    Then: The page redirects back to /admin/sync/ and the sync is triggered
    Then: The staff user sees the updated sync dashboard
    """

    def test_staff_triggers_sync_from_dashboard(self, django_server):
        _clear_content_sources()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        sources = _seed_content_sources()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Dashboard loads showing content sources
                assert "AI-Shipping-Labs/blog" in body
                assert "Content Sync" in body or "Sync" in body

                # Each source shows last sync time or "Never synced"
                blog_card = page.locator(
                    '.bg-card:has-text("AI-Shipping-Labs/blog")'
                ).first
                blog_text = blog_card.inner_text()
                assert "Never synced" in blog_text or "Last synced" in blog_text

                # Step 2: The "Sync Now" button exists next to the
                # blog source
                sync_button = blog_card.locator('button:has-text("Sync Now")')
                assert sync_button.count() >= 1

                # Click the Sync Now button (this submits a form that
                # triggers an async task or inline sync)
                sync_button.click()
                page.wait_for_load_state("networkidle")

                # Then: Page redirects back to /admin/sync/
                assert "/admin/sync" in page.url

                # Dashboard still shows the sources
                body = page.content()
                assert "AI-Shipping-Labs/blog" in body

                # Page loaded without errors
                assert "Server Error" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 6: Staff reviews sync history for a specific content source
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario6StaffReviewsSyncHistory:
    """Staff reviews sync history for a specific content source.

    Given: A staff user logged in as admin@test.com (superuser) and a
           content source has been synced at least once
    1. Navigate to /admin/sync/
    2. Click the "History" link next to a content source
    Then: The sync history page loads showing past syncs with status,
          duration, and item counts
    Then: If any sync had errors, the errors are displayed
    3. Click "Back to Content Sync"
    Then: The user returns to the main sync dashboard
    """

    def test_staff_reviews_sync_history_and_returns(self, django_server):
        _clear_content_sources()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        from integrations.models import ContentSource

        blog_source = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/blog",
            content_type="article",
            is_private=False,
        )

        # Create sync log entries -- one success, one partial with errors
        _create_sync_log(
            blog_source,
            status="success",
            items_created=5,
            items_updated=2,
            items_deleted=0,
        )
        _create_sync_log(
            blog_source,
            status="partial",
            items_created=3,
            items_updated=1,
            items_deleted=1,
            errors=[
                {"file": "broken.md", "error": "Invalid YAML frontmatter"},
            ],
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )

                # Step 2: Click "History" for the blog source
                blog_card = page.locator(
                    '.bg-card:has-text("AI-Shipping-Labs/blog")'
                ).first
                history_link = blog_card.locator('a:has-text("History")')
                history_link.click()
                page.wait_for_load_state("networkidle")

                # Then: History page loads
                assert "/history/" in page.url
                body = page.content()

                # Shows past sync entries with statuses
                assert "success" in body
                assert "partial" in body

                # Shows item counts
                assert "created" in body
                assert "updated" in body

                # Shows error details for the partial sync
                assert "broken.md" in body
                assert "Invalid YAML frontmatter" in body

                # Step 3: Click "Back to Content Sync"
                back_link = page.locator('a:has-text("Back to Content Sync")')
                assert back_link.count() >= 1
                back_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: Returns to the sync dashboard
                assert page.url.rstrip("/").endswith("/admin/sync")

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 7: Staff triggers sync for all content sources at once
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario7StaffTriggersSyncAll:
    """Staff triggers sync for all content sources at once.

    Given: A staff user logged in as admin@test.com (superuser) and
           multiple content sources are configured
    1. Navigate to /admin/sync/
    2. Click the "Sync All" button
    Then: The page redirects back to /admin/sync/ and syncs are triggered
    Then: The staff user can continue monitoring from the dashboard
    """

    def test_staff_triggers_sync_all(self, django_server):
        _clear_content_sources()
        _ensure_tiers()
        _create_staff_user("admin@test.com")
        _seed_content_sources()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Verify multiple sources are shown
                assert "AI-Shipping-Labs/blog" in body
                assert "AI-Shipping-Labs/courses" in body
                assert "AI-Shipping-Labs/resources" in body
                assert "AI-Shipping-Labs/projects" in body

                # Step 2: Click the "Sync All" button
                sync_all_btn = page.locator('button:has-text("Sync All")')
                assert sync_all_btn.count() >= 1

                sync_all_btn.click()
                page.wait_for_load_state("networkidle")

                # Then: Page redirects back to /admin/sync/
                assert "/admin/sync" in page.url

                # Dashboard still shows sources (user can continue
                # monitoring)
                body = page.content()
                assert "AI-Shipping-Labs/blog" in body
                assert "Server Error" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 8: Staff sees helpful empty state when no content sources
#              are configured
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario8EmptyStateNoContentSources:
    """Staff sees helpful empty state when no content sources are
    configured.

    Given: A staff user logged in as admin@test.com (superuser) and
           no content sources exist in the database
    1. Navigate to /admin/sync/
    Then: The page loads without errors and shows a message indicating
          no content sources are configured
    Then: The message includes guidance on how to create content sources
    """

    def test_empty_state_shows_guidance(self, django_server):
        _clear_content_sources()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Page loads without errors
                assert "Server Error" not in body

                # Then: Shows a message about no content sources
                body_text = page.inner_text("body")
                has_empty_message = (
                    "no content source" in body_text.lower()
                    or "no sources" in body_text.lower()
                    or "not configured" in body_text.lower()
                    or "seed" in body_text.lower()
                    or "get started" in body_text.lower()
                )
                assert has_empty_message, (
                    f"Expected empty state message, got: {body_text[:300]}"
                )

                # The "Sync All" button should not be present or be
                # disabled when there are no sources
                # (the page should still load gracefully)
                assert "Server Error" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 9: Non-staff user is denied access to the sync dashboard
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario9NonStaffDeniedAccess:
    """Non-staff user is denied access to the sync dashboard.

    Given: A regular user logged in as free@test.com (Free tier, not staff)
    1. Navigate to /admin/sync/
    Then: The user is redirected to the admin login page
    2. Navigate to /admin/django_q/schedule/
    Then: The user is again denied access and redirected to the admin
          login page
    """

    def test_non_staff_redirected_from_sync_and_django_q(self, django_server):
        _ensure_tiers()
        _create_user("free@test.com", tier_slug="free")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = _auth_context(browser, "free@test.com")
            page = context.new_page()
            try:
                # Step 1: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )

                # Then: Redirected to admin login page
                assert "login" in page.url.lower()

                # No sync controls visible
                body = page.content()
                assert "Sync Now" not in body
                assert "Sync All" not in body

                # Step 2: Navigate to /admin/django_q/schedule/
                page.goto(
                    f"{django_server}/admin/django_q/schedule/",
                    wait_until="networkidle",
                )

                # Then: Again denied access, redirected to login
                assert "login" in page.url.lower()

                body = page.content()
                assert "health-check" not in body
                assert "cleanup-webhook-logs" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 10: Anonymous visitor cannot access job admin pages
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario10AnonymousCannotAccessJobAdmin:
    """Anonymous visitor cannot access job admin pages.

    Given: An anonymous visitor (not logged in)
    1. Navigate to /admin/django_q/success/
    Then: The visitor is redirected to the admin login page
    2. Navigate to /admin/sync/
    Then: The visitor is redirected to the admin login page
    Then: No job or sync information is exposed to unauthenticated users
    """

    def test_anonymous_redirected_from_job_admin_pages(self, django_server):
        _ensure_tiers()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # Anonymous context (no session cookie)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                # Step 1: Navigate to /admin/django_q/success/
                page.goto(
                    f"{django_server}/admin/django_q/success/",
                    wait_until="networkidle",
                )

                # Then: Redirected to admin login page
                assert "login" in page.url.lower()

                body = page.content()
                # No job information exposed
                assert "health_check" not in body
                assert "Successful tasks" not in body

                # Step 2: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )

                # Then: Redirected to admin login page
                assert "login" in page.url.lower()

                body = page.content()
                assert "Sync Now" not in body
                assert "Content Sync" not in body

            finally:
                browser.close()


# ---------------------------------------------------------------------------
# Scenario 11: Staff navigates between job monitoring views to build
#              a full picture
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario11StaffNavigatesJobMonitoringViews:
    """Staff navigates between job monitoring views to build a full picture.

    Given: A staff user logged in as admin@test.com (superuser) and
           `setup_schedules` has been run
    1. Navigate to /admin/
    2. Click on "Scheduled tasks" under the Django Q section
    Then: The schedule list page loads showing recurring jobs
    3. Navigate to /admin/django_q/success/
    Then: The successful tasks list loads
    4. Navigate to /admin/django_q/failure/
    Then: The failed tasks list loads
    5. Navigate to /admin/sync/
    Then: The content sync dashboard loads
    Then: The staff user can move between all job monitoring views
    """

    def test_staff_navigates_all_job_views(self, django_server):
        _clear_django_q_tables()
        _clear_content_sources()
        _ensure_tiers()
        _create_staff_user("admin@test.com")

        # Set up schedules and seed content sources
        _run_setup_schedules()
        _seed_content_sources()

        # Create a successful and a failed task for the views
        _create_successful_task(
            func_name="jobs.tasks.healthcheck.health_check",
            result={"status": "ok"},
        )
        _create_failed_task(
            func_name="jobs.tasks.example.broken_task",
        )

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()
            try:
                _login_admin_via_browser(page, django_server, "admin@test.com")

                # Step 1: Navigate to /admin/
                page.goto(
                    f"{django_server}/admin/",
                    wait_until="networkidle",
                )
                body = page.content()
                assert "Django Q" in body or "django_q" in body.lower()

                # Step 2: Click on "Scheduled tasks" link
                schedule_link = page.locator(
                    'a[href="/admin/django_q/schedule/"]'
                )
                assert schedule_link.count() >= 1
                schedule_link.first.click()
                page.wait_for_load_state("networkidle")

                # Then: Schedule list page loads with recurring jobs
                body = page.content()
                assert "health-check" in body
                assert "cleanup-webhook-logs" in body
                assert "event-reminders" in body

                # Step 3: Navigate to /admin/django_q/success/
                page.goto(
                    f"{django_server}/admin/django_q/success/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Successful tasks list loads
                assert "Server Error" not in body
                assert "health_check" in body or "healthcheck" in body

                # Step 4: Navigate to /admin/django_q/failure/
                page.goto(
                    f"{django_server}/admin/django_q/failure/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Failed tasks list loads
                assert "Server Error" not in body
                assert "broken_task" in body or "example" in body

                # Step 5: Navigate to /admin/sync/
                page.goto(
                    f"{django_server}/admin/sync/",
                    wait_until="networkidle",
                )
                body = page.content()

                # Then: Content sync dashboard loads
                assert "Server Error" not in body
                assert "AI-Shipping-Labs/blog" in body
                assert "AI-Shipping-Labs/courses" in body

            finally:
                browser.close()
