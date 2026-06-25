"""Playwright E2E for series-aware event detail registration (issue #1077).

A viewer holding a standing ``SeriesRegistration`` for the series an
occurrence belongs to is shown as registered on that occurrence's detail
page, even without a per-occurrence ``EventRegistration`` row.

Scenarios:
1. Series-registered member sees the registered state + series heading.
2. Series registrant manages registration from the occurrence page.
3. Per-occurrence registrant keeps the single-event cancel flow.
4. Member registered for both series and occurrence sees per-occurrence cancel.
5. Member unregistered from one occurrence but still on the series stays
   effectively registered.
6. Standalone event (no series) is unaffected.
7. Anonymous visitor sees the normal registration card on a series occurrence.
8. Series registrant can join near start time.

Usage:
    uv run pytest playwright_tests/test_event_detail_series_registration_1077.py -v
"""

import os
from datetime import datetime, timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _reset_event_state():
    from django.db import connection

    from events.models import (
        Event,
        EventRegistration,
        EventSeries,
        SeriesRegistration,
    )

    SeriesRegistration.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_series(slug, name):
    from django.db import connection

    from events.models import EventSeries

    series = EventSeries(
        name=name,
        slug=slug,
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    connection.close()
    return series


def _make_occurrence(
    slug, title, *, series=None, offset_minutes=None, offset_days=7,
    required_level=0, status="upcoming", zoom_join_url="", position=1,
):
    from django.db import connection
    from django.utils import timezone

    from events.models import Event

    if offset_minutes is not None:
        start = timezone.now() + timedelta(minutes=offset_minutes)
    else:
        start = timezone.now() + timedelta(days=offset_days)
    event = Event(
        title=title,
        slug=slug,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        origin="studio",
        required_level=required_level,
        event_series=series,
        series_position=position if series else None,
        zoom_join_url=zoom_join_url,
    )
    event.save()
    connection.close()
    return event


def _series_register(email, series):
    from django.db import connection

    from accounts.models import User
    from events.models import SeriesRegistration

    user = User.objects.get(email=email)
    SeriesRegistration.objects.get_or_create(series=series, user=user)
    connection.close()


def _event_register(email, event):
    from django.db import connection

    from accounts.models import User
    from events.models import EventRegistration

    user = User.objects.get(email=email)
    EventRegistration.objects.get_or_create(event=event, user=user)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestSeriesRegisteredSeesRegistered:
    def test_series_registered_member_sees_registered_state(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_user("main-1077a@test.com", tier_slug="main")
        series = _make_series("series-1077a", "Series A")
        event = _make_occurrence(
            "series-1077a-occ", "Series A Session", series=series,
        )
        _series_register("main-1077a@test.com", series)

        ctx = _auth_context(browser, "main-1077a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        # Registered, not the register button.
        assert page.locator(
            '[data-testid="event-registered-confirmation"]'
        ).is_visible()
        assert page.locator("[data-event-register-button]").count() == 0
        # Series heading.
        assert (
            "You're registered for this series"
            in page.locator('[data-testid="event-registered-heading"]').inner_text()
        )
        # Add to calendar present for this occurrence.
        assert page.locator(
            '[data-testid="event-add-to-calendar"]'
        ).is_visible()
        # Manage series registration link points at the series page.
        link = page.locator(
            '[data-testid="event-manage-series-registration-link"]'
        )
        assert link.is_visible()
        assert series.get_absolute_url() in link.get_attribute("href")

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestSeriesRegistrantManagesFromOccurrence:
    def test_manage_link_navigates_to_series_page(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_user("main-1077b@test.com", tier_slug="main")
        series = _make_series("series-1077b", "Series B")
        event = _make_occurrence(
            "series-1077b-occ", "Series B Session", series=series,
        )
        _series_register("main-1077b@test.com", series)

        ctx = _auth_context(browser, "main-1077b@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        # No per-occurrence cancel button.
        assert page.locator("[data-event-unregister-button]").count() == 0

        page.locator(
            '[data-testid="event-manage-series-registration-link"]'
        ).click()
        page.wait_for_load_state("domcontentloaded")

        assert series.get_absolute_url() in page.url
        # The series page reflects their series registration.
        assert page.locator(
            '[data-testid="series-registered-state"]'
        ).is_visible()

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestPerOccurrenceCancelFlow:
    def test_per_occurrence_registrant_keeps_cancel(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_user("main-1077c@test.com", tier_slug="main")
        event = _make_occurrence(
            "standalone-1077c", "Standalone C", required_level=0,
        )
        _event_register("main-1077c@test.com", event)

        ctx = _auth_context(browser, "main-1077c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        assert (
            "You're registered!"
            in page.locator('[data-testid="event-registered-heading"]').inner_text()
        )
        cancel = page.locator("[data-event-unregister-button]")
        assert cancel.is_visible()

        # The cancel flow pops a native confirm() dialog; accept it.
        page.on("dialog", lambda dialog: dialog.accept())
        cancel.click()
        # The card flips to the unregistered state.
        page.locator("[data-event-register-button]").wait_for(state="visible")

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestBothRegistrationsShowCancel:
    def test_both_series_and_occurrence_shows_cancel(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_user("main-1077d@test.com", tier_slug="main")
        series = _make_series("series-1077d", "Series D")
        event = _make_occurrence(
            "series-1077d-occ", "Series D Session", series=series,
        )
        _series_register("main-1077d@test.com", series)
        _event_register("main-1077d@test.com", event)

        ctx = _auth_context(browser, "main-1077d@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        # Per-occurrence wins: cancel button present, no series-manage link.
        assert page.locator("[data-event-unregister-button]").is_visible()
        assert page.locator(
            '[data-testid="event-manage-series-registration-link"]'
        ).count() == 0

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestUnregisteredOccurrenceStillSeriesRegistered:
    def test_cancelled_occurrence_falls_back_to_series(
        self, django_server, browser
    ):
        _reset_event_state()
        _create_user("main-1077e@test.com", tier_slug="main")
        series = _make_series("series-1077e", "Series E")
        event = _make_occurrence(
            "series-1077e-occ", "Series E Session", series=series,
        )
        # Series flag stands; the per-occurrence row was cancelled (none here).
        _series_register("main-1077e@test.com", series)

        ctx = _auth_context(browser, "main-1077e@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        assert (
            "You're registered for this series"
            in page.locator('[data-testid="event-registered-heading"]').inner_text()
        )
        assert page.locator("[data-event-unregister-button]").count() == 0

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestStandaloneEventUnaffected:
    def test_standalone_event_shows_register(self, django_server, browser):
        _reset_event_state()
        _create_user("main-1077f@test.com", tier_slug="main")
        event = _make_occurrence(
            "standalone-1077f", "Standalone F", required_level=0,
        )

        ctx = _auth_context(browser, "main-1077f@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        assert page.locator("[data-event-register-button]").is_visible()
        assert page.locator(
            '[data-testid="event-manage-series-registration-link"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="event-registered-confirmation"]'
        ).count() == 0

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestAnonymousOnSeriesOccurrence:
    def test_anonymous_sees_normal_card(self, django_server, page):
        _reset_event_state()
        series = _make_series("series-1077g", "Series G")
        event = _make_occurrence(
            "series-1077g-occ", "Series G Session", series=series,
            required_level=0,
        )

        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        # Anonymous email-only form renders; no series block.
        assert page.locator(
            '[data-testid="event-anonymous-email-form"]'
        ).is_visible()
        assert page.locator(
            '[data-testid="event-manage-series-registration-link"]'
        ).count() == 0
        assert page.locator(
            '[data-testid="event-registered-confirmation"]'
        ).count() == 0


@pytest.mark.django_db(transaction=True)
class TestSeriesRegistrantCanJoin:
    def test_join_link_visible_near_start(self, django_server, browser):
        _reset_event_state()
        _create_user("main-1077h@test.com", tier_slug="main")
        series = _make_series("series-1077h", "Series H")
        event = _make_occurrence(
            "series-1077h-occ", "Series H Session", series=series,
            offset_minutes=2, zoom_join_url="https://zoom.us/j/1077",
        )
        _series_register("main-1077h@test.com", series)

        ctx = _auth_context(browser, "main-1077h@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{event.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        assert page.locator(
            '[data-testid="event-registered-confirmation"]'
        ).is_visible()
        join = page.locator('[data-testid="event-join-now"]')
        assert join.is_visible()
        assert "Click here to join" in join.inner_text()

        ctx.close()
