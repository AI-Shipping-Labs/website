"""Playwright checks for long Studio edit form ergonomics."""

import os
from datetime import datetime

import pytest
from django.db import connection
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


def _reset_content():
    from content.models import Article, Course, Module, Unit
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    connection.close()


def _create_course(**kwargs):
    from content.models import Course

    defaults = {
        "title": "Sticky Course",
        "slug": "sticky-course",
        "description": "Long description",
        "status": "draft",
        "required_level": 0,
    }
    defaults.update(kwargs)
    course = Course.objects.create(**defaults)
    connection.close()
    return course


def _create_article(**kwargs):
    from content.models import Article

    defaults = {
        "title": "Sticky Article",
        "slug": "sticky-article",
        "date": timezone.now().date(),
        "published": True,
        "content_markdown": "\n\n".join([f"Section {i}" for i in range(25)]),
    }
    defaults.update(kwargs)
    article = Article.objects.create(**defaults)
    connection.close()
    return article


def _create_event(**kwargs):
    from events.models import Event

    defaults = {
        "title": "Sticky Event",
        "slug": "sticky-event",
        "start_datetime": datetime(2026, 6, 1, 10, 0),
        "end_datetime": datetime(2026, 6, 1, 11, 0),
        "status": "upcoming",
    }
    defaults.update(kwargs)
    event = Event.objects.create(**defaults)
    connection.close()
    return event


def _staff_page(django_server, browser):
    _ensure_tiers()
    _create_staff_user("studio-long-forms@test.com")
    context = _auth_context(browser, "studio-long-forms@test.com")
    page = context.new_page()
    return context, page


def _assert_sticky_not_covering(page, field_selector):
    field = page.locator(field_selector)
    field.scroll_into_view_if_needed()
    page.wait_for_timeout(100)

    bar_box = page.locator('[data-testid="sticky-action-bar"]').bounding_box()
    field_box = field.bounding_box()
    assert bar_box is not None
    assert field_box is not None

    overlaps_vertically = (
        field_box["y"] < bar_box["y"] + bar_box["height"]
        and field_box["y"] + field_box["height"] > bar_box["y"]
    )
    assert not overlaps_vertically


@pytest.mark.django_db(transaction=True)
def test_staff_saves_course_from_sticky_action(django_server, browser):
    _reset_content()
    course = _create_course()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/courses/{course.pk}/edit", wait_until="domcontentloaded")
    page.locator('textarea[name="peer_review_criteria"]').fill("Review the README and demo.")
    page.mouse.wheel(0, 1600)
    assert page.locator('[data-testid="sticky-save-action"]').is_visible()
    page.locator('[data-testid="sticky-save-action"]').click()
    page.wait_for_load_state("domcontentloaded")

    course.refresh_from_db()
    assert course.peer_review_criteria == "Review the README and demo."
    context.close()


@pytest.mark.django_db(transaction=True)
def test_course_side_panel_and_synced_course_readonly(django_server, browser):
    _reset_content()
    course = _create_course(
        status="published",
        individual_price_eur="49.00",
    )
    synced = _create_course(
        title="Synced Course",
        slug="synced-course",
        source_repo="AI-Shipping-Labs/content",
        source_path="courses/synced-course/course.yaml",
    )
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/courses/{course.pk}/edit", wait_until="domcontentloaded")
    panel = page.locator('[data-testid="studio-meta-actions-panel"]')
    assert panel.locator("text=Manage Access").is_visible()
    assert panel.locator("text=Manage Peer Reviews").is_visible()
    assert panel.locator("text=Manage Enrollments").is_visible()
    assert panel.locator('[data-testid="notification-actions"]').is_visible()
    assert panel.locator('[data-testid="stripe-product-panel"]').count() == 0

    page.goto(f"{django_server}/studio/courses/{synced.pk}/edit", wait_until="domcontentloaded")
    assert page.locator('[data-testid="sticky-save-action"]').count() == 0
    assert page.locator('[data-testid="sticky-github-source-link"]').is_visible()
    assert page.locator('input[name="title"]').is_disabled()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_staff_saves_event_from_sticky_action_and_sees_integrations(django_server, browser):
    _reset_content()
    event = _create_event()
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    page.locator('input[name="location"]').fill("Studio Room")
    page.mouse.wheel(0, 1200)
    page.locator('[data-testid="sticky-save-action"]').click()
    page.wait_for_load_state("domcontentloaded")

    event.refresh_from_db()
    assert event.location == "Studio Room"
    assert page.locator('[data-testid="zoom-meeting-panel"]').is_visible()
    assert page.locator("text=Create Zoom Meeting").is_visible()
    assert page.locator('[data-testid="notification-actions"]').is_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_staff_saves_article_from_sticky_action(django_server, browser):
    _reset_content()
    article = _create_article(published=False)
    context, page = _staff_page(django_server, browser)

    page.goto(f"{django_server}/studio/articles/{article.pk}/edit", wait_until="domcontentloaded")
    page.locator('textarea[name="content_markdown"]').fill("Updated body\n\n" * 20)
    page.mouse.wheel(0, 1600)
    page.locator('[data-testid="sticky-save-action"]').click()
    page.wait_for_load_state("domcontentloaded")

    article.refresh_from_db()
    assert article.content_markdown.startswith("Updated body")
    assert page.locator('[data-testid="studio-meta-actions-panel"]').is_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_mobile_sticky_actions_do_not_cover_final_fields(django_server, browser, tmp_path):
    _reset_content()
    course = _create_course()
    event = _create_event()
    article = _create_article()
    context, page = _staff_page(django_server, browser)
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(f"{django_server}/studio/courses/{course.pk}/edit", wait_until="domcontentloaded")
    _assert_sticky_not_covering(page, 'textarea[name="peer_review_criteria"]')
    page.screenshot(path=str(tmp_path / "issue-412-course-mobile.png"), full_page=True)

    page.goto(f"{django_server}/studio/events/{event.pk}/edit", wait_until="domcontentloaded")
    _assert_sticky_not_covering(page, 'input[name="tags"]')
    page.screenshot(path=str(tmp_path / "issue-412-event-mobile.png"), full_page=True)

    page.goto(f"{django_server}/studio/articles/{article.pk}/edit", wait_until="domcontentloaded")
    _assert_sticky_not_covering(page, 'input[name="tags"]')
    page.screenshot(path=str(tmp_path / "issue-412-article-mobile.png"), full_page=True)
    context.close()
