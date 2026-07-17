"""Playwright E2E for one-click "Create Zoom meetings for all events" (#859).

The Zoom API and the background-job enqueue are both patched in-process:

- ``studio.views.event_series.enqueue_create_series_zoom_meetings`` is patched
  to run the real worker synchronously (the E2E server has no Django-Q worker),
  so the button click -> POST -> job -> persisted summary -> reload flow is
  exercised end to end.
- ``events.tasks.create_series_zoom_meetings.create_meeting`` is patched so no
  live Zoom credentials are needed. A ``[HUMAN]`` criterion covers the real
  Zoom call.

Scenarios:
1. Staff creates Zoom meetings for every eligible session in one click.
2. Existing meetings are preserved, not recreated (2 created, 2 skipped).
3. One Zoom failure does not block the others (2 created, 1 failed + error).
4. Nothing to do when every occurrence already has a meeting (button disabled).
5. Custom-platform and past occurrences are skipped as ineligible.

Usage:
    uv run pytest playwright_tests/test_studio_series_create_zoom_859.py -v
"""

import os
from datetime import datetime, timedelta
from unittest.mock import patch

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

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _clear_state():
    from events.models import Event, EventSeries

    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_series(slug, name):
    from django.utils import timezone

    from events.models import EventSeries

    series = EventSeries(
        name=name,
        slug=slug,
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    return series, timezone.now()


def _add_event(series, slug, *, when, status="upcoming", platform="zoom",
               meeting_id=""):
    from events.models import Event

    return Event.objects.create(
        title=slug.replace("-", " ").title(),
        slug=slug,
        start_datetime=when,
        end_datetime=when + timedelta(hours=1),
        status=status,
        origin="studio",
        platform=platform,
        event_series=series,
        zoom_meeting_id=meeting_id,
        zoom_join_url=(f"https://zoom.us/j/{meeting_id}" if meeting_id else ""),
    )


# A counter-backed fake so every call returns a distinct meeting.
def _fake_create_meeting():
    counter = {"n": 0}

    def _inner(event):
        counter["n"] += 1
        mid = f"9000{counter['n']}"
        return {"meeting_id": mid, "join_url": f"https://zoom.us/j/{mid}"}

    return _inner


def _run_synchronously(series_id):
    """Drop-in for the enqueue helper: run the real worker inline."""
    from events.tasks.create_series_zoom_meetings import (
        create_series_zoom_meetings,
    )

    return create_series_zoom_meetings(series_id)


def _click_create_zoom(page, django_server, series_pk, create_meeting_fn):
    """Click the button with the worker + Zoom both patched in-process."""
    create_zoom = page.locator('[data-testid="series-create-zoom"]')
    if not create_zoom.is_visible():
        page.get_by_label("More actions").click()
    with patch(
        "studio.views.event_series.enqueue_create_series_zoom_meetings",
        side_effect=_run_synchronously,
    ), patch(
        "events.tasks.create_series_zoom_meetings.create_meeting",
        side_effect=create_meeting_fn,
    ):
        create_zoom.click()
        page.wait_for_url(
            f"**/studio/event-series/{series_pk}/",
            timeout=10000,
        )


@pytest.mark.django_db(transaction=True)
class TestScenarioCreateAll:
    def test_one_click_creates_for_every_eligible(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-859a@test.com")
        series, now = _make_series("series-859a", "Build Club A")
        for i in range(1, 5):
            _add_event(series, f"s859a-{i}", when=now + timedelta(days=7 * i))
        connection.close()

        ctx = _auth_context(browser, "admin-859a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        note = page.locator('[data-testid="series-zoom-eligible-note"]')
        assert "4 occurrences need a Zoom meeting" in note.inner_text()

        _click_create_zoom(page, django_server, series.pk, _fake_create_meeting())

        summary = page.locator('[data-testid="series-zoom-summary-counts"]')
        summary.wait_for(state="visible", timeout=10000)
        text = summary.inner_text()
        assert "Created 4" in text
        assert "failed 0" in text

        # All four rows now show a Zoom indicator.
        yes = page.locator('[data-zoom="yes"]')
        assert yes.count() == 4
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioPreserveExisting:
    def test_existing_meetings_not_recreated(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-859b@test.com")
        series, now = _make_series("series-859b", "Build Club B")
        # Two without meetings, two with existing meetings.
        _add_event(series, "s859b-1", when=now + timedelta(days=7))
        _add_event(series, "s859b-2", when=now + timedelta(days=14))
        kept1 = _add_event(
            series, "s859b-3", when=now + timedelta(days=21),
            meeting_id="keep-111",
        )
        kept2 = _add_event(
            series, "s859b-4", when=now + timedelta(days=28),
            meeting_id="keep-222",
        )
        connection.close()

        ctx = _auth_context(browser, "admin-859b@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        note = page.locator('[data-testid="series-zoom-eligible-note"]')
        assert "2 occurrences need a Zoom meeting" in note.inner_text()

        _click_create_zoom(page, django_server, series.pk, _fake_create_meeting())

        summary = page.locator('[data-testid="series-zoom-summary-counts"]')
        summary.wait_for(state="visible", timeout=10000)
        text = summary.inner_text()
        assert "Created 2" in text
        assert "2 already had a meeting" in text

        # Pre-existing meeting ids unchanged.
        connection.close()
        kept1.refresh_from_db()
        kept2.refresh_from_db()
        assert kept1.zoom_meeting_id == "keep-111"
        assert kept2.zoom_meeting_id == "keep-222"
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioPartialFailure:
    def test_one_failure_does_not_block_others(self, django_server, browser):
        from integrations.services.zoom import ZoomAPIError

        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-859c@test.com")
        series, now = _make_series("series-859c", "Build Club C")
        for i in range(1, 4):
            _add_event(series, f"s859c-{i}", when=now + timedelta(days=7 * i))
        connection.close()

        # Fail on the second create call only.
        state = {"n": 0}

        def flaky(event):
            state["n"] += 1
            if state["n"] == 2:
                raise ZoomAPIError("Too Many Requests", status_code=429)
            mid = f"700{state['n']}"
            return {"meeting_id": mid, "join_url": f"https://zoom.us/j/{mid}"}

        ctx = _auth_context(browser, "admin-859c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )

        _click_create_zoom(page, django_server, series.pk, flaky)

        summary = page.locator('[data-testid="series-zoom-summary-counts"]')
        summary.wait_for(state="visible", timeout=10000)
        text = summary.inner_text()
        assert "Created 2" in text
        assert "failed 1" in text

        failures = page.locator('[data-testid="series-zoom-failures"]')
        assert "Too Many Requests" in failures.inner_text()
        # Two rows got meetings, one did not.
        assert page.locator('[data-zoom="yes"]').count() == 2
        assert page.locator('[data-zoom="no"]').count() == 1
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioNothingToDo:
    def test_button_disabled_when_all_have_meetings(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-859d@test.com")
        series, now = _make_series("series-859d", "Build Club D")
        for i in range(1, 3):
            _add_event(
                series, f"s859d-{i}", when=now + timedelta(days=7 * i),
                meeting_id=f"done-{i}",
            )
        connection.close()

        ctx = _auth_context(browser, "admin-859d@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        disabled = page.locator('[data-testid="series-create-zoom-disabled"]')
        assert disabled.is_visible()
        assert "All occurrences have Zoom meetings" in disabled.inner_text()
        assert page.locator('[data-testid="series-create-zoom"]').count() == 0
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioIneligibleSkipped:
    def test_past_and_custom_are_skipped(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-859e@test.com")
        series, now = _make_series("series-859e", "Build Club E")
        future = _add_event(series, "s859e-future", when=now + timedelta(days=7))
        past = _add_event(
            series, "s859e-past", when=now - timedelta(days=7),
            status="completed",
        )
        custom = _add_event(
            series, "s859e-custom", when=now + timedelta(days=14),
            platform="custom",
        )
        connection.close()

        ctx = _auth_context(browser, "admin-859e@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        note = page.locator('[data-testid="series-zoom-eligible-note"]')
        assert "1 occurrence need a Zoom meeting" in note.inner_text()

        _click_create_zoom(page, django_server, series.pk, _fake_create_meeting())

        summary = page.locator('[data-testid="series-zoom-summary-counts"]')
        summary.wait_for(state="visible", timeout=10000)
        text = summary.inner_text()
        assert "Created 1" in text

        connection.close()
        future.refresh_from_db()
        past.refresh_from_db()
        custom.refresh_from_db()
        assert future.zoom_meeting_id
        assert past.zoom_meeting_id == ""
        assert custom.zoom_meeting_id == ""
        ctx.close()
