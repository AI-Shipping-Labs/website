"""Playwright coverage for the date-derived sprint badge (#979).

A member scanning ``/sprints`` should be able to tell cohorts apart at a
glance: which one is starting soon, which is active, which is ending soon,
and which has ended -- driven by ``start_date`` / ``end_date``, not the
stored ``status`` field. These flows seed sprints with dates relative to
the run's "today" (so the suite is not pinned to a calendar date) and assert
each lifecycle badge renders on the ``/sprints`` card and the detail page,
and that a finished-but-still-status-active cohort reads "Ended".

Screenshots are written to ``.tmp/aisl-issue-979-screenshots`` for tester
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

SCREENSHOT_DIR = Path(__file__).resolve().parents[1] / ".tmp" / "aisl-issue-979-screenshots"

# The badge window W defaults to 7 days. The relative offsets below
# (3 days out, 2 weeks in, ends in 3 days, ended last week) all sit clearly
# inside / outside that window so the assertions are stable.


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


def _create_sprint(name, slug, start_date, duration_weeks=6, status="active"):
    from django.db import connection

    from plans.models import Sprint

    sprint = Sprint.objects.create(
        name=name,
        slug=slug,
        start_date=start_date,
        duration_weeks=duration_weeks,
        status=status,
        min_tier_level=20,
    )
    connection.close()
    return sprint


def _start_for_end_in(days_from_today, duration_weeks):
    """start_date so end_date (= start + duration_weeks) is ``days_from_today``
    days from today. ``end_date`` is the derived hand-off date."""
    today = datetime.date.today()
    return today + datetime.timedelta(days=days_from_today) - datetime.timedelta(
        weeks=duration_weeks
    )


def _card_badge_text(page, sprint_slug):
    """Badge text on the /sprints card for the given sprint slug."""
    card = page.locator(
        f'[data-testid="sprints-sprint-card"]:has(a[href$="/sprints/{sprint_slug}"])'
    )
    return card.locator('[data-testid="sprints-sprint-status"]').inner_text()


@pytest.mark.django_db(transaction=True)
class TestSprintBadge:
    def test_member_tells_cohorts_apart_on_sprints_page(
        self, django_server, browser, django_db_blocker
    ):
        """Four cohorts seeded across the lifecycle each show their own
        date-derived badge on /sprints."""
        today = datetime.date.today()
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            # Starts in 3 days (within W) -> Starting soon.
            _create_sprint(
                "Soon Cohort", "soon-cohort",
                start_date=today + datetime.timedelta(days=3),
                duration_weeks=6,
            )
            # Started 2 weeks ago, ends in ~4 weeks -> Active.
            _create_sprint(
                "Active Cohort", "active-cohort",
                start_date=today - datetime.timedelta(days=14),
                duration_weeks=6,
            )
            # Ends in 3 days (within W of end), started weeks ago -> Ending soon.
            _create_sprint(
                "Ending Cohort", "ending-cohort",
                start_date=_start_for_end_in(3, duration_weeks=6),
                duration_weeks=6,
            )
            # Ended last week (end_date in the past) -> Ended.
            _create_sprint(
                "Done Cohort", "done-cohort",
                start_date=_start_for_end_in(-7, duration_weeks=6),
                duration_weeks=6,
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")

        # Each card shows the right lifecycle label (CSS-uppercased; compare
        # case-insensitively).
        assert "starting soon" in _card_badge_text(page, "soon-cohort").lower()
        assert "active" in _card_badge_text(page, "active-cohort").lower()
        assert "ending soon" in _card_badge_text(page, "ending-cohort").lower()
        assert "ended" in _card_badge_text(page, "done-cohort").lower()

        # The ended cohort must NOT read its stored status ("Active") -- the
        # badge is date-derived.
        assert "active" not in _card_badge_text(page, "done-cohort").lower()
        _shot(page, "01-sprints-lifecycle-badges")
        ctx.close()

    def test_active_sprint_detail_badge(
        self, django_server, browser, django_db_blocker
    ):
        """Opening an active sprint shows an Active badge on the detail page."""
        today = datetime.date.today()
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            _create_sprint(
                "Active Cohort", "active-cohort",
                start_date=today - datetime.timedelta(days=7),
                duration_weeks=6,
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
        page.locator('a[href$="/sprints/active-cohort"]').first.click()
        page.wait_for_load_state("domcontentloaded")

        assert page.locator('[data-testid="sprint-detail-name"]').inner_text() == (
            "Active Cohort"
        )
        badge = page.locator('[data-testid="sprint-status-badge"]')
        assert "active" in badge.inner_text().lower()
        _shot(page, "02-active-detail-badge")
        ctx.close()

    def test_finished_cohort_reads_ended_even_when_status_active(
        self, django_server, browser, django_db_blocker
    ):
        """A sprint whose end_date is past but status is still 'active' reads
        Ended (not Active) on the detail badge."""
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            _create_sprint(
                "Past Cohort", "past-cohort",
                start_date=_start_for_end_in(-7, duration_weeks=6),
                duration_weeks=6,
                status="active",  # stored status stays active on purpose
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/sprints/past-cohort", wait_until="domcontentloaded"
        )

        badge = page.locator('[data-testid="sprint-status-badge"]')
        assert "ended" in badge.inner_text().lower()
        assert "active" not in badge.inner_text().lower()
        _shot(page, "03-past-cohort-ended-badge")
        ctx.close()

    def test_ending_soon_agrees_on_list_and_detail(
        self, django_server, browser, django_db_blocker
    ):
        """A cohort ending in 2 days reads 'Ending soon' on both the /sprints
        card and the detail page (list and detail agree)."""
        with django_db_blocker.unblock():
            ensure_tiers()
            ensure_site_config_tiers()
            _clear_sprints()
            _create_sprint(
                "Wrap Cohort", "wrap-cohort",
                start_date=_start_for_end_in(2, duration_weeks=6),
                duration_weeks=6,
            )
            _create_user("main@test.com", tier_slug="main")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/sprints", wait_until="domcontentloaded")
        assert "ending soon" in _card_badge_text(page, "wrap-cohort").lower()
        _shot(page, "04-ending-soon-list")

        page.goto(
            f"{django_server}/sprints/wrap-cohort", wait_until="domcontentloaded"
        )
        badge = page.locator('[data-testid="sprint-status-badge"]')
        assert "ending soon" in badge.inner_text().lower()
        _shot(page, "05-ending-soon-detail")
        ctx.close()
