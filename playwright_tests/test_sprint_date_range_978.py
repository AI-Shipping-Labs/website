"""Playwright coverage for the sprint start--end (duration) range (#978).

A member should be able to read, at a glance, when a sprint runs from start
to finish -- not just when it starts. These flows verify the
``Start - End (N weeks)`` wording renders consistently on the sprint detail
page, the ``/sprints`` list, and the ``/activities`` cards, and that the
cross-year form shows the year on both sides.

Screenshots are written to ``/tmp/aisl-issue-978-screenshots`` for tester
review.
"""

import datetime
import os
from pathlib import Path

import pytest

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user
from playwright_tests.conftest import ensure_site_config_tiers, ensure_tiers

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Local-only: seeds the DB and injects a session cookie, so it cannot run
# against the deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only

SCREENSHOT_DIR = Path("/tmp/aisl-issue-978-screenshots")


def _shot(page, name):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=SCREENSHOT_DIR / f"{name}.png", full_page=False)


def _clear_sprints():
    from django.db import connection

    from plans.models import Plan, Sprint, SprintEnrollment

    Plan.objects.all().delete()
    SprintEnrollment.objects.all().delete()
    Sprint.objects.all().delete()
    connection.close()


def _create_sprint(
    name="June 2026",
    slug="june-2026",
    start_date=datetime.date(2026, 6, 17),
    duration_weeks=6,
    status="active",
    min_tier_level=20,
):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date,
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=min_tier_level,
    )
    connection.close()
    return sprint


def _format_date(value):
    return f"{value:%B} {value.day}, {value.year}"


def _expected_sprint_range(start_date, duration_weeks):
    end_date = start_date + datetime.timedelta(weeks=duration_weeks)
    if start_date.year == end_date.year:
        start_label = f"{start_date:%B} {start_date.day}"
        end_label = _format_date(end_date)
        return f"{start_label} – {end_label} ({duration_weeks} weeks)"
    return f"{_format_date(start_date)} – {_format_date(end_date)} ({duration_weeks} weeks)"


@pytest.mark.django_db(transaction=True)
class TestSprintDateRange:
    def test_member_reads_when_current_sprint_runs(
        self, django_server, browser, django_db_blocker
    ):
        """Detail page shows the full window, drops the old 'Starts' line,
        and keeps the status badge."""
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            start_date = datetime.date.today() - datetime.timedelta(days=7)
            _create_sprint(
                name="Current Sprint",
                slug="current-sprint",
                start_date=start_date,
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/sprints/current-sprint",
            wait_until="domcontentloaded",
        )

        assert page.locator('[data-testid="sprint-detail-name"]').inner_text() == (
            "Current Sprint"
        )
        date_line = page.locator('[data-testid="sprint-date-range"]').inner_text()
        assert date_line == _expected_sprint_range(start_date, 6)
        # The old "Starts <date> · N weeks" wording is gone.
        body = page.locator("body").inner_text()
        assert f"Starts {_format_date(start_date)}" not in body
        # The status badge survives, unchanged by this feature.
        badge = page.locator('[data-testid="sprint-status-badge"]')
        assert badge.is_visible()
        # The badge text is CSS-uppercased (the underlying display value is
        # "Active"); compare case-insensitively.
        assert "active" in badge.inner_text().lower()
        _shot(page, "01-sprint-detail-date-range")
        ctx.close()

    def test_sprints_list_shows_full_window_per_cohort(
        self, django_server, browser, django_db_blocker
    ):
        """Each /sprints card shows a start and an end date with the
        duration in parentheses, not just a start + separate duration."""
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            _create_sprint()
            _create_sprint(
                name="August 2026",
                slug="august-2026",
                start_date=datetime.date(2026, 8, 1),
                duration_weeks=8,
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

        ranges = page.locator('[data-testid="sprints-sprint-dates"]')
        assert ranges.count() == 2
        texts = {ranges.nth(i).inner_text() for i in range(ranges.count())}
        assert "June 17 – July 29, 2026 (6 weeks)" in texts
        assert "August 1 – September 26, 2026 (8 weeks)" in texts
        _shot(page, "02-sprints-list-windows")
        ctx.close()

    def test_visitor_activities_shows_start_and_end(
        self, django_server, page, django_db_blocker
    ):
        """An anonymous visitor on /activities sees the start--end (duration)
        range, consistent with the /sprints and detail wording."""
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            _create_sprint()

        page.goto(f"{django_server}/activities", wait_until="domcontentloaded")

        card = page.locator('[data-testid="activities-sprint-card"]').first
        assert card.is_visible()
        dates = card.locator('[data-testid="activities-sprint-dates"]')
        assert dates.inner_text() == "June 17 – July 29, 2026 (6 weeks)"
        _shot(page, "03-activities-start-end")

    def test_cross_year_sprint_shows_year_on_both_sides(
        self, django_server, browser, django_db_blocker
    ):
        """A sprint that crosses the year boundary shows the full year on
        both the start and the end date."""
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            _create_sprint(
                name="December 2025",
                slug="december-2025",
                start_date=datetime.date(2025, 12, 16),
                duration_weeks=6,
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/sprints/december-2025",
            wait_until="domcontentloaded",
        )

        date_line = page.locator('[data-testid="sprint-date-range"]').inner_text()
        assert date_line == "December 16, 2025 – January 27, 2026 (6 weeks)"
        _shot(page, "04-cross-year-range")
        ctx.close()
