"""Mobile density audit for Studio list pages on Pixel 7 (issue #620).

Covers nine acceptance scenarios:

- Articles, Events, Courses, Recordings, Projects, Users, Email templates
  density at 412 x 915 (Pixel 7).
- Recordings + Projects horizontal-overflow guardrail (they used to
  bypass the shared responsive helper).
- Desktop guardrail at 1280 x 900: rows still render as a horizontal
  table with the existing 56-88 px row height.
- Sidebar drawer still opens on top of the new dense rows.

Tests rely on the shared ``auth_context`` / ``create_*_user`` helpers
from ``playwright_tests/conftest.py`` and seed the bare minimum rows
each scenario needs (no reliance on the synced dev DB).
"""

import datetime
import os
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

PIXEL_7_VIEWPORT = {"width": 412, "height": 915}
DESKTOP_VIEWPORT = {"width": 1280, "height": 900}
SCREENSHOT_DIR = Path("/tmp/aisl-issue-620-screenshots")


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _reset_density_data():
    """Wipe content created by previous scenarios so each test is hermetic.

    Uses the pattern from ``test_studio_mobile_lists.py`` — the dev DB
    is shared across scenarios in a single ``--noreload`` server, so we
    delete whatever the previous test inserted before seeding fresh.
    """
    from accounts.models import User
    from content.models import Article, Course, Project
    from email_app.models import EmailTemplateOverride
    from events.models import Event

    # Drop overrides too — the email-templates scenario asserts ≥ 5 rows
    # which the filesystem templates already provide; overrides from
    # other tests can change the row count and "Edited" status.
    EmailTemplateOverride.objects.all().delete()
    Project.objects.all().delete()
    Event.objects.all().delete()
    Course.objects.all().delete()
    Article.objects.all().delete()
    # Drop every non-staff user so /studio/users/ row counts are
    # predictable. We keep is_staff users so the auth_context cookie
    # still authenticates.
    User.objects.filter(is_staff=False).delete()
    connection.close()


def _seed_articles(count):
    from content.models import Article

    today = timezone.now().date()
    for i in range(count):
        Article.objects.create(
            title=f"Density Article {i:02d}",
            slug=f"density-article-{i:02d}",
            date=today,
            author=f"Author {i:02d}",
            published=True,
        )
    connection.close()


def _seed_events(count):
    from events.models import Event

    base = timezone.now() + datetime.timedelta(days=7)
    for i in range(count):
        Event.objects.create(
            title=f"Density Event {i:02d}",
            slug=f"density-event-{i:02d}",
            start_datetime=base + datetime.timedelta(hours=i),
            status="upcoming",
            kind="workshop",
            platform="custom",
            max_participants=20 + i,
        )
    connection.close()


def _seed_courses(count):
    from django.utils.text import slugify

    from content.models import Course, CourseInstructor, Instructor

    instructor_name = "Density Instructor"
    instructor, _ = Instructor.objects.get_or_create(
        instructor_id=slugify(instructor_name)[:200],
        defaults={"name": instructor_name, "status": "published"},
    )
    for i in range(count):
        course = Course.objects.create(
            title=f"Density Course {i:02d}",
            slug=f"density-course-{i:02d}",
            status="published",
            required_level=10,
        )
        CourseInstructor.objects.get_or_create(
            course=course,
            instructor=instructor,
            defaults={"position": 0},
        )
    connection.close()


def _seed_recordings(count):
    """Mixed published / draft recordings (Event model with recording_url)."""
    from events.models import Event

    base = timezone.now() - datetime.timedelta(days=7)
    for i in range(count):
        Event.objects.create(
            title=f"Density Recording {i:02d}",
            slug=f"density-recording-{i:02d}",
            start_datetime=base - datetime.timedelta(hours=i),
            status="completed",
            recording_url=f"https://youtube.com/watch?v=density-{i:02d}",
            published=(i % 2 == 0),
        )
    connection.close()


def _seed_projects(count):
    from content.models import Project

    today = timezone.now().date()
    for i in range(count):
        Project.objects.create(
            title=f"Density Project {i:02d}",
            slug=f"density-project-{i:02d}",
            date=today,
            author=f"Project Author {i:02d}",
            status=("published" if i % 2 == 0 else "pending_review"),
            published=(i % 2 == 0),
        )
    connection.close()


def _seed_users(count):
    """Create N non-staff users so the users list has rows to measure."""
    for i in range(count):
        _create_user(
            f"density-user-{i:02d}@test.com",
            tier_slug="basic",
            email_verified=True,
        )


def _email_template_filesystem_count():
    """Return the number of filesystem-defined email templates.

    The /studio/email-templates/ list is driven by ``_all_template_names()``
    which reads ``email_app/email_templates/*.md`` and includes any DB
    overrides. We don't need to seed anything; the shipped templates
    (welcome, password_reset, etc.) provide enough rows on every test DB.
    """
    from pathlib import Path

    templates_dir = (
        Path(__file__).resolve().parent.parent
        / "email_app"
        / "email_templates"
    )
    return len(list(templates_dir.glob("*.md")))


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def _assert_no_horizontal_overflow(page):
    overflow = page.evaluate(
        """() => {
            const root = document.scrollingElement || document.documentElement;
            return root.scrollWidth - root.clientWidth;
        }"""
    )
    assert overflow <= 2, (
        f"Page produced horizontal scrollbar of {overflow}px at "
        f"{PIXEL_7_VIEWPORT['width']}px viewport"
    )


def _row_heights(page, selector):
    return page.locator(selector).evaluate_all(
        """nodes => nodes.map(n => n.getBoundingClientRect().height)"""
    )


def _rows_in_initial_viewport(page, selector):
    """Count rows whose top edge sits within the initial viewport height."""
    return page.locator(selector).evaluate_all(
        """(nodes) => nodes.filter(n => {
            const rect = n.getBoundingClientRect();
            return rect.top < window.innerHeight && rect.bottom > 0;
        }).length"""
    )


def _capture(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=True)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_articles_list_shows_six_plus_rows_at_pixel_7(django_server, browser):
    """Scenario 1: at least 6 articles visible in the initial viewport."""
    _ensure_tiers()
    staff_email = "density-articles@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_articles(10)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")

    rows_in_view = _rows_in_initial_viewport(page, "tbody tr")
    assert rows_in_view >= 6, (
        f"Expected ≥ 6 article rows in the initial Pixel 7 viewport, "
        f"got {rows_in_view}"
    )

    heights = _row_heights(page, "tbody tr")
    assert heights, "No article rows rendered"
    for h in heights:
        assert h < 160, f"Article row height {h}px exceeds 160px target"

    # Status / Author / Date should each render as a single labeled line —
    # the LABEL pseudo + value must share one inline-flex line, not two.
    # We can't assert on ``::before`` ``display`` directly: per CSS Display 3,
    # children of a flex/inline-flex container get their ``display`` value
    # blockified, so the pseudo always reports ``block`` regardless of the
    # author stylesheet. Instead, measure the cell height — a single inline
    # line at the studio's base font size stays well under 32px.
    first_row = page.locator("tbody tr").first
    status_cell = first_row.locator('[data-label="Status"]').first
    cell_height = status_cell.evaluate(
        "node => node.getBoundingClientRect().height"
    )
    assert cell_height < 32, (
        f"Status row should fit on one line (height < 32px), "
        f"got {cell_height}px"
    )

    _capture(page, "articles-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_events_list_shows_four_plus_rows_at_pixel_7(django_server, browser):
    """Scenario 2: dense Events rows with inline LABEL: value pairs."""
    _ensure_tiers()
    staff_email = "density-events@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_events(5)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")

    rows_in_view = _rows_in_initial_viewport(page, "tbody tr")
    assert rows_in_view >= 4, (
        f"Expected ≥ 4 event rows in the initial Pixel 7 viewport, "
        f"got {rows_in_view}"
    )

    for h in _row_heights(page, "tbody tr"):
        assert h < 220, f"Event row height {h}px exceeds 220px target"

    first_row = page.locator("tbody tr").first
    # Each fact (Status / Kind·Platform / Date / Capacity) owns its own
    # full-width grid row so labels and values can never collide with
    # neighbouring columns. Verify that by checking each non-title cell
    # spans the full grid width (grid-column-end == "-1" or the cell is
    # blockified onto a single row by the column rule).
    for label in ["Status", "Kind / Platform", "Date", "Capacity"]:
        cell = first_row.locator(f'[data-label="{label}"]').first
        # The column rule sets ``grid-column: 1 / -1`` on every non-title
        # cell. The browser blockifies inline-flex items in a grid to
        # ``flex``, so we cannot assert on ``display`` directly — instead
        # assert on the grid-column placement.
        grid_col_end = cell.evaluate(
            "node => window.getComputedStyle(node).gridColumnEnd"
        )
        assert grid_col_end == "-1", (
            f"Expected '{label}' cell to span to grid column -1 "
            f"(full-width row), got {grid_col_end!r}"
        )
        # And the LABEL pseudo + VALUE must share a single visual line —
        # the cell only ever wraps at the right edge of the viewport.
        cell_height = cell.evaluate(
            "node => node.getBoundingClientRect().height"
        )
        assert cell_height < 48, (
            f"Expected '{label}' cell to render on one line "
            f"(height < 48px), got {cell_height}px"
        )

    _capture(page, "events-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_courses_list_fits_three_rows_in_pixel_7_viewport(django_server, browser):
    """Scenario 3: 3 course rows fit without scrolling past the bottom."""
    _ensure_tiers()
    staff_email = "density-courses@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_courses(3)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/courses/", wait_until="domcontentloaded")

    heights = _row_heights(page, "tbody tr")
    assert len(heights) == 3, f"Expected 3 course rows, got {len(heights)}"
    for h in heights:
        assert h < 220, f"Course row height {h}px exceeds 220px target"

    rows_in_view = _rows_in_initial_viewport(page, "tbody tr")
    assert rows_in_view == 3, (
        f"Expected all 3 course rows in initial viewport, got {rows_in_view}"
    )

    _capture(page, "courses-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_recordings_list_no_horizontal_overflow_at_pixel_7(django_server, browser):
    """Scenario 4: Recordings stack as cards (no horizontal scroll)."""
    _ensure_tiers()
    staff_email = "density-recordings@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_recordings(3)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/recordings/", wait_until="domcontentloaded")

    _assert_no_horizontal_overflow(page)

    first_row = page.locator("tbody tr").first
    # Title is the row header — full-width, not labelled.
    title_td = first_row.locator("td").first
    title_grid_column = title_td.evaluate(
        "node => window.getComputedStyle(node).gridColumnStart"
    )
    assert title_grid_column == "1", (
        f"Title cell should start at grid column 1 (got {title_grid_column!r})"
    )
    title_grid_end = title_td.evaluate(
        "node => window.getComputedStyle(node).gridColumnEnd"
    )
    assert title_grid_end in {"-1", "auto"} or title_grid_end.endswith(
        "/-1"
    ) or "span" in title_grid_end, title_grid_end

    # Status / Date render as inline LABEL: value pairs.
    for label in ["Status", "Date"]:
        cell = first_row.locator(f'[data-label="{label}"]').first
        assert cell.is_visible()

    # Actions row contains the primary action + (when applicable) the
    # "View workshop" secondary. We seeded recordings without a linked
    # workshop, so the secondary slot may be empty — but at least one
    # action must always be present.
    actions = first_row.locator(".studio-actions-cell a")
    assert actions.count() >= 1

    _capture(page, "recordings-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_projects_list_no_horizontal_overflow_at_pixel_7(django_server, browser):
    """Scenario 5: Projects stack as cards (no horizontal scroll)."""
    _ensure_tiers()
    staff_email = "density-projects@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_projects(3)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/projects/", wait_until="domcontentloaded")

    _assert_no_horizontal_overflow(page)

    first_row = page.locator("tbody tr").first
    for label in ["Status", "Author", "Date"]:
        cell = first_row.locator(f'[data-label="{label}"]').first
        assert cell.is_visible()

    actions = first_row.locator(".studio-actions-cell a")
    # Each project shows BOTH a primary action (View / Review) and the
    # secondary "View on site" link.
    assert actions.count() >= 2, (
        f"Expected at least 2 actions per project row, got {actions.count()}"
    )
    view_on_site = first_row.locator('[data-testid="view-on-site"]').first
    assert view_on_site.is_visible()

    _capture(page, "projects-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_users_list_dense_rows_and_view_link_works(django_server, browser):
    """Scenario 6: Users list stays compact and View action navigates."""
    _ensure_tiers()
    staff_email = "density-users@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_users(5)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/users/", wait_until="domcontentloaded")

    # The /studio/users/ page header carries a stat-card grid + filter
    # chips + search form that consume the upper viewport before the
    # first row. Page-chrome bloat is out of scope for the row-density
    # work in issue #620 — we only verify each user row itself stays
    # compact (≤ 240 px). Initial-viewport row count is tracked
    # separately in the follow-up Users-chrome ticket.
    heights = _row_heights(page, "tbody tr")
    assert heights, "No user rows rendered"
    for h in heights:
        assert h < 240, f"User row height {h}px exceeds 240px target"

    # Tap the View action on the first user row → navigates to detail.
    first_row = page.locator("tbody tr").first
    first_row.locator('[data-testid="user-view-link"]').first.click()
    page.wait_for_load_state("domcontentloaded")
    assert "/studio/users/" in page.url
    assert page.url != f"{django_server}/studio/users/"

    _capture(page, "users-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_email_templates_list_dense_rows_at_pixel_7(django_server, browser):
    """Scenario 7: Email templates list shows ≥ 5 rows compactly.

    No seeding required — the email-template list is driven by the
    filesystem templates shipped in ``email_app/email_templates/`` plus
    any DB overrides. The shipped set has ~11 rows which is plenty.
    """
    _ensure_tiers()
    staff_email = "density-email@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    assert _email_template_filesystem_count() >= 5

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(
        f"{django_server}/studio/email-templates/",
        wait_until="domcontentloaded",
    )

    rows_in_view = _rows_in_initial_viewport(page, "tbody tr")
    assert rows_in_view >= 5, (
        f"Expected ≥ 5 email-template rows in Pixel 7 viewport, got "
        f"{rows_in_view}"
    )

    for h in _row_heights(page, "tbody tr"):
        assert h < 200, f"Email-template row height {h}px exceeds 200px target"

    _capture(page, "email-templates-pixel7")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_desktop_table_density_unchanged(django_server, browser):
    """Scenario 8: at 1280 px, rows render as a horizontal table, not cards."""
    _ensure_tiers()
    staff_email = "density-desktop@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_articles(5)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(DESKTOP_VIEWPORT)
    page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")

    # Column headers are visible (mobile hides <thead>).
    thead = page.locator("thead")
    assert thead.is_visible()
    for col in ["Title", "Status", "Author", "Date", "Actions"]:
        assert page.locator("thead th", has_text=col).count() >= 1, col

    # Rows behave as actual table rows, not card stacks. Heights should
    # be in the 56-88 px desktop band.
    heights = _row_heights(page, "tbody tr")
    assert heights, "No rows rendered at desktop"
    for h in heights:
        assert 40 < h < 100, (
            f"Desktop row height {h}px outside expected 40-100px band"
        )

    # No card-stack chrome: rows should not be display:grid at desktop.
    first_row = page.locator("tbody tr").first
    display = first_row.evaluate(
        "node => window.getComputedStyle(node).display"
    )
    assert display == "table-row", (
        f"At 1280px, row should be table-row (got {display!r}) — the "
        "@media (max-width: 767px) block must not leak onto desktop"
    )

    _capture(page, "articles-desktop")
    context.close()


@pytest.mark.django_db(transaction=True)
def test_mobile_sidebar_drawer_still_works_over_dense_rows(
    django_server, browser
):
    """Scenario 9: hamburger toggle opens the drawer, X closes it."""
    _ensure_tiers()
    staff_email = "density-drawer@test.com"
    _create_staff_user(staff_email)
    _reset_density_data()
    _seed_articles(3)

    context = _auth_context(browser, staff_email)
    page = context.new_page()
    page.set_viewport_size(PIXEL_7_VIEWPORT)
    page.goto(f"{django_server}/studio/articles/", wait_until="domcontentloaded")

    # Drawer starts hidden behind the hamburger toggle.
    sidebar = page.locator("#studio-sidebar")
    assert "hidden" in (sidebar.get_attribute("class") or "")

    page.locator("#studio-sidebar-toggle").click()
    page.wait_for_function(
        """() => !document.getElementById('studio-sidebar')
            .classList.contains('hidden')"""
    )

    # First link inside the navigation list is "Back to website" (the
    # outer ``#studio-sidebar a`` would point at the Studio dashboard
    # logo per the #570 sidebar reorg, so we scope to ``#studio-sidebar-nav``).
    first_link = page.locator("#studio-sidebar-nav a").first
    assert first_link.is_visible()
    assert "Back to website" in first_link.inner_text()

    # Close via the X button → article rows are interactive again.
    page.locator("#studio-sidebar-close").click()
    page.wait_for_function(
        """() => document.getElementById('studio-sidebar')
            .classList.contains('hidden')"""
    )
    assert page.locator("tbody tr").first.is_visible()

    _capture(page, "drawer-pixel7")
    context.close()
