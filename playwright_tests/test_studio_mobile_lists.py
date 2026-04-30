"""Mobile E2E coverage for Studio list surfaces (issue #406)."""

import datetime
import os
import uuid
from pathlib import Path

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
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
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

MOBILE_VIEWPORT = {"width": 390, "height": 900}
SCREENSHOT_DIR = Path("/tmp/aisl-issue-406-screenshots")


def _reset_studio_mobile_data():
    from django_q.models import Task

    from accounts.models import ImportBatch
    from content.models import Article, Course, Workshop, WorkshopPage
    from events.models import Event
    from integrations.models import ContentSource, SyncLog

    Task.objects.all().delete()
    SyncLog.objects.all().delete()
    ContentSource.objects.all().delete()
    ImportBatch.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    connection.close()


def _seed_studio_mobile_data(staff_email):
    from django_q.models import Task

    from accounts.models import ImportBatch
    from content.models import Article, Course, Workshop
    from events.models import Event
    from integrations.models import ContentSource, SyncLog

    now = timezone.now()
    Article.objects.create(
        title="Mobile Article Audit",
        slug="mobile-article-audit",
        date=now.date(),
        author="Studio",
        published=True,
        source_repo="AI-Shipping-Labs/content",
    )
    Course.objects.create(
        title="Mobile Course Triage",
        slug="mobile-course-triage",
        status="published",
        instructor_name="Alexey",
        required_level=10,
        source_repo="AI-Shipping-Labs/content",
    )
    Workshop.objects.create(
        slug="mobile-workshop",
        title="Mobile Workshop Detail",
        date=datetime.date(2026, 4, 21),
        description="Hands-on mobile workshop.",
        status="published",
        landing_required_level=0,
        pages_required_level=10,
        recording_required_level=20,
        source_repo="AI-Shipping-Labs/workshops-content",
        source_commit="abc1234def5678901234567890123456789abcde",
    )
    Event.objects.create(
        title="Mobile Event Capacity",
        slug="mobile-event-capacity",
        start_datetime=now,
        status="upcoming",
        kind="workshop",
        platform="custom",
        max_participants=25,
    )
    actor = _create_user("mobile-member@test.com", tier_slug="main")
    ImportBatch.objects.create(
        source="course_db",
        actor=actor,
        dry_run=True,
        status=ImportBatch.STATUS_COMPLETED,
        users_created=3,
        users_skipped=1,
        errors=[{"message": "Missing email"}],
        emails_queued=0,
    )
    source = ContentSource.objects.create(
        repo_name="AI-Shipping-Labs/content",
        last_sync_status="success",
        last_synced_at=now,
        last_synced_commit="abc1234def5678901234567890123456789abcde",
    )
    SyncLog.objects.create(
        source=source,
        batch_id=uuid.uuid4(),
        status="success",
        finished_at=now,
        items_created=1,
        items_updated=2,
        items_unchanged=4,
        commit_sha="abc1234def5678901234567890123456789abcde",
        items_detail=[
            {
                "title": "Mobile Course Triage",
                "slug": "mobile-course-triage",
                "action": "updated",
                "content_type": "course",
            }
        ],
    )
    Task.objects.create(
        id=uuid.uuid4().hex,
        name="mobile.sync.task",
        func="integrations.services.github.sync_content_source",
        started=now - datetime.timedelta(seconds=8),
        stopped=now - datetime.timedelta(seconds=3),
        success=True,
        result="ok",
    )
    _create_user("mobile-list-user@test.com", tier_slug="basic")
    connection.close()


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2


def _assert_visible_text_and_action(page, row_selector, identity, status, action_text):
    row = page.locator(row_selector).filter(has_text=identity).first
    assert row.is_visible()
    assert status in row.inner_text()
    action = row.get_by_text(action_text, exact=True).first
    assert action.is_visible()
    box = action.bounding_box()
    assert box is not None
    assert box["x"] + box["width"] <= MOBILE_VIEWPORT["width"]


def _capture_mobile_screenshot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


@pytest.mark.django_db(transaction=True)
def test_studio_core_lists_are_usable_at_phone_width(django_server, browser):
    _ensure_tiers()
    staff_email = "mobile-studio-admin@test.com"
    _create_staff_user(staff_email)
    _reset_studio_mobile_data()
    _seed_studio_mobile_data(staff_email)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(MOBILE_VIEWPORT)

    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")
    _assert_visible_text_and_action(
        page, "tbody tr", "Mobile Course Triage", "Published", "View"
    )
    assert "Level 10" in page.locator("tbody tr").filter(
        has_text="Mobile Course Triage"
    ).first.inner_text()
    _assert_no_horizontal_overflow(page)
    _capture_mobile_screenshot(page, "courses")

    page.fill('input[name="q"]', "Mobile Course")
    page.get_by_role("button", name="Search").click()
    page.wait_for_load_state("domcontentloaded")
    _assert_visible_text_and_action(
        page, "tbody tr", "Mobile Course Triage", "Published", "View"
    )
    _assert_no_horizontal_overflow(page)

    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
    _assert_visible_text_and_action(
        page, "tbody tr", "Mobile Event Capacity", "Upcoming", "Edit"
    )
    assert "25" in page.locator("tbody tr").filter(
        has_text="Mobile Event Capacity"
    ).first.inner_text()
    _assert_no_horizontal_overflow(page)
    _capture_mobile_screenshot(page, "events")

    page.goto(f"{django_server}/studio/workshops/", wait_until="domcontentloaded")
    workshop_row = page.locator('[data-testid="workshop-row"]').filter(
        has_text="Mobile Workshop Detail"
    ).first
    assert workshop_row.is_visible()
    assert "Published" in workshop_row.inner_text()
    workshop_row.get_by_text("View", exact=True).first.click()
    page.wait_for_load_state("domcontentloaded")
    assert "/studio/workshops/" in page.url
    _capture_mobile_screenshot(page, "workshops")

    page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")
    _assert_visible_text_and_action(
        page, "tbody tr", "Mobile Article Audit", "Published", "View"
    )
    _assert_no_horizontal_overflow(page)
    _capture_mobile_screenshot(page, "articles")

    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")
    _assert_visible_text_and_action(
        page, "tbody tr", "mobile-list-user@test.com", "Active", "View"
    )
    _assert_no_horizontal_overflow(page)
    _capture_mobile_screenshot(page, "users")

    page.goto(f"{django_server}/studio/imports/", wait_until="domcontentloaded")
    import_row = page.locator("tbody tr").filter(has_text="Course database").first
    assert import_row.is_visible()
    assert "Completed" in import_row.inner_text()
    assert "dry-run" in import_row.inner_text()
    assert "Missing email" not in import_row.inner_text()
    import_row.get_by_text("Course database", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    assert "/studio/imports/" in page.url
    _capture_mobile_screenshot(page, "imports")

    page.goto(f"{django_server}/studio/sync/history/", wait_until="domcontentloaded")
    header = page.locator(".batch-header").first
    assert header.is_visible()
    assert "success" in header.inner_text()
    assert "AI-Shipping-Labs/content" in header.inner_text()
    header.click()
    assert page.locator(".hist-type-row").filter(has_text="Course").first.is_visible()
    _assert_no_horizontal_overflow(page)
    _capture_mobile_screenshot(page, "sync-history")

    page.goto(f"{django_server}/studio/worker/", wait_until="domcontentloaded")
    task_row = page.locator(".recent-task-row").filter(has_text="mobile.sync.task").first
    assert task_row.is_visible()
    assert "Success" in task_row.inner_text()
    task_row.get_by_text("mobile.sync.task", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    assert "/studio/worker/task/" in page.url
    _capture_mobile_screenshot(page, "worker")

    context.close()
