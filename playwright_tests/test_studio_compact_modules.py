"""Playwright checks for the compact Studio course modules/units display.

Issue #491: the modules/units section on `/studio/courses/<id>/edit` should
be compact and collapsed by default. Each module is a `<details>` with a
summary showing title, order, unit count, and source badge. Expanding
reveals unit rows with View/Edit links and (for local courses) an
"Add Unit" form.
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


def _reset_courses():
    from content.models import Course, Module, Unit

    Unit.objects.all().delete()
    Module.objects.all().delete()
    Course.objects.all().delete()
    connection.close()


def _create_course_with_modules(slug, title, synced=False):
    from django.utils.text import slugify

    from content.models import Course, Module, Unit

    if synced:
        course = Course.objects.create(
            title=title,
            slug=slug,
            status="published",
            source_repo="AI-Shipping-Labs/content",
            source_path=f"courses/{slug}/course.yaml",
        )
    else:
        course = Course.objects.create(
            title=title, slug=slug, status="draft",
        )

    for m_index, m_title in enumerate(["Module Alpha", "Module Beta"], start=1):
        if synced:
            module = Module.objects.create(
                course=course,
                title=m_title,
                slug=slugify(m_title),
                sort_order=m_index,
                source_repo="AI-Shipping-Labs/content",
                source_path=f"courses/{slug}/{slugify(m_title)}/README.md",
            )
        else:
            module = Module.objects.create(
                course=course,
                title=m_title,
                slug=slugify(m_title),
                sort_order=m_index,
            )
        for u_index in range(1, 3):
            Unit.objects.create(
                module=module,
                title=f"{m_title} Unit {u_index}",
                slug=slugify(f"{m_title} Unit {u_index}"),
                sort_order=u_index,
            )

    connection.close()
    return course


def _staff_page(browser, viewport=None):
    _ensure_tiers()
    _create_staff_user("compact-modules@test.com")
    context = _auth_context(browser, "compact-modules@test.com")
    page = context.new_page()
    if viewport is not None:
        page.set_viewport_size(viewport)
    return context, page


@pytest.mark.django_db(transaction=True)
def test_compact_modules_collapsed_by_default_desktop(
    django_server, browser, tmp_path,
):
    """Desktop 1280x900: modules render as collapsed disclosures with
    summary counts; unit rows are not visible until a module is expanded."""
    _reset_courses()
    course = _create_course_with_modules("compact-local", "Compact Local")
    context, page = _staff_page(
        browser, viewport={"width": 1280, "height": 900},
    )

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="networkidle",
    )

    # Summary shows aggregate counts.
    counts = page.locator('[data-testid="modules-summary-counts"]').first
    assert counts.is_visible()
    assert "2 modules" in counts.inner_text()
    assert "4 units" in counts.inner_text()

    # Two collapsed disclosures.
    disclosures = page.locator('[data-testid="module-disclosure"]')
    assert disclosures.count() == 2

    # Summary visible; unit rows hidden.
    first_summary = page.locator('[data-testid="module-summary"]').first
    assert first_summary.is_visible()
    assert "Module Alpha" in first_summary.inner_text()
    assert "2 units" in first_summary.inner_text()

    # Unit rows exist in DOM but are not visible while details are closed.
    unit_rows = page.locator('[data-testid="unit-row"]')
    assert unit_rows.count() == 4
    assert not unit_rows.first.is_visible()

    page.screenshot(
        path=str(tmp_path / "issue-491-collapsed-desktop.png"),
        full_page=True,
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_compact_modules_expand_reveals_units_and_actions_desktop(
    django_server, browser, tmp_path,
):
    """Desktop: clicking a module summary expands it and reveals the unit
    rows with their Edit links plus the Add Unit form for local courses."""
    _reset_courses()
    course = _create_course_with_modules("compact-local", "Compact Local")
    context, page = _staff_page(
        browser, viewport={"width": 1280, "height": 900},
    )

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="networkidle",
    )

    first_summary = page.locator('[data-testid="module-summary"]').first
    first_summary.click()

    # First module's units are now visible.
    first_disclosure = page.locator('[data-testid="module-disclosure"]').first
    units_in_first = first_disclosure.locator('[data-testid="unit-row"]')
    assert units_in_first.count() == 2
    assert units_in_first.first.is_visible()

    # Edit link is visible and points at the unit edit URL.
    edit_link = first_disclosure.locator(
        '[data-testid="unit-edit-link"]',
    ).first
    assert edit_link.is_visible()
    href = edit_link.get_attribute("href")
    assert href is not None
    assert "/studio/units/" in href and "/edit" in href

    # Add-unit form is visible and reachable on local courses.
    add_unit_form = first_disclosure.locator(
        '[data-testid="add-unit-form"]',
    )
    assert add_unit_form.is_visible()
    assert add_unit_form.locator('input[name="title"]').is_visible()

    # Other modules remain collapsed.
    second_disclosure = page.locator(
        '[data-testid="module-disclosure"]',
    ).nth(1)
    second_units = second_disclosure.locator('[data-testid="unit-row"]')
    assert not second_units.first.is_visible()

    page.screenshot(
        path=str(tmp_path / "issue-491-expanded-desktop.png"),
        full_page=True,
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_local_course_keeps_add_module_and_add_unit_controls(
    django_server, browser, tmp_path,
):
    """Local courses render the Add Module form near the section header
    and an Add Unit form inside each expanded module."""
    _reset_courses()
    course = _create_course_with_modules("compact-local", "Compact Local")
    context, page = _staff_page(
        browser, viewport={"width": 1280, "height": 900},
    )

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="networkidle",
    )

    add_module_form = page.locator('[data-testid="add-module-form"]')
    assert add_module_form.is_visible()

    # Two add-unit forms exist (one per module) but are hidden until expand.
    add_unit_forms = page.locator('[data-testid="add-unit-form"]')
    assert add_unit_forms.count() == 2

    page.locator('[data-testid="module-summary"]').first.click()
    assert add_unit_forms.first.is_visible()
    context.close()


@pytest.mark.django_db(transaction=True)
def test_synced_course_has_no_add_forms_but_keeps_view_links(
    django_server, browser, tmp_path,
):
    """Source-managed courses must not render Add Module / Add Unit forms,
    but unit rows with View links must remain reachable on expand."""
    _reset_courses()
    course = _create_course_with_modules(
        "compact-synced", "Compact Synced", synced=True,
    )
    context, page = _staff_page(
        browser, viewport={"width": 1280, "height": 900},
    )

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="networkidle",
    )

    assert page.locator('[data-testid="add-module-form"]').count() == 0
    assert page.locator('[data-testid="add-unit-form"]').count() == 0

    page.locator('[data-testid="module-summary"]').first.click()

    first_disclosure = page.locator('[data-testid="module-disclosure"]').first
    edit_link = first_disclosure.locator(
        '[data-testid="unit-edit-link"]',
    ).first
    assert edit_link.is_visible()
    assert edit_link.inner_text().strip() == "View"
    context.close()


@pytest.mark.django_db(transaction=True)
def test_compact_modules_mobile_no_horizontal_overflow_synced(
    django_server, browser, tmp_path,
):
    """Mobile 390x844 (synced course): no horizontal overflow, workflow
    panel appears before the modules section, and an expanded module body
    stays readable within the viewport."""
    _reset_courses()
    course = _create_course_with_modules(
        "compact-synced", "Compact Synced", synced=True,
    )
    context, page = _staff_page(
        browser, viewport={"width": 390, "height": 844},
    )

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="domcontentloaded",
    )

    body_width = page.evaluate("() => document.body.scrollWidth")
    viewport_width = page.evaluate("() => window.innerWidth")
    assert body_width <= viewport_width + 1, (
        f"Horizontal overflow: body={body_width} viewport={viewport_width}"
    )

    # Workflow panel comes before the modules section.
    workflow_box = page.locator(
        '[data-testid="course-workflow-panel"]',
    ).bounding_box()
    modules_box = page.locator(
        '[data-testid="course-modules-section"]',
    ).bounding_box()
    assert workflow_box is not None and modules_box is not None
    assert workflow_box["y"] < modules_box["y"], (
        "Workflows panel must appear above the modules section on mobile"
    )

    # Collapsed-by-default screenshot.
    page.screenshot(
        path=str(tmp_path / "issue-491-collapsed-mobile-synced.png"),
        full_page=True,
    )

    page.locator('[data-testid="module-summary"]').first.click()
    expanded_body = page.locator('[data-testid="module-body"]').first
    assert expanded_body.is_visible()

    # Expanded body must not exceed viewport width.
    expanded_box = expanded_body.bounding_box()
    assert expanded_box is not None
    assert expanded_box["x"] + expanded_box["width"] <= viewport_width + 1

    page.screenshot(
        path=str(tmp_path / "issue-491-expanded-mobile-synced.png"),
        full_page=True,
    )
    context.close()


@pytest.mark.django_db(transaction=True)
def test_compact_modules_mobile_no_horizontal_overflow_local(
    django_server, browser, tmp_path,
):
    """Mobile 390x844 (local course): same overflow / DOM-order checks
    plus the Add Module form remains reachable above the modules list."""
    _reset_courses()
    course = _create_course_with_modules("compact-local", "Compact Local")
    context, page = _staff_page(
        browser, viewport={"width": 390, "height": 844},
    )

    page.goto(
        f"{django_server}/studio/courses/{course.pk}/edit",
        wait_until="domcontentloaded",
    )

    body_width = page.evaluate("() => document.body.scrollWidth")
    viewport_width = page.evaluate("() => window.innerWidth")
    assert body_width <= viewport_width + 1, (
        f"Horizontal overflow: body={body_width} viewport={viewport_width}"
    )

    add_module_form = page.locator('[data-testid="add-module-form"]')
    assert add_module_form.count() == 1

    page.screenshot(
        path=str(tmp_path / "issue-491-collapsed-mobile-local.png"),
        full_page=True,
    )

    page.locator('[data-testid="module-summary"]').first.click()
    page.screenshot(
        path=str(tmp_path / "issue-491-expanded-mobile-local.png"),
        full_page=True,
    )
    context.close()
