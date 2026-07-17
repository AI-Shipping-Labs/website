"""Playwright coverage for standardized Studio table action cells."""

import datetime
import os
import uuid
from pathlib import Path

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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = [pytest.mark.local_only, pytest.mark.core]

SCREENSHOT_DIR = Path(".tmp/screenshots/issue-1277")


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOT_DIR / f"{name}.png"), full_page=True)


def _reset_state():
    from django_q.models import OrmQ, Task

    from accounts.models import User
    from content.models import Article, Course
    from events.models import Event
    from integrations.models import ContentSource, SyncLog

    OrmQ.objects.all().delete()
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


def _create_pending_task():
    from django_q.models import OrmQ
    from django_q.signing import SignedPackage

    task_id = uuid.uuid4().hex
    payload = {
        "id": task_id,
        "name": "action-cell-pending-task",
        "func": "integrations.services.github.sync_content_source",
        "args": (),
        "kwargs": {},
    }
    queued = OrmQ.objects.create(
        key="default",
        payload=SignedPackage.dumps(payload),
    )
    connection.close()
    return queued.pk, task_id


def _create_sync_history_batch():
    from integrations.models import ContentSource, SyncLog

    source = ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/action-cell-history",
        last_sync_status="success",
        last_synced_at=timezone.now(),
    )
    sync_log = SyncLog.objects.create(
        source=source,
        batch_id=uuid.uuid4(),
        status="success",
        finished_at=timezone.now(),
        items_created=1,
        items_updated=2,
        items_unchanged=3,
        commit_sha="abc1234def5678901234567890123456789abcde",
        items_detail=[
            {
                "title": "Action Cell Course",
                "slug": "action-cell-course",
                "action": "updated",
                "content_type": "course",
            }
        ],
    )
    connection.close()
    return source.pk, sync_log.pk


@pytest.mark.django_db(transaction=True)
class TestStudioActionCells:
    def test_content_row_navigation_and_public_link_are_secondary(
        self,
        django_server,
        browser,
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
        assert "border-border" in actions.nth(0).get_attribute("class")
        assert "bg-secondary" in actions.nth(0).get_attribute("class")
        assert "bg-accent" not in actions.nth(0).get_attribute("class")
        assert actions.nth(1).inner_text().strip() == "View on site"
        assert "bg-secondary" in actions.nth(1).get_attribute("class")
        assert "whitespace-nowrap" in actions.nth(1).get_attribute("class")
        assert actions.nth(1).get_attribute("target") == "_blank"
        # Articles intentionally had no rel attribute before this visual-only
        # change; preserve that exact target/rel contract.
        assert actions.nth(1).get_attribute("rel") is None

        actions.nth(0).click()
        page.wait_for_url(f"**/studio/articles/{article.pk}/edit")
        context.close()

    def test_user_row_only_exposes_view_action(
        self,
        django_server,
        browser,
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
        view = row.locator("[data-testid='user-view-link']")
        assert view.is_visible()
        assert "border-border" in view.get_attribute("class")
        assert "bg-secondary" in view.get_attribute("class")
        assert "bg-accent" not in view.get_attribute("class")
        assert view.get_attribute("href") == f"/studio/users/{target.pk}/"
        assert row.get_by_role("button", name="Login as").count() == 0
        assert f"/studio/impersonate/{target.pk}/" not in page.content()

        view.focus()
        assert page.evaluate("document.activeElement.textContent.trim()") == "View"
        assert "focus-visible:ring-accent" in view.get_attribute("class")
        _capture(page, "users-secondary-view")
        view.click()
        page.wait_for_url(f"**/studio/users/{target.pk}/")
        context.close()

    def test_email_template_edit_navigation_keeps_mutations_distinct(
        self,
        django_server,
        browser,
    ):
        from email_app.models import EmailTemplateOverride

        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        EmailTemplateOverride.objects.create(
            template_name="welcome",
            subject="Action cell welcome",
            body_markdown="Hello.",
        )
        connection.close()

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/email-templates/",
            wait_until="domcontentloaded",
        )

        row = page.locator("tr", has_text="welcome").first
        edit = row.get_by_role("link", name="Edit", exact=True)
        send_form = row.locator("form", has_text="Send test to me")
        reset = row.get_by_role("button", name="Reset to default", exact=True)
        assert "border-border" in edit.get_attribute("class")
        assert "bg-secondary" in edit.get_attribute("class")
        assert "bg-accent" not in edit.get_attribute("class")
        assert send_form.get_attribute("method").lower() == "post"
        assert "bg-secondary" in send_form.locator("button").get_attribute("class")
        assert "border-red-500/40" in reset.get_attribute("class")
        assert "Reset welcome to the filesystem default?" in (
            reset.locator("xpath=ancestor::form").get_attribute("onsubmit")
        )

        _capture(page, "email-templates-secondary-edit")
        edit_href = edit.get_attribute("href")
        edit.click()
        page.wait_for_url(f"**{edit_href}")
        assert page.url.endswith(edit_href)
        context.close()

    def test_worker_destructive_and_async_actions_have_clear_confirm(
        self,
        django_server,
        browser,
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

    def test_pending_worker_inspect_is_secondary_on_page_and_fragment(
        self,
        django_server,
        browser,
    ):
        from django_q.models import OrmQ, Task

        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        queued_pk, task_id = _create_pending_task()
        inspect_href = f"/studio/worker/queue/{queued_pk}/inspect/?task_id={task_id}"

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")

        row = page.locator(f'[data-queued-task-id="{queued_pk}"]')
        inspect = row.locator('[data-action="inspect"]')
        delete = row.locator('[data-action="delete-queued"]')
        assert inspect.get_attribute("href") == inspect_href
        assert "border-border" in inspect.get_attribute("class")
        assert "bg-secondary" in inspect.get_attribute("class")
        assert "bg-accent" not in inspect.get_attribute("class")
        assert "border-red-500/40" in delete.get_attribute("class")

        fragment = context.new_page()
        fragment.goto(
            f"{django_server}/studio/worker/?fragment=pending",
            wait_until="domcontentloaded",
        )
        fragment_row = fragment.locator(f'[data-queued-task-id="{queued_pk}"]')
        fragment_inspect = fragment_row.locator('[data-action="inspect"]')
        fragment_delete = fragment_row.locator('[data-action="delete-queued"]')
        assert fragment_inspect.get_attribute("href") == inspect_href
        assert "border-border" in fragment_inspect.get_attribute("class")
        assert "bg-secondary" in fragment_inspect.get_attribute("class")
        assert "bg-accent" not in fragment_inspect.get_attribute("class")
        assert "border-red-500/40" in fragment_delete.get_attribute("class")
        fragment.close()

        inspect.click()
        page.wait_for_url(f"**{inspect_href}")
        assert page.url.endswith(inspect_href)
        assert page.get_by_text("action-cell-pending-task", exact=True).is_visible()
        assert OrmQ.objects.filter(pk=queued_pk).exists()
        assert Task.objects.count() == 0
        connection.close()
        context.close()

    def test_sync_history_details_is_secondary_disclosure_without_mutation(
        self,
        django_server,
        browser,
    ):
        from django_q.models import OrmQ, Task

        from integrations.models import ContentSource, SyncLog

        _ensure_tiers()
        _reset_state()
        _create_staff_user("studio-actions@test.com")
        source_pk, sync_log_pk = _create_sync_history_batch()

        context = _auth_context(browser, "studio-actions@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/studio/sync/history/",
            wait_until="domcontentloaded",
        )

        header = page.locator(".batch-header").first
        details = header.locator('[data-action="sync-history-details"]')
        detail_panel = page.locator(".batch-detail").first
        assert "border-border" in details.get_attribute("class")
        assert "bg-secondary" in details.get_attribute("class")
        assert "bg-accent" not in details.get_attribute("class")
        assert details.get_attribute("aria-expanded") == "false"
        assert not detail_panel.is_visible()

        mutation_requests = []
        page.on(
            "request",
            lambda request: (
                mutation_requests.append((request.method, request.url))
                if request.method != "GET" and "/studio/sync" in request.url
                else None
            ),
        )
        details.click()

        assert details.get_attribute("aria-expanded") == "true"
        detail_panel.wait_for(state="visible")
        assert detail_panel.locator(".hist-type-row", has_text="Course").is_visible()
        assert mutation_requests == []
        assert SyncLog.objects.filter(pk=sync_log_pk, status="success").exists()
        assert ContentSource.objects.get(pk=source_pk).last_sync_status == "success"
        assert OrmQ.objects.count() == 0
        assert Task.objects.count() == 0
        connection.close()
        context.close()

    def test_sync_actions_keep_labels_nowrap_and_flash_queued_state(
        self,
        django_server,
        browser,
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
