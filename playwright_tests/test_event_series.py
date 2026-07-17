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
import zoneinfo
from datetime import date, datetime, timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


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
        # Series creation is a rare action in the shared header overflow.
        page.get_by_label("More actions").click()
        new_series = page.locator('[data-testid="event-series-new-button"]')
        assert new_series.is_visible()
        new_series.click()
        page.wait_for_url(re.compile(r".*/studio/event-series/new$"))

        start = _next_weekday(2)  # Wednesday
        page.fill('input[name="name"]', "Spring Workshop Series")
        page.fill('input[name="start_date"]', start.strftime("%d/%m/%Y"))
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="duration_hours"]', "1.5")
        page.fill('input[name="occurrences"]', "6")
        # Issue #665: timezone is now a <select> rendered by the shared
        # studio/_partials/datetime_picker.html, not a free-text input.
        page.select_option('select[name="timezone"]', "Europe/Berlin")

        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_url(re.compile(r".*/studio/event-series/\d+/$"))

        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 6

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
        base_dt = datetime.combine(
            date.today() + timedelta(days=14),
            datetime(2026, 1, 1, 18, 0).time(),
        )
        for i in range(1, 7):
            ev = Event(
                title=f"Spring Workshop Series — Session {i}",
                slug=f"sws2-session-{i}",
                start_datetime=base_dt + timedelta(days=7 * (i - 1)),
                end_datetime=base_dt + timedelta(days=7 * (i - 1), hours=1, minutes=30),
                status="draft",
                origin="studio",
                # Issue #665: pin timezone='UTC' so the stored UTC instant
                # round-trips through the picker unchanged. Without this
                # the Event model default ('Europe/Berlin') would re-
                # interpret 18:00 wall-clock as Berlin, and submitting
                # 19:00 would store 17:00 UTC (CEST −2h).
                timezone="UTC",
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
        # Issue #860: link-less event — accept the "no meeting link" confirm.
        page.on("dialog", lambda d: d.accept())
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{target.pk}/edit$"))

        target.refresh_from_db()
        assert target.title == "Spring Workshop — Special Session"
        assert "indoors" in target.description
        assert target.start_datetime.hour == 19

        # Studio events list: studio-origin rows have no GitHub marker.
        page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
        row = page.locator(
            f'tr:has(a[href="/studio/events/{target.pk}/edit"])'
        ).first
        assert row.is_visible()
        assert row.locator('[data-testid="origin-github-icon"]').count() == 0

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
        # Operational fields (status, platform) remain editable.
        assert not page.locator('select[name="status"]').first.is_disabled()
        assert not page.locator('select[name="platform"]').first.is_disabled()
        # Capacity removed (#984): no Max Participants input exists.
        assert page.locator('input[name="max_participants"]').count() == 0

        # GitHub marker on the list.
        page.goto(f"{django_server}/studio/events/", wait_until="domcontentloaded")
        assert page.locator(
            f'tr:has(a[href="/studio/events/{gh.pk}/edit"]) [data-testid="origin-github-icon"]'
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
        page.locator('[data-testid="sticky-save-action"]').click()
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
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_load_state("domcontentloaded")
        assert "/studio/event-series/new" in page.url
        assert page.locator('[data-testid="error-occurrences"]').is_visible()
        assert EventSeries.objects.count() == 0
        assert Event.objects.count() == 0

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 10 (issue #856): Required Level is a named-tier dropdown
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
class TestScenario10RequiredLevelDropdown:
    def test_named_dropdown_and_main_gating(self, django_server, browser):
        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg10@test.com")
        ctx = _auth_context(browser, "staff-eg10@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )

        # The access field is a <select>, not a bare number input.
        level = page.locator('select[name="required_level"]')
        assert level.count() == 1
        assert page.locator('input[type="number"][name="required_level"]').count() == 0

        # Options read as tier names, not opaque integers.
        option_texts = level.locator("option").all_inner_texts()
        assert option_texts == ["Free (0)", "Basic (10)", "Main (20)", "Premium (30)"]

        # Gate the whole series to Main (20).
        start = _next_weekday(2)  # Wednesday
        page.fill('input[name="name"]', "Main-Gated Series")
        page.fill('input[name="start_date"]', start.strftime("%d/%m/%Y"))
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="duration_hours"]', "1")
        page.fill('input[name="occurrences"]', "3")
        page.select_option('select[name="timezone"]', "Europe/Berlin")
        page.select_option('select[name="required_level"]', "20")
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_url(re.compile(r".*/studio/event-series/\d+/$"))

        series = EventSeries.objects.get(slug="main-gated-series")
        events = list(series.events.all())
        assert len(events) == 3
        assert all(ev.required_level == 20 for ev in events)

        # The generated event's editor shows Main selected.
        target = events[0]
        page.goto(
            f"{django_server}/studio/events/{target.pk}/edit",
            wait_until="domcontentloaded",
        )
        editor_level = page.locator('select[name="required_level"]')
        assert editor_level.input_value() == "20"

        ctx.close()

    def test_default_selection_is_free(self, django_server, browser):
        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg10b@test.com")
        ctx = _auth_context(browser, "staff-eg10b@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )
        # Default selection is Free (0) before any change.
        assert page.locator('select[name="required_level"]').input_value() == "0"

        # Submit without touching Required Level -> open events.
        start = _next_weekday(2)
        page.fill('input[name="name"]', "Default Free Series")
        page.fill('input[name="start_date"]', start.strftime("%d/%m/%Y"))
        page.fill('input[name="start_time"]', "18:00")
        page.fill('input[name="duration_hours"]', "1")
        page.fill('input[name="occurrences"]', "2")
        page.select_option('select[name="timezone"]', "Europe/Berlin")
        page.locator('[data-testid="sticky-save-action"]').click()
        page.wait_for_url(re.compile(r".*/studio/event-series/\d+/$"))

        series = EventSeries.objects.get(slug="default-free-series")
        assert all(ev.required_level == 0 for ev in series.events.all())

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
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        assert page.locator('[data-testid="series-name"]').inner_text() == "Public Series"
        rows = page.locator('[data-testid="series-event"]')
        assert rows.count() == 6

        # Click the first event — lands on the event detail page.
        # Issue #673: canonical URL is ``/events/<id>/public-series-session-1``.
        page.locator('[data-testid="series-event-link"]').first.click()
        page.wait_for_url(
            re.compile(r".*/events/\d+/public-series-session-1$"),
        )
        assert page.locator("h1").first.is_visible()

        ctx.close()


# ---------------------------------------------------------------------------
# Issue #957: a rebuilt series with shuffled series_position still reads in
# calendar order on the public series page; drafts + cancelled stay hidden.
# ---------------------------------------------------------------------------


def _seed_shuffled_office_hours():
    """Seed a publicly-visible series whose stored ``series_position`` is
    shuffled relative to ``start_datetime`` (one earlier-dated session carries
    a later position), mimicking a two-batch rebuild. Returns the slug.
    """
    from django.db import connection
    from django.utils import timezone

    from events.models import Event, EventSeries

    series = EventSeries(
        name="Rebuilt Office Hours",
        slug="rebuilt-office-hours",
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    now = timezone.now()
    # (days_out, series_position) — position deliberately disagrees with date.
    plan = [
        (10, 4),  # earliest date, highest position
        (14, 2),
        (17, 3),
        (22, 1),  # latest date, lowest position
    ]
    for days, position in plan:
        Event(
            title=f"Rebuilt Office Hours — Session {position}",
            slug=f"rebuilt-oh-session-{position}",
            start_datetime=now + timedelta(days=days),
            status="upcoming",
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=position,
        ).save()
    connection.close()
    return series


def _seed_scrambled_studio_series():
    """Seed the Studio detail regression case from issue #1041."""
    from django.db import connection
    from django.utils import timezone

    from events.models import Event, EventSeries

    series = EventSeries(
        name="Scrambled Studio Series",
        slug="scrambled-studio-series",
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    base_date = date.today() + timedelta(days=10)
    plan = [
        ("Alpha kickoff", "alpha-kickoff-pw", 1, base_date, "draft"),
        ("Bravo lab", "bravo-lab-pw", 7, base_date + timedelta(days=1), "upcoming"),
        ("Charlie clinic", "charlie-clinic-pw", 3, base_date + timedelta(days=2), "draft"),
        ("Delta review", "delta-review-pw", 4, base_date + timedelta(days=3), "draft"),
        ("Echo demo", "echo-demo-pw", 8, base_date + timedelta(days=4), "draft"),
        ("Foxtrot close", "foxtrot-close-pw", 9, base_date + timedelta(days=5), "draft"),
    ]
    events = []
    for title, slug, position, start_date, status in plan:
        starts_at = datetime.combine(start_date, datetime(2026, 1, 1, 18, 0).time())
        event = Event(
            title=title,
            slug=slug,
            start_datetime=timezone.make_aware(
                starts_at,
                zoneinfo.ZoneInfo("UTC"),
            ),
            status=status,
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=position,
        )
        event.save()
        events.append(event)
    connection.close()
    return series, events


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario957ChronologicalOrder:
    def test_sessions_read_top_to_bottom_in_date_order(
        self, django_server, browser
    ):
        _reset_event_state()
        series = _seed_shuffled_office_hours()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        rows = page.locator('[data-testid="series-event"]')
        assert rows.count() == 4

        # Read the rendered date string of each row top-to-bottom. Each row's
        # date must be on or after the row above it (calendar order).
        date_texts = page.locator(
            '[data-testid="series-event-date"]'
        ).all_inner_texts()
        parsed = [
            datetime.strptime(t.split(" · ")[0].strip(), "%A, %b %d, %Y")
            for t in date_texts
        ]
        assert parsed == sorted(parsed), (
            "Sessions are not in calendar order: " + str(date_texts)
        )

        # The first row is the earliest-dated session (position 4) and the
        # last is the latest-dated (position 1), proving the sort ignores
        # series_position.
        first_slug = rows.first.get_attribute("data-event-slug")
        last_slug = rows.last.get_attribute("data-event-slug")
        assert first_slug == "rebuilt-oh-session-4"
        assert last_slug == "rebuilt-oh-session-1"

        ctx.close()

    def test_draft_and_cancelled_stay_out_of_public_schedule(
        self, django_server, browser
    ):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event

        _reset_event_state()
        series = _seed_shuffled_office_hours()
        now = timezone.now()
        Event(
            title="Rebuilt Office Hours — Draft",
            slug="rebuilt-oh-draft",
            start_datetime=now + timedelta(days=12),
            status="draft",
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=9,
        ).save()
        Event(
            title="Rebuilt Office Hours — Cancelled",
            slug="rebuilt-oh-cancelled",
            start_datetime=now + timedelta(days=13),
            status="cancelled",
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=8,
        ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        # Only the four published upcoming sessions are listed, still in order.
        rows = page.locator('[data-testid="series-event"]')
        assert rows.count() == 4
        body = page.locator("body").inner_text()
        assert "Rebuilt Office Hours — Draft" not in body
        assert "Rebuilt Office Hours — Cancelled" not in body

        date_texts = page.locator(
            '[data-testid="series-event-date"]'
        ).all_inner_texts()
        parsed = [
            datetime.strptime(t.split(" · ")[0].strip(), "%A, %b %d, %Y")
            for t in date_texts
        ]
        assert parsed == sorted(parsed)

        ctx.close()


# ---------------------------------------------------------------------------
# Issue #1041: Studio series detail hides internal occurrence numbers while
# preserving chronological table order and row actions.
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario1041StudioSeriesDetailNoInternalNumbers:
    def test_scrambled_positions_are_hidden_and_row_actions_still_work(
        self, django_server, browser
    ):
        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-eg1041@test.com")
        series, events = _seed_scrambled_studio_series()
        ctx = _auth_context(browser, "staff-eg1041@test.com")
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())

        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )

        table = page.locator("table").first
        assert table.locator("thead th").all_inner_texts() == [
            "TITLE",
            "VISIBILITY",
            "ACCESS",
            "ZOOM",
            "START",
            "REGISTRATIONS",
            "ACTIONS",
        ]
        assert page.locator('[data-testid="series-position"]').count() == 0

        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 6
        titles = [
            rows.nth(i).locator('td[data-label="Title"]').inner_text()
            for i in range(rows.count())
        ]
        assert titles == [
            "Alpha kickoff",
            "Bravo lab",
            "Charlie clinic",
            "Delta review",
            "Echo demo",
            "Foxtrot close",
        ]
        for i, position in enumerate([1, 7, 3, 4, 8, 9]):
            cells = rows.nth(i).locator("td").all_inner_texts()
            assert len(cells) == 7
            assert cells[0] == titles[i]
            assert not rows.nth(i).inner_text().startswith(f"{position}\n")

        rows.nth(0).locator('[data-testid="member-event-publish"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series.pk}/$"))
        assert rows.nth(0).locator('[data-testid="event-publish-state"]').inner_text() == "Published"
        assert page.locator('[data-testid="series-position"]').count() == 0

        rows.nth(1).locator('[data-testid="member-event-unpublish"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series.pk}/$"))
        assert rows.nth(1).locator('[data-testid="event-publish-state"]').inner_text() == "Not published"
        assert page.locator('[data-testid="series-position"]').count() == 0

        rows.nth(1).locator('[data-testid="member-event-edit"]').click()
        page.wait_for_url(re.compile(rf".*/studio/events/{events[1].pk}/edit$"))
        page.fill('input[name="title"]', "Bravo lab updated")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{events[1].pk}/edit$"))
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        assert rows.nth(1).locator('td[data-label="Title"]').inner_text() == "Bravo lab updated"
        assert page.locator('[data-testid="series-position"]').count() == 0

        future = date.today() + timedelta(days=35)
        page.fill('input[name="title"]', "Golf followup")
        page.fill('input[name="start_date"]', future.strftime("%d/%m/%Y"))
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series.pk}/$"))

        new_event = Event.objects.get(title="Golf followup")
        assert new_event.series_position == 10
        assert page.locator('[data-testid="series-position"]').count() == 0
        assert page.locator('[data-testid="event-series-member-row"]').last.locator(
            'td[data-label="Title"]'
        ).inner_text() == "Golf followup"

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
        assert page.locator('[data-testid="event-series-card"]').count() == 0
        assert page.locator('[data-testid="series-card-see-more"]').count() == 0
        assert "Standalone Event" in page.locator("body").inner_text()
        series_link = page.locator('[data-testid="event-card-series-link"]')
        assert series_link.count() == 1
        assert "Listed Public Series" in series_link.first.inner_text()
        series_link.first.locator("a").click()
        page.wait_for_url(
            re.compile(r".*/events/series/\d+/listed-public-series$")
        )

        ctx.close()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario1028UpcomingListingSeriesCollapse:
    def test_listing_shows_next_occurrence_with_see_more(
        self, django_server, browser
    ):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        series = EventSeries(
            name="LLM Zoomcamp 2026 office hours",
            slug="llm-zoomcamp-2026-office-hours",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
            timezone="UTC",
        )
        series.save()
        for i in range(4):
            Event(
                title=f"LLM Zoomcamp Office Hours Session {i + 1}",
                slug=f"llm-zoomcamp-office-hours-{i + 1}",
                start_datetime=timezone.now() + timedelta(days=i + 1),
                status="upcoming",
                origin="studio",
                timezone="UTC",
                event_series=series,
                series_position=i + 1,
            ).save()
        Event(
            title="Standalone Future Clinic",
            slug="standalone-future-clinic",
            start_datetime=timezone.now() + timedelta(days=10),
            status="upcoming",
            origin="studio",
            timezone="UTC",
        ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events?filter=upcoming",
            wait_until="domcontentloaded",
        )

        card = page.locator(
            '[data-testid="event-series-card"]'
            '[data-series-slug="llm-zoomcamp-2026-office-hours"]'
        )
        assert card.count() == 1
        text = card.inner_text()
        assert "Event series" in text
        assert "LLM Zoomcamp Office Hours Session 1" in text
        assert "LLM Zoomcamp Office Hours Session 2" not in text
        assert "LLM Zoomcamp Office Hours Session 4" not in text
        assert card.locator('[data-testid="series-card-date"]').count() == 1
        assert card.locator('[data-testid="series-card-see-more"]').count() == 1
        assert card.locator('[data-testid="series-card-cta"]').count() == 0
        assert "View series" not in text
        body = page.locator("body").inner_text()
        assert "LLM Zoomcamp Office Hours Session 2" not in body
        assert "LLM Zoomcamp Office Hours Session 4" not in body
        assert "Standalone Future Clinic" in body

        card.locator('[data-testid="series-card-see-more"]').click()
        page.wait_for_url(
            re.compile(r".*/events/series/\d+/llm-zoomcamp-2026-office-hours$")
        )
        rows = page.locator('[data-testid="series-event"]')
        assert rows.count() == 4
        series_text = page.locator("body").inner_text()
        assert "LLM Zoomcamp Office Hours Session 1" in series_text
        assert "LLM Zoomcamp Office Hours Session 4" in series_text

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 11 (issue #893): Staff sees a derived status badge per occurrence
# on the series detail page
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario11StatusBadgePerOccurrence:
    def test_draft_vs_upcoming_badge_and_flip(self, django_server, browser):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg11@test.com")
        series = EventSeries(
            name="Status Badge Series",
            slug="status-badge-series",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        draft = Event(
            title="Draft Occurrence",
            slug="status-badge-draft",
            start_datetime=timezone.now() + timedelta(days=7),
            status="draft",
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=1,
        )
        draft.save()
        upcoming = Event(
            title="Upcoming Occurrence",
            slug="status-badge-upcoming",
            start_datetime=timezone.now() + timedelta(days=14),
            status="upcoming",
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=2,
        )
        upcoming.save()
        connection.close()

        ctx = _auth_context(browser, "staff-eg11@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )

        # Issue #858: the bare "Draft" badge is replaced by viewer-friendly
        # publish wording. The draft occurrence reads "Not published".
        draft_state = page.locator(
            f'tr:has(a[href="/studio/events/{draft.pk}/edit"]) '
            '[data-testid="event-publish-state"]'
        ).first
        assert draft_state.get_attribute("data-status") == "draft"
        assert "Not published" in draft_state.inner_text()
        assert "Draft" not in draft_state.inner_text()

        # The upcoming occurrence reads "Published".
        upcoming_state = page.locator(
            f'tr:has(a[href="/studio/events/{upcoming.pk}/edit"]) '
            '[data-testid="event-publish-state"]'
        ).first
        assert upcoming_state.get_attribute("data-status") == "upcoming"
        assert "Published" in upcoming_state.inner_text()

        # Flip the draft occurrence to published (upcoming) via its editor.
        page.goto(
            f"{django_server}/studio/events/{draft.pk}/edit",
            wait_until="domcontentloaded",
        )
        # Issue #860: this occurrence has no Zoom meeting / URL, so Save fires
        # the "no meeting link" confirm — accept it to let the save through.
        page.on("dialog", lambda d: d.accept())
        page.select_option('select[name="status"]', "upcoming")
        page.locator("button:has-text('Save Changes')").first.click()
        page.wait_for_url(re.compile(rf".*/studio/events/{draft.pk}/edit$"))

        # Back on the series page, that row no longer reads "Not published".
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        flipped_state = page.locator(
            f'tr:has(a[href="/studio/events/{draft.pk}/edit"]) '
            '[data-testid="event-publish-state"]'
        ).first
        assert flipped_state.get_attribute("data-status") != "draft"
        assert "Not published" not in flipped_state.inner_text()

        ctx.close()


# ---------------------------------------------------------------------------
# Scenario 10: Cancelled occurrences are hidden from visitors but visible
# to staff (issue #863)
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario10CancelledHiddenFromPublic:
    def test_cancelled_hidden_for_visitor_visible_for_staff(
        self, django_server, browser
    ):
        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg10@test.com")
        series = EventSeries(
            name="Cancellation Series",
            slug="cancellation-series",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        # 6 live (upcoming) occurrences + 3 cancelled — the live symptom from
        # issue #863 (a series with 9 occurrences, 3 cancelled).
        for i in range(1, 7):
            Event(
                title=f"Live Occurrence {i}",
                slug=f"cancellation-series-live-{i}",
                start_datetime=timezone.now() + timedelta(days=7 * i),
                status="upcoming",
                origin="studio",
                event_series=series,
                series_position=i,
            ).save()
        for i in range(1, 4):
            Event(
                title=f"Cancelled Occurrence {i}",
                slug=f"cancellation-series-cancelled-{i}",
                start_datetime=timezone.now() + timedelta(days=7 * (6 + i)),
                status="cancelled",
                origin="studio",
                event_series=series,
                series_position=6 + i,
            ).save()
        connection.close()

        # Anonymous visitor: only the 6 live occurrences are listed, no
        # cancelled occurrence and no "Cancelled" label.
        anon = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon.new_page()
        anon_page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        rows = anon_page.locator('[data-testid="series-event"]')
        assert rows.count() == 6
        body = anon_page.locator("body").inner_text()
        assert "Cancelled Occurrence" not in body
        assert "Cancelled" not in body
        anon.close()

        # Staff: all 9 occurrences (including the 3 cancelled) are visible so
        # they can be managed.
        staff_ctx = _auth_context(browser, "staff-eg10@test.com")
        staff_page = staff_ctx.new_page()
        staff_page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        staff_rows = staff_page.locator('[data-testid="series-event"]')
        assert staff_rows.count() == 9
        staff_body = staff_page.locator("body").inner_text()
        assert "Cancelled Occurrence 1" in staff_body
        staff_ctx.close()


# ---------------------------------------------------------------------------
# Issue #956: the series page shows a per-session tier badge so an
# anonymous visitor can see the Free-vs-paid split across sibling sessions
# without signing in. Free rows read "Free" (no lock); gated rows read the
# public tier label with a lock icon.
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario956PerSessionTierBadge:
    def test_anon_distinguishes_free_from_paid_sessions(
        self, django_server, browser
    ):
        from django.db import connection
        from django.utils import timezone

        from content.access import LEVEL_MAIN, LEVEL_OPEN
        from events.models import Event, EventSeries

        _reset_event_state()
        series = EventSeries(
            name="Tier Split Series",
            slug="tier-split-series",
            start_time=datetime(2026, 1, 1, 18, 0).time(),
        )
        series.save()
        # Two upcoming sessions: one Free, one Main-gated. The whole point
        # of the badge is the visible contrast between them.
        Event(
            title="Free Session",
            slug="tier-split-free",
            start_datetime=timezone.now() + timedelta(days=7),
            status="upcoming",
            origin="studio",
            required_level=LEVEL_OPEN,
            event_series=series,
            series_position=1,
        ).save()
        Event(
            title="Main Session",
            slug="tier-split-main",
            start_datetime=timezone.now() + timedelta(days=14),
            status="upcoming",
            origin="studio",
            required_level=LEVEL_MAIN,
            event_series=series,
            series_position=2,
        ).save()
        connection.close()

        anon = browser.new_context(viewport={"width": 1280, "height": 720})
        page = anon.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        badges = page.locator('[data-testid="series-event-tier"]')
        assert badges.count() == 2
        assert page.locator(
            '[data-testid="series-event-tier"][data-component="member-badge"]'
        ).count() == 2

        # The Free badge (required-level 0) reads "Free" and has no lock.
        # lucide.createIcons() swaps the <i data-lucide="lock"> for an
        # <svg class="lucide lucide-lock">, so we assert on the rendered
        # SVG class rather than the pre-render data-lucide attribute.
        free_badge = page.locator(
            '[data-testid="series-event-tier"][data-required-level="0"]'
        )
        assert free_badge.count() == 1
        assert "Free" in free_badge.inner_text()
        assert free_badge.locator("svg.lucide-lock").count() == 0

        # The Main badge (required-level 20) reads the public label + lock.
        main_badge = page.locator(
            '[data-testid="series-event-tier"][data-required-level="20"]'
        )
        assert main_badge.count() == 1
        assert "Main or above" in main_badge.inner_text()
        main_badge.locator("svg.lucide-lock").first.wait_for()
        assert main_badge.locator("svg.lucide-lock").count() == 1

        anon.close()


# ---------------------------------------------------------------------------
# Issue #877: the public series cadence label is derived from real
# occurrences, so it stays honest once the schedule drifts (irregular) and is
# unchanged for a genuinely-weekly series.
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario877ScheduleLabel:
    def test_irregular_series_shows_honest_summary(
        self, django_server, browser
    ):
        from django.db import connection

        from events.models import Event, EventSeries

        _reset_event_state()
        # Series first occurrence is a Wednesday at 18:00 Europe/Berlin, but
        # the real schedule drifts across mixed weekdays over several weeks.
        series = EventSeries(
            name="Drifted Series",
            slug="drifted-series",
            day_of_week=2,  # Wednesday
            start_time=datetime(2026, 1, 1, 18, 0).time(),
            timezone="Europe/Berlin",
        )
        series.save()
        schedule = [
            datetime(2026, 6, 15, 18, 0),  # Monday
            datetime(2026, 6, 24, 18, 0),  # Wednesday
            datetime(2026, 6, 29, 18, 0),  # Monday
            datetime(2026, 7, 6, 18, 0),   # Monday
            datetime(2026, 7, 21, 18, 0),  # Tuesday
            datetime(2026, 8, 3, 18, 0),   # Monday
        ]
        for i, dt in enumerate(schedule, start=1):
            aware = dt.replace(tzinfo=zoneinfo.ZoneInfo("Europe/Berlin"))
            Event(
                title=f"Drift Session {i}",
                slug=f"drifted-series-session-{i}",
                start_datetime=aware,
                timezone="Europe/Berlin",
                status="upcoming",
                origin="studio",
                event_series=series,
                series_position=i,
            ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        cadence = page.locator('[data-testid="series-cadence"]')
        label = cadence.inner_text()
        # No false weekly-cadence claim.
        assert "Weekly on" not in label
        assert "Monday" not in label
        # Honest neutral summary naming the session count and date range.
        assert "6 sessions" in label
        assert "–" in label
        # The stated count matches the number of occurrence cards.
        rows = page.locator('[data-testid="series-event"]')
        assert rows.count() == 6

        ctx.close()

    def test_weekly_series_keeps_familiar_label(
        self, django_server, browser
    ):
        from django.db import connection

        from events.models import Event, EventSeries

        _reset_event_state()
        series = EventSeries(
            name="Weekly Series",
            slug="weekly-series",
            day_of_week=2,  # Wednesday
            start_time=datetime(2026, 1, 1, 18, 0).time(),
            timezone="Europe/Berlin",
        )
        series.save()
        base = datetime(2026, 6, 17, 18, 0)  # Wednesday
        for i in range(3):
            aware = (base + timedelta(weeks=i)).replace(
                tzinfo=zoneinfo.ZoneInfo("Europe/Berlin"))
            Event(
                title=f"Weekly Session {i + 1}",
                slug=f"weekly-series-session-{i + 1}",
                start_datetime=aware,
                timezone="Europe/Berlin",
                status="upcoming",
                origin="studio",
                event_series=series,
                series_position=i + 1,
            ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        label = page.locator(
            '[data-testid="series-cadence"]').inner_text().strip()
        assert label == "Weekly on Wednesday at 18:00 Europe/Berlin"
        # No neutral "N sessions" summary in place of the weekly label.
        assert "sessions" not in label

        ctx.close()

    def test_staff_preview_draft_only_series_has_no_blank_cadence_line(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_staff_user("staff-eg877@test.com")
        # All-draft series: zero publicly-visible occurrences, so the label is
        # empty and the cadence paragraph must be omitted entirely.
        series_pk = _seed_office_hours_series()
        series_url = _series_public_url(series_pk)

        ctx = _auth_context(browser, "staff-eg877@test.com")
        page = ctx.new_page()
        resp = page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        )
        assert resp.status == 200
        assert page.locator('[data-testid="series-name"]').is_visible()
        # The cadence paragraph is absent rather than rendered blank.
        assert page.locator('[data-testid="series-cadence"]').count() == 0

        ctx.close()


# ---------------------------------------------------------------------------
# Issue #947: the /events grouped series card cadence is the honest
# schedule_label too, matching the series page (the false "Weekly on …" claim
# is gone for irregular series everywhere).
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario947ListingCardCadence:
    def test_listing_card_shows_honest_cadence_matching_series_page(
        self, django_server, browser
    ):
        from django.db import connection

        from events.models import Event, EventSeries

        _reset_event_state()
        # First occurrence is a Wednesday at 18:00 Europe/Berlin, but the real
        # schedule drifts across weekdays — an irregular series with 3 upcoming
        # live occurrences, so /events renders it as one grouped series card.
        series = EventSeries(
            name="Listing Drifted Series",
            slug="listing-drifted-series",
            day_of_week=2,  # Wednesday (claimed)
            start_time=datetime(2026, 1, 1, 18, 0).time(),
            timezone="Europe/Berlin",
        )
        series.save()
        # Anchor occurrences to genuinely-future dates relative to the run so a
        # hardcoded calendar date can't silently flip a session into the past
        # and drop the "N upcoming sessions" suffix (issue #947 regression).
        from django.utils import timezone as dj_timezone

        today = dj_timezone.now().astimezone(
            zoneinfo.ZoneInfo("Europe/Berlin")
        ).date()
        schedule = [
            datetime(d.year, d.month, d.day, 18, 0)
            for d in (
                today + timedelta(days=3),
                today + timedelta(days=12),
                today + timedelta(days=17),
            )
        ]
        for i, dt in enumerate(schedule, start=1):
            aware = dt.replace(tzinfo=zoneinfo.ZoneInfo("Europe/Berlin"))
            Event(
                title=f"Listing Drift Session {i}",
                slug=f"listing-drifted-series-session-{i}",
                start_datetime=aware,
                timezone="Europe/Berlin",
                status="upcoming",
                origin="studio",
                event_series=series,
                series_position=i,
            ).save()
        connection.close()

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(f"{django_server}/events", wait_until="domcontentloaded")

        card = page.locator(
            '[data-testid="event-series-card"]'
            '[data-series-slug="listing-drifted-series"]'
        )
        assert card.count() == 1
        meta = card.locator('[data-testid="series-card-meta"]').inner_text()
        # No false weekly-cadence claim; honest session summary + suffix.
        assert "Weekly on" not in meta
        assert "3 sessions" in meta
        assert "3 upcoming session" in meta

        # Click through to the series page; its header agrees with the card.
        card.locator('[data-testid="series-card-link"]').click()
        page.wait_for_url(
            re.compile(r".*/events/series/\d+/listing-drifted-series$")
        )
        page_label = page.locator(
            '[data-testid="series-cadence"]'
        ).inner_text().strip()
        assert "Weekly on" not in page_label
        assert "3 sessions" in page_label

        ctx.close()


# ---------------------------------------------------------------------------
# Issue #854 Part A: irregular schedules — per-occurrence date + time + title
# ---------------------------------------------------------------------------


def _seed_office_hours_series():
    """Create an 'Office Hours' series with 3 empty-description sessions."""
    from django.db import connection
    from django.utils import timezone

    from events.models import Event, EventSeries

    series = EventSeries(
        name="Office Hours",
        slug="office-hours",
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
        description="",
    )
    series.save()
    for i in range(1, 4):
        Event(
            title=f"Office Hours — Session {i}",
            slug=f"office-hours-session-{i}",
            description="",
            start_datetime=timezone.now() + timedelta(days=7 * i),
            origin="studio",
            timezone="UTC",
            event_series=series,
            series_position=i,
        ).save()
    pk = series.pk
    connection.close()
    return pk


def _series_public_url(series_pk):
    from events.models import EventSeries

    return EventSeries.objects.get(pk=series_pk).get_absolute_url()


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario854IrregularSchedule:
    def test_add_occurrence_with_custom_time_and_weekday(
        self, django_server, browser
    ):
        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg854a@test.com")
        series_pk = _seed_office_hours_series()

        ctx = _auth_context(browser, "staff-eg854a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        # First irregular occurrence: two weeks out, 20:00.
        d1 = date.today() + timedelta(days=14)
        page.fill('input[name="start_date"]', d1.strftime("%d/%m/%Y"))
        page.fill('[data-testid="dtp-add-time"]', "20:00")
        page.select_option('[data-testid="dtp-add-tz"]', "UTC")
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series_pk}/$"))

        series = EventSeries.objects.get(pk=series_pk)
        occ1 = series.events.order_by("-series_position").first()
        assert occ1.series_position == 4
        # 20:00, not the series default 18:00.
        assert occ1.start_datetime.hour == 20

        # Second irregular occurrence on a different day/time: 09:30.
        d2 = date.today() + timedelta(days=20)
        page.fill('input[name="start_date"]', d2.strftime("%d/%m/%Y"))
        page.fill('[data-testid="dtp-add-time"]', "09:30")
        page.select_option('[data-testid="dtp-add-tz"]', "UTC")
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series_pk}/$"))

        series = EventSeries.objects.get(pk=series_pk)
        occ2 = series.events.order_by("-series_position").first()
        assert occ2.series_position == 5
        assert occ2.start_datetime.hour == 9
        assert occ2.start_datetime.minute == 30

        # Both irregular occurrences are listed (5 total rows now).
        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 5

        ctx.close()

    def test_custom_title_drives_slug(self, django_server, browser):
        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg854b@test.com")
        series_pk = _seed_office_hours_series()

        ctx = _auth_context(browser, "staff-eg854b@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        d1 = date.today() + timedelta(days=14)
        page.fill('[data-testid="add-occurrence-title"]', "Special Guest AMA")
        page.fill('input[name="start_date"]', d1.strftime("%d/%m/%Y"))
        page.select_option('[data-testid="dtp-add-tz"]', "UTC")
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series_pk}/$"))

        # The new row shows the custom title, not "— Session N".
        assert page.get_by_text("Special Guest AMA").first.is_visible()

        series = EventSeries.objects.get(pk=series_pk)
        new_event = series.events.order_by("-series_position").first()
        assert new_event.title == "Special Guest AMA"
        assert new_event.slug == "special-guest-ama"

        ctx.close()


# ---------------------------------------------------------------------------
# Issue #854 Part B: opt-in parent->child propagation
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario854Propagation:
    def test_propagate_description_to_children(self, django_server, browser):
        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg854c@test.com")
        series_pk = _seed_office_hours_series()

        ctx = _auth_context(browser, "staff-eg854c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        page.fill('textarea[name="description"]', "Bring your questions.")
        page.check('[data-testid="event-series-propagate"]')
        page.locator('[data-testid="event-series-metadata-save"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series_pk}/$"))

        # Success message reports the count.
        msg = page.locator('[data-testid="messages-region"]')
        assert "Updated 3 events" in msg.inner_text()

        series = EventSeries.objects.get(pk=series_pk)
        for child in series.events.all():
            assert child.description == "Bring your questions."

        ctx.close()

    def test_no_propagate_preserves_manual_child_edits(
        self, django_server, browser
    ):
        from django.db import connection

        from events.models import Event, EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg854d@test.com")
        series_pk = _seed_office_hours_series()
        # Hand-edit one child's description.
        child = Event.objects.filter(
            event_series_id=series_pk, series_position=1,
        ).first()
        child_pk = child.pk
        child.description = "Custom note"
        child.save()
        connection.close()

        ctx = _auth_context(browser, "staff-eg854d@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        page.fill('textarea[name="description"]', "New series blurb.")
        # Leave the propagate checkbox UNCHECKED.
        page.locator('[data-testid="event-series-metadata-save"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series_pk}/$"))

        series = EventSeries.objects.get(pk=series_pk)
        assert series.description == "New series blurb."
        # The hand-edited child survived.
        child = Event.objects.get(pk=child_pk)
        assert child.description == "Custom note"

        ctx.close()

    def test_propagate_slug_rename_to_children(self, django_server, browser):
        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-eg854e@test.com")
        series_pk = _seed_office_hours_series()

        ctx = _auth_context(browser, "staff-eg854e@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        page.fill('input[name="slug"]', "founder-office-hours")
        page.check('[data-testid="event-series-propagate"]')
        page.locator('[data-testid="event-series-metadata-save"]').click()
        page.wait_for_url(re.compile(rf".*/studio/event-series/{series_pk}/$"))

        msg = page.locator('[data-testid="messages-region"]')
        assert "Updated 3 events" in msg.inner_text()

        series = EventSeries.objects.get(pk=series_pk)
        for child in series.events.all():
            assert child.slug.startswith("founder-office-hours-session-")

        ctx.close()


# ---------------------------------------------------------------------------
# Issue #858: explicit Publish model + hide-empty-series + row layout
# ---------------------------------------------------------------------------


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestScenario858PublishAndVisibility:
    def test_publish_then_unpublish_drives_public_visibility(
        self, django_server, browser
    ):
        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-eg858a@test.com")
        # All occurrences start as draft (default status).
        series_pk = _seed_office_hours_series()
        series_url = _series_public_url(series_pk)

        ctx = _auth_context(browser, "staff-eg858a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        # Every occurrence reads "Not published" with a Publish control.
        states = page.locator('[data-testid="event-publish-state"]')
        assert states.count() == 3
        assert "Not published" in states.first.inner_text()
        publish_btns = page.locator('[data-testid="member-event-publish"]')
        assert publish_btns.count() == 3

        first_event = Event.objects.filter(
            event_series_id=series_pk
        ).order_by("series_position").first()
        publish_row = page.locator(
            f'tr:has(a[href="/studio/events/{first_event.pk}/edit"])'
        )
        publish_row.locator(
            '[data-testid="member-event-publish"]'
        ).click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series_pk}/$")
        )

        # That row now reads "Published" and offers Unpublish.
        publish_row = page.locator(
            f'tr:has(a[href="/studio/events/{first_event.pk}/edit"])'
        )
        assert "Published" in publish_row.locator(
            '[data-testid="event-publish-state"]'
        ).inner_text()
        assert publish_row.locator(
            '[data-testid="member-event-unpublish"]'
        ).is_visible()

        # The published occurrence is now visible to an anonymous visitor and
        # the series page loads (no 404).
        anon = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon.new_page()
        resp = anon_page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        )
        assert resp.status == 200
        events = anon_page.locator('[data-testid="series-event"]')
        assert events.count() == 1
        body = anon_page.locator("body").inner_text()
        assert "Draft" not in body
        anon.close()

        # Unpublish pulls it back out: the only published event is gone, so the
        # series 404s again for the anonymous visitor.
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )
        page.locator(
            f'tr:has(a[href="/studio/events/{first_event.pk}/edit"]) '
            '[data-testid="member-event-unpublish"]'
        ).click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series_pk}/$")
        )

        anon2 = browser.new_context(viewport={"width": 1280, "height": 720})
        anon2_page = anon2.new_page()
        resp2 = anon2_page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        )
        assert resp2.status == 404
        anon2.close()

        ctx.close()

    def test_empty_series_404s_for_anon_but_renders_for_staff(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_staff_user("staff-eg858b@test.com")
        # All-draft series: zero published occurrences.
        series_pk = _seed_office_hours_series()
        series_url = _series_public_url(series_pk)

        # Anonymous visitor 404s and never sees the placeholder or "Draft".
        anon = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon.new_page()
        resp = anon_page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        )
        assert resp.status == 404
        body = anon_page.locator("body").inner_text()
        assert "No published events" not in body
        assert "Draft" not in body
        anon.close()

        # Staff previews the same empty series (renders, no 404).
        ctx = _auth_context(browser, "staff-eg858b@test.com")
        page = ctx.new_page()
        resp_staff = page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        )
        assert resp_staff.status == 200
        assert page.locator('[data-testid="series-name"]').is_visible()
        ctx.close()

    def test_hide_populated_series_via_visibility_toggle(
        self, django_server, browser
    ):
        from events.models import Event

        _reset_event_state()
        _create_staff_user("staff-eg858c@test.com")
        series_pk = _seed_office_hours_series()
        series_url = _series_public_url(series_pk)
        # Publish one occurrence so the series has published events.
        Event.objects.filter(event_series_id=series_pk).update(
            status="upcoming"
        )

        # Visible by default: anonymous can load it.
        anon = browser.new_context(viewport={"width": 1280, "height": 720})
        anon_page = anon.new_page()
        assert anon_page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        ).status == 200
        anon.close()

        # Staff unchecks "Visible to the public" and saves metadata.
        ctx = _auth_context(browser, "staff-eg858c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )
        page.uncheck('[data-testid="event-series-is-active"]')
        page.locator('[data-testid="event-series-metadata-save"]').click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series_pk}/$")
        )

        # Anonymous now 404s even though published events exist.
        anon2 = browser.new_context(viewport={"width": 1280, "height": 720})
        anon2_page = anon2.new_page()
        assert anon2_page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        ).status == 404
        anon2.close()

        # Re-check and save: the page loads again for anonymous visitors.
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )
        page.check('[data-testid="event-series-is-active"]')
        page.locator('[data-testid="event-series-metadata-save"]').click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series_pk}/$")
        )

        anon3 = browser.new_context(viewport={"width": 1280, "height": 720})
        anon3_page = anon3.new_page()
        assert anon3_page.goto(
            f"{django_server}{series_url}",
            wait_until="domcontentloaded",
        ).status == 200
        anon3.close()
        ctx.close()

    def test_series_detail_is_single_column_rows(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_staff_user("staff-eg858d@test.com")
        series_pk = _seed_office_hours_series()

        ctx = _auth_context(browser, "staff-eg858d@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series_pk}/",
            wait_until="domcontentloaded",
        )

        # The four key sections stack vertically (rows): member events table,
        # add-occurrence form, metadata form, delete panel. Assert each is
        # full-width by comparing bounding-box left edges (same x start) and
        # strictly increasing top edges (one under the other).
        member_table = page.locator(
            '[data-testid="event-series-member-row"]'
        ).first
        add_form = page.locator('[data-testid="add-occurrence-form"]')
        meta_form = page.locator('[data-testid="event-series-metadata-form"]')
        delete_panel = page.locator(
            '[data-testid="event-series-delete-panel"]'
        )

        boxes = [
            member_table.bounding_box(),
            add_form.bounding_box(),
            meta_form.bounding_box(),
            delete_panel.bounding_box(),
        ]
        tops = [b["y"] for b in boxes]
        assert tops == sorted(tops), (
            "Sections are not stacked top-to-bottom: " + str(tops)
        )
        # The stacked content forms (add/metadata/delete) share a left edge,
        # confirming a single column rather than a side-by-side grid.
        lefts = [boxes[1]["x"], boxes[2]["x"], boxes[3]["x"]]
        assert max(lefts) - min(lefts) < 2, (
            "Sections are not left-aligned in one column: " + str(lefts)
        )

        # The metadata save still works after the layout change.
        page.fill('input[name="name"]', "Renamed Office Hours")
        page.locator('[data-testid="event-series-metadata-save"]').click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series_pk}/$")
        )
        assert "Renamed Office Hours" in page.locator(
            '[data-testid="event-series-name"]'
        ).inner_text()

        ctx.close()
