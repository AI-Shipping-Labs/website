"""Playwright E2E tests for Event series (issue #564, renamed from
event-group in #575).

Nine scenarios covering the Studio create/edit/delete flow, the origin
gate, the sync-isolation guarantee, the add-occurrence form, the
validation guard, and the two public-surface flows.

Usage:
    uv run pytest playwright_tests/test_event_series.py -v
"""

import os
import re
from datetime import date, datetime, timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


def _reset_event_state():
    """Delete all event-series state for a clean slate."""
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _next_weekday(weekday=2):
    """Return the next date with the given weekday (default Wednesday)."""
    today = date.today()
    delta = (weekday - today.weekday()) % 7
    if delta == 0:
        delta = 7
    return today + timedelta(days=delta)


# ---------------------------------------------------------------------------
# Scenario 1: Staff creates a 6-week event series in one shot
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario1CreateSeries:
    def test_create_six_week_series(self, django_server, browser):
        _reset_event_state()
        _create_staff_user("staff-eg1@test.com")
        ctx = _auth_context(browser, "staff-eg1@test.com")
        page = ctx.new_page()

        page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
        # The list page shows the New event series button.
        assert page.locator('[data-testid="event-series-new-button"]').is_visible()
        page.locator('[data-testid="event-series-new-button"]').click()
        page.wait_for_url(re.compile(r".*/studio/event-series/new$"))

        start = _next_weekday(2)  # Wednesday
        page.fill('input[name="name"]', "Spring Workshop Series")
        page.fill('input[name="start_date"]', start.strftime("%d/%m/%Y"))
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="duration_hours"]', "1.5")
        page.fill('input[name="occurrences"]', "6")
        page.fill('input[name="timezone"]', "Europe/Berlin")

        page.locator('[data-testid="event-series-submit"]').click()
        page.wait_for_url(re.compile(r".*/studio/event-series/\d+/$"))

        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 6
        # Every row has a Draft status badge.
        draft_count = page.get_by_text("Draft", exact=False).count()
        assert draft_count >= 6

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 2: Studio-origin event is fully editable end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario2StudioEditable:
    def test_studio_event_full_edit(self, django_server, browser):
        from django.db import connection

        from events.models import Event, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg2@test.com")

        series = EventSeries(
            name="Spring Workshop Series",
            slug="spring-workshop-series-2",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        events = []
        base_dt = datetime(2026, 6, 3, 18, 0)
        for i in range(1, 7):
            ev = Event(
                title=f"Spring Workshop Series — Session {i}",
                slug=f"sws2-session-{i}",
                start_datetime=base_dt + timedelta(days=7 * (i - 1)),
                end_datetime=base_dt + timedelta(days=7 * (i - 1), hours=1, minutes=30),
                status="draft",
                origin="studio",
                event_series=series,
                series_position=i,
            )
            ev.save()
            events.append(ev)
        target = events[2]  # Session 3
        connection.close()

        ctx = _auth_context(browser, "staff-eg2@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        # Click Edit on Session 3
        page.locator(f'a[href="/studio/events/{target.pk}/edit"]').first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{target.pk}/edit$"))

        # No synced banner; Save Changes button visible.
        assert not page.get_by_text("This content is synced from GitHub").is_visible()
        assert page.get_by_text("Save Changes").is_visible()

        page.fill('input[name="title"]', "Spring Workshop — Special Session")
        page.fill('textarea[name="description"]', "We're moving this one indoors.")
        page.fill('input[name="event_time"]', "19:00")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{target.pk}/edit$"))

        target.refresh_from_db()
        assert target.title == "Spring Workshop — Special Session"
        assert "indoors" in target.description
        assert target.start_datetime.hour == 19

        # Studio events list: origin badge is `studio`.
        page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
        assert page.locator(
            f'tr:has(a[href="/studio/events/{target.pk}/edit"]) [data-testid="origin-badge"][data-origin="studio"]'
        ).first.is_visible()

        # The other 5 sessions still have their original titles.
        for other in events:
            if other.pk == target.pk:
                continue
            other.refresh_from_db()
            assert "Special Session" not in other.title
            assert other.start_datetime.hour == 18

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 3: GitHub-origin event is locked to its sync source
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario3GitHubLocked:
    def test_github_event_is_readonly(self, django_server, browser):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-eg3@test.com")
        gh = Event(
            title="GitHub-Synced Event",
            slug="gh-synced-event",
            start_datetime=timezone.now() + timedelta(days=7),
            origin="github",
            source_repo="AI-Shipping-Labs/content",
            source_path="events/gh-synced-event.yaml",
        )
        gh.save()
        connection.close()

        ctx = _auth_context(browser, "staff-eg3@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/events/{gh.pk}/edit",
            wait_until="domcontentloaded",
        )

        # Banner present.
        assert page.get_by_text("This content is synced from GitHub").first.is_visible()
        # Title input is disabled.
        assert page.locator('input[name="title"]').first.is_disabled()
        # Operational fields (status, platform, max_participants) remain editable.
        assert not page.locator('select[name="status"]').first.is_disabled()
        assert not page.locator('select[name="platform"]').first.is_disabled()
        assert not page.locator('input[name="max_participants"]').first.is_disabled()

        # Origin badge on the list.
        page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
        assert page.locator(
            f'tr:has(a[href="/studio/events/{gh.pk}/edit"]) [data-testid="origin-badge"][data-origin="github"]'
        ).first.is_visible()

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 4: A GitHub sync run leaves studio-origin events alone
#
# This is exercised exhaustively by the Django test in
# ``integrations/tests/test_event_origin_sync_isolation.py`` (which
# runs the real dispatcher against a temp repo). A Playwright variant
# would add nothing — there is no UI surface for "sync touched my
# event". We leave the assertion in place as a smoke test that the
# studio event survives a manual sync trigger through the Studio sync
# dashboard.
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario4SyncIsolation:
    def test_studio_event_survives_sync_dashboard_visit(
        self, django_server, browser
    ):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-eg4@test.com")
        ev = Event(
            title="Survives Sync",
            slug="survives-sync",
            description="We're moving this one indoors.",
            start_datetime=timezone.now() + timedelta(days=7),
            origin="studio",
        )
        ev.save()
        connection.close()

        ctx = _auth_context(browser, "staff-eg4@test.com")
        page = ctx.new_page()
        # Visit the sync dashboard (no actual sync run triggered — we
        # just confirm that any UI interaction here does not delete the
        # event row).
        page.goto(f"{django_server}/studio/sync/", wait_until="domcontentloaded")

        ev.refresh_from_db()
        assert ev.origin == "studio"
        assert ev.description == "We're moving this one indoors."

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 5: Staff adds one more occurrence to an existing series
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario5AddOccurrence:
    def test_add_one_more(self, django_server, browser):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg5@test.com")
        series = EventSeries(
            name="Add Series",
            slug="add-series-pw",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        for i in range(1, 7):
            Event(
                title=f"Session {i}",
                slug=f"add-series-pw-session-{i}",
                start_datetime=timezone.now() + timedelta(days=7 * i),
                origin="studio",
                event_series=series,
                series_position=i,
            ).save()
        connection.close()

        ctx = _auth_context(browser, "staff-eg5@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        future = date.today() + timedelta(days=80)
        page.fill('input[name="start_date"]', future.strftime("%d/%m/%Y"))
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series.pk}/$"))

        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 7

        new_event = series.events.order_by("-series_position").first()
        assert new_event.series_position == 7
        assert new_event.origin == "studio"
        assert new_event.event_series_id == series.pk

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 6: Staff deletes the series but keeps the events
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario6DeleteSeries:
    def test_delete_keeps_events(self, django_server, browser):
        from django.db import connection
        from django.utils import timezone

        from accounts.models import User
        from events.models import Event, EventRegistration, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg6@test.com")
        viewer, _ = User.objects.get_or_create(
            email="viewer-eg6@test.com",
            defaults={"email_verified": True},
        )
        series = EventSeries(
            name="Doomed Series",
            slug="doomed-series",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        events = []
        for i in range(1, 7):
            ev = Event(
                title=f"Session {i}",
                slug=f"doomed-session-{i}",
                start_datetime=timezone.now() + timedelta(days=7 * i),
                origin="studio",
                event_series=series,
                series_position=i,
            )
            ev.save()
            events.append(ev)
        EventRegistration.objects.create(event=events[0], user=viewer)
        connection.close()

        ctx = _auth_context(browser, "staff-eg6@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        # Bypass the JS confirm() dialog.
        page.on("dialog", lambda d: d.accept())
        page.locator('[data-testid="event-series-delete-submit"]').click()
        page.wait_for_url(re.compile(r".*/studio/events/$"))

        # Series is gone; the events remain in place with no series label.
        assert EventSeries.objects.filter(pk=series.pk).count() == 0
        assert Event.objects.filter(slug__startswith="doomed-session-").count() == 6
        # The registration row survives (DB not destroyed).
        assert (
            EventRegistration.objects.filter(event__slug="doomed-session-1").count()
            == 1
        )

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 7: Validation rejects unreasonable occurrence counts
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario7ValidationGuard:
    def test_zero_occurrences_rejected(self, django_server, browser):
        from events.models import Event, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg7@test.com")
        ctx = _auth_context(browser, "staff-eg7@test.com")
        page = ctx.new_page()

        # Zero is rejected.
        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )
        start = _next_weekday(2)
        page.fill('input[name="name"]', "Bad Zero")
        page.fill('input[name="start_date"]', start.strftime("%d/%m/%Y"))
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="duration_hours"]', "1")
        # Use evaluate to bypass the browser's min=1 enforcement and force
        # the server-side validation path.
        page.evaluate(
            "document.querySelector('input[name=\"occurrences\"]').removeAttribute('min')"
        )
        page.fill('input[name="occurrences"]', "0")
        page.locator('[data-testid="event-series-submit"]').click()
        # Stay on /new, error rendered, no rows created.
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/event-series/new" in page.url
        assert page.locator('[data-testid="error-occurrences"]').is_visible()
        assert EventSeries.objects.count() == 0
        assert Event.objects.count() == 0

        # 27 is rejected.
        page.fill('input[name="name"]', "Bad Too Many")
        page.fill('input[name="start_date"]', start.strftime("%d/%m/%Y"))
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="duration_hours"]', "1")
        page.evaluate(
            "document.querySelector('input[name=\"occurrences\"]').removeAttribute('max')"
        )
        page.fill('input[name="occurrences"]', "27")
        page.locator('[data-testid="event-series-submit"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/event-series/new" in page.url
        assert page.locator('[data-testid="error-occurrences"]').is_visible()
        assert EventSeries.objects.count() == 0
        assert Event.objects.count() == 0

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 8: Public visitor browses a series page
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario8PublicSeriesPage:
    def test_anonymous_views_series(self, django_server, browser):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        series = EventSeries(
            name="Public Series",
            slug="public-series",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        for i in range(1, 7):
            Event(
                title=f"Public Session {i}",
                slug=f"public-series-session-{i}",
                start_datetime=timezone.now() + timedelta(days=7 * i),
                status="upcoming",
                origin="studio",
                event_series=series,
                series_position=i,
            ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/groups/public-series",
            wait_until="domcontentloaded",
        )
        assert page.locator('[data-testid="series-name"]').inner_text() == "Public Series"
        rows = page.locator('[data-testid="series-event"]')
        assert rows.count() == 6

        # Click the first event — lands on the event detail page.
        page.locator('[data-testid="series-event-link"]').first.click()
        page.wait_for_url(re.compile(r".*/events/public-series-session-1$"))
        assert page.locator("h1").first.is_visible()

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 9: Public events listing surfaces the series link
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario9ListingShowsSeriesLink:
    def test_listing_shows_series_label(self, django_server, browser):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        series = EventSeries(
            name="Listed Public Series",
            slug="listed-public-series",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        Event(
            title="Grouped Event",
            slug="listed-grouped-event",
            start_datetime=timezone.now() + timedelta(days=7),
            status="upcoming",
            origin="studio",
            event_series=series,
            series_position=1,
        ).save()
        Event(
            title="Standalone Event",
            slug="listed-standalone-event",
            start_datetime=timezone.now() + timedelta(days=14),
            status="upcoming",
            origin="studio",
        ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events?filter=upcoming",
            wait_until="domcontentloaded",
        )
        # The series-linked event shows the series link, the standalone does not.
        series_link = page.locator('[data-testid="event-card-series-link"]')
        assert series_link.count() == 1
        assert "Listed Public Series" in series_link.first.inner_text()
        series_link.first.locator("a").click()
        page.wait_for_url(re.compile(r".*/events/groups/listed-public-series"))

        ctx.close()
