"""Playwright coverage for Studio courses list scanability (#489)."""

import os

import pytest

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

MOBILE_VIEWPORT = {"width": 390, "height": 844}


def _reset_courses():
    from content.models import Course, Module, Unit

    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _seed_courses():
    from django.utils.text import slugify

    from content.access import LEVEL_BASIC, LEVEL_PREMIUM
    from content.models import Course, CourseInstructor, Instructor

    synced = Course.objects.create(
        title="Source Managed Premium Course With A Long Operator Facing Name",
        slug="source-managed-premium-course-with-a-long-operator-facing-name",
        status="published",
        required_level=LEVEL_PREMIUM,
        source_repo="AI-Shipping-Labs/content",
        source_path="courses/source-managed-premium/course.yaml",
    )
    local = Course.objects.create(
        title="Local Basic Course",
        slug="local-basic-course",
        status="draft",
        required_level=LEVEL_BASIC,
    )
    for course, name in (
        (synced, "Alexey Grigorev"),
        (local, "Studio Team"),
    ):
        instructor, _ = Instructor.objects.get_or_create(
            instructor_id=slugify(name)[:200] or "test-instructor",
            defaults={
                "name": name,
                "status": "published",
            },
        )
        CourseInstructor.objects.get_or_create(
            course=course,
            instructor=instructor,
            defaults={"position": 0},
        )
    connection.close()
    return synced, local


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2


@pytest.mark.django_db(transaction=True)
def test_studio_courses_list_desktop_metadata_and_actions(django_server, browser):
    _ensure_tiers()
    _reset_courses()
    _create_staff_user("courses-list-staff@test.com")
    synced, local = _seed_courses()

    context = _auth_context(browser, "courses-list-staff@test.com")
    page = context.new_page()
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    assert page.get_by_role("columnheader", name="Source").is_visible()
    status_filter = page.locator('[data-testid="studio-status-filter"]')
    assert status_filter.is_visible()
    assert status_filter.evaluate("el => el.tagName") == "SELECT"

    synced_row = page.locator('[data-testid="studio-course-row"]').filter(
        has_text=synced.title
    ).first
    assert synced_row.is_visible()
    assert "Premium (Level 30)" in synced_row.inner_text()
    assert "Level 30" in synced_row.inner_text()
    assert "AI-Shipping-Labs/content" in synced_row.inner_text()
    assert "courses/source-managed-premium/course.yaml" in synced_row.inner_text()
    assert synced_row.get_by_role("link", name="View", exact=True).is_visible()
    assert synced_row.get_by_role("link", name="View on site", exact=True).is_visible()
    source_link = synced_row.get_by_role(
        "link", name="courses/source-managed-premium/course.yaml"
    )
    assert source_link.get_attribute("href") == (
        "https://github.com/AI-Shipping-Labs/content/blob/main/"
        "courses/source-managed-premium/course.yaml"
    )

    local_row = page.locator('[data-testid="studio-course-row"]').filter(
        has_text=local.title
    ).first
    assert "Basic (Level 10)" in local_row.inner_text()
    assert "No GitHub source metadata" in local_row.inner_text()
    assert local_row.get_by_role("link", name="Edit", exact=True).is_visible()
    assert local_row.get_by_role("link", name="View on site", exact=True).is_visible()

    with page.expect_navigation(url="**/studio/courses/?q=&status=draft"):
        status_filter.select_option("draft")
    assert page.locator('[data-testid="studio-course-row"]').filter(
        has_text=local.title
    ).first.is_visible()
    assert page.locator('[data-testid="studio-course-row"]').filter(
        has_text=synced.title
    ).count() == 0
    context.close()


@pytest.mark.django_db(transaction=True)
def test_studio_courses_list_mobile_cards_wrap_without_overflow(django_server, browser):
    _ensure_tiers()
    _reset_courses()
    _create_staff_user("courses-list-mobile-staff@test.com")
    synced, local = _seed_courses()

    context = _auth_context(browser, "courses-list-mobile-staff@test.com")
    page = context.new_page()
    page.set_viewport_size(MOBILE_VIEWPORT)
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    _assert_no_horizontal_overflow(page)
    card = page.locator('[data-testid="studio-course-row"]').filter(
        has_text=synced.title
    ).first
    assert card.is_visible()
    assert "Source" in card.inner_text()
    assert "Premium (Level 30)" in card.inner_text()
    assert card.get_by_role("link", name="View", exact=True).is_visible()
    assert card.get_by_role("link", name="View on site", exact=True).is_visible()

    title_box = card.locator('[data-testid="studio-course-title"]').bounding_box()
    assert title_box is not None
    assert title_box["x"] >= 0
    assert title_box["x"] + title_box["width"] <= MOBILE_VIEWPORT["width"]

    local_card = page.locator('[data-testid="studio-course-row"]').filter(
        has_text=local.title
    ).first
    assert local_card.get_by_role("link", name="Edit", exact=True).is_visible()
    assert local_card.get_by_role("link", name="View on site", exact=True).is_visible()

    _assert_no_horizontal_overflow(page)
    context.close()
