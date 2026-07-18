"""Playwright checks for source-managed course edit page action hierarchy.

Issue #490: source actions (Edit on GitHub, View on site, Re-sync source)
remain reachable after the standardized Studio header moved secondary actions
into the shared overflow menu. The YAML guide remains visible.
"""

import os

import pytest
from django.db import connection

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

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _reset_courses():
    from content.models import Course, Module, Unit

    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_synced_course():
    from content.models import Course

    course = Course.objects.create(
        title="Synced Source Course",
        slug="synced-source-course",
        status="published",
        source_repo="AI-Shipping-Labs/content",
        source_path="courses/synced-source-course/course.yaml",
    )
    connection.close()
    return course


def _staff_page(browser):
    _ensure_tiers()
    _create_staff_user("course-source-actions@test.com")
    context = _auth_context(browser, "course-source-actions@test.com")
    page = context.new_page()
    return context, page


@pytest.mark.django_db(transaction=True)
def test_source_managed_action_row_desktop_single_line(
    django_server, browser, tmp_path,
):
    """Desktop 1280x900: primary and overflow actions remain reachable."""
    _reset_courses()
    course = _create_synced_course()
    context, page = _staff_page(browser)
    page.set_viewport_size({"width": 1280, "height": 900})

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="networkidle",
    )

    action_row = page.locator('[data-testid="studio-header-actions"]').first
    assert action_row.is_visible()

    view = page.locator('[data-testid="view-on-site"]').first
    docs = page.locator('[data-testid="sticky-docs-link"]').first
    overflow = page.locator('[data-testid="studio-header-overflow"]').first
    overflow_toggle = overflow.locator("summary")

    assert view.is_visible()
    assert overflow_toggle.is_visible()
    assert docs.is_visible()

    # The primary action and More actions control share the header row.
    v = view.bounding_box()
    o = overflow_toggle.bounding_box()
    assert v is not None and o is not None
    assert abs(v["y"] - o["y"]) < max(v["height"], o["height"])

    overflow_toggle.click()
    github = overflow.get_by_test_id("sticky-github-source-link")
    resync = overflow.get_by_test_id("sticky-resync-source-button")
    assert github.is_visible()
    assert resync.is_visible()

    page.screenshot(
        path=str(tmp_path / "issue-490-action-row-desktop.png"),
        full_page=True,
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_source_managed_action_row_mobile_no_horizontal_overflow(
    django_server, browser, tmp_path,
):
    """Mobile 390x844: page does not horizontally overflow and source
    actions remain reachable (visible after scroll)."""
    _reset_courses()
    course = _create_synced_course()
    context, page = _staff_page(browser)
    page.set_viewport_size({"width": 390, "height": 844})

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="domcontentloaded",
    )

    # No horizontal page overflow.
    body_width = page.evaluate("() => document.body.scrollWidth")
    viewport_width = page.evaluate("() => window.innerWidth")
    assert body_width <= viewport_width + 1, (
        f"Horizontal overflow: body={body_width} viewport={viewport_width}"
    )

    # Source action row exists and the three buttons are present (sticky bar
    # may hug the bottom of the viewport on mobile, which is the design).
    assert page.locator('[data-testid="studio-header-actions"]').count() == 1
    assert (
        page.locator('[data-testid="sticky-github-source-link"]').count() == 1
    )
    assert (
        page.locator('[data-testid="view-on-site"]').count() == 1
    )
    assert (
        page.locator('[data-testid="sticky-resync-source-button"]').count() == 1
    )

    page.screenshot(
        path=str(tmp_path / "issue-490-action-row-mobile.png"),
        full_page=True,
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_source_managed_stripe_metadata_renders_not_configured(
    django_server, browser, tmp_path,
):
    """Empty Stripe IDs show 'Not configured', not empty editable fields."""
    _reset_courses()
    course = _create_synced_course()
    context, page = _staff_page(browser)
    page.set_viewport_size({"width": 1280, "height": 900})

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="domcontentloaded",
    )

    section = page.locator('[data-testid="individual-purchase-readonly"]')
    assert section.is_visible()
    assert "Not configured" in section.inner_text()
    # No editable price input on source-managed courses.
    assert page.locator('input[name="individual_price_eur"]').count() == 0

    page.screenshot(
        path=str(tmp_path / "issue-490-stripe-not-configured.png"),
        full_page=True,
    )
    context.close()
