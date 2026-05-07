"""Playwright coverage for standardized Studio table action cells."""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _reset_state():
    from django_q.models import Task

    from accounts.models import User
    from content.models import Article, Course
    from events.models import Event
    from integrations.models import ContentSource, SyncLog

    Task.objects.all().delete()
    SyncLog.objects.all().delete()
    ContentSource.objects.all().delete()
    Event.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    User.objects.exclude(email="studio-actions@test.com").delete()
    connection.close()


def _create_content_rows():
    from django.utils.text import slugify

    from content.models import Article, Course, CourseInstructor, Instructor
    from events.models import Event

    article = Article.objects.create(
        title="Action Cell Article",
        slug="action-cell-article",
        date=datetime.date(2026, 4, 30),
        published=True,
        source_repo="AI-Shipping-Labs/content",
    )
    course = Course.objects.create(
        title="Action Cell Course",
        slug="action-cell-course",
        status="draft",
    )
    instructor_name = "Studio"
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify(instructor_name)[:200] or "test-instructor",
        defaults={
            "name": instructor_name,
            "status": "published",
        },
    )
    CourseInstructor.objects.get_or_create(
        course=course,
        instructor=instructor,
        defaults={"position": 0},
    )
    event = Event.objects.create(
        title="Action Cell Event",
        slug="action-cell-event",
        status="upcoming",
        kind="workshop",
        platform="custom",
        start_datetime=timezone.now() + datetime.timedelta(days=7),
    )
    connection.close()
    return article, course, event


def _create_sync_source():
    from integrations.models import ContentSource

    source = ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/action-cell-content",
        is_private=False,
    )
    connection.close()
    return source


def _create_failed_task():
    from django_q.models import Task

    task = Task.objects.create(
        id="action-cell-failed-task",
        name="action-cell-failed-task",
        func="integrations.services.github.sync_content_source",
        started=timezone.now() - datetime.timedelta(seconds=10),
        stopped=timezone.now(),
        success=False,
        result="boom",
    )
    connection.close()
    return task


@pytest.mark.django_db(transaction=True)
class TestStudioActionCells:
    def test_content_row_primary_and_secondary_actions(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        article, _course, _event = _create_content_rows()

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")

        row = page.locator("tr", has_text="Action Cell Article").first
        actions = row.locator(".studio-actions-cell .studio-action")
        assert actions.count() == 2
        assert actions.nth(0).inner_text().strip() == "View"
        assert "bg-accent" in actions.nth(0).get_attribute("class")
        assert actions.nth(1).inner_text().strip() == "View on site"
        assert "bg-secondary" in actions.nth(1).get_attribute("class")
        assert "whitespace-nowrap" in actions.nth(1).get_attribute("class")

        actions.nth(0).click()
        page.wait_for_url(f"**/studio/articles/{article.pk}/edit")
        context.close()

    def test_user_secondary_impersonation_action_is_keyboard_reachable(
        self, django_server, browser,
    ):
        from accounts.models import User

        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        target = User.objects.create_user(
            email="target-action@test.com",
            password="TestPass123!",
            email_verified=True,
        )
        connection.close()

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/users/?q=target-action",
            wait_until="domcontentloaded",
        )

        row = page.locator("tr", has_text="target-action@test.com").first
        assert row.locator("[data-testid='user-view-link']").is_visible()
        button = row.get_by_role("button", name="Login as")
        assert button.is_visible()
        assert f"/studio/impersonate/{target.pk}/" in page.content()

        button.focus()
        assert page.evaluate("document.activeElement.textContent.trim()") == "Login as"
        context.close()

    def test_worker_destructive_and_async_actions_have_clear_confirm(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        _create_failed_task()

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")

        retry = page.locator("[data-action='retry-failed']").first
        delete = page.locator("[data-action='delete-failed']").first
        assert "border-blue-500/40" in retry.get_attribute("class")
        assert "border-red-500/40" in delete.get_attribute("class")

        messages = []
        page.on("dialog", lambda dialog: (messages.append(dialog.message), dialog.dismiss()))
        delete.click()
        assert messages
        assert "Delete failed task action-cell-failed-task?" in messages[0]
        assert "This cannot be undone." in messages[0]
        context.close()

    def test_sync_actions_keep_labels_nowrap_and_flash_queued_state(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        _create_sync_source()

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/sync/", wait_until="domcontentloaded")

        sync_now = page.get_by_role("button", name="Sync now").first
        assert "whitespace-nowrap" in sync_now.get_attribute("class")
        assert "border-blue-500/40" in sync_now.get_attribute("class")
        sync_now.click()
        assert page.get_by_text("Sync queued").first.is_visible()

        page.set_viewport_size({"width": 390, "height": 844})
        assert "whitespace-nowrap" in sync_now.get_attribute("class")
        context.close()
