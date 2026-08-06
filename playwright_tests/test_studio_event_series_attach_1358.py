"""Playwright E2E for Studio series attach/detach + collections (#1358).

Scenarios:
- Staff attaches a standalone event to a collection from the event edit page,
  then detaches it via the "— None —" option.
- Staff creates a "No fixed cadence" collection with no weekly slot.

Usage:
    uv run pytest playwright_tests/test_studio_event_series_attach_1358.py -v
"""

import os
import re

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _reset_event_state():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestStudioSeriesAttach:
    @pytest.mark.core
    def test_attach_then_detach_from_edit_page(self, django_server, browser):
        from datetime import timedelta

        from django.db import connection
        from django.utils import timezone

        from events.models import Event, EventSeries

        _reset_event_state()
        collection = EventSeries.objects.create(
            name="Book Club Collection",
            slug="book-club-collection",
            cadence="none",
            day_of_week=None,
            start_time=None,
            timezone="UTC",
        )
        start = timezone.now() + timedelta(days=30)
        event = Event.objects.create(
            title="Standalone Kickoff",
            slug="standalone-kickoff",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status="draft",
            timezone="UTC",
            origin="studio",
        )
        connection.close()

        _create_staff_user("staff-attach-1358@test.com")
        ctx = _auth_context(browser, "staff-attach-1358@test.com")
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())

        # Attach the event to the collection.
        page.goto(
            f"{django_server}/studio/events/{event.pk}/edit",
            wait_until="domcontentloaded",
        )
        page.select_option(
            'select[name="event_series"]', str(collection.pk),
        )
        page.locator('button[type="submit"]:has-text("Save Changes")').click()
        page.wait_for_url(re.compile(rf".*/studio/events/{event.pk}/edit$"))

        # The read-only "Part of series" line links to the collection.
        parent = page.locator('[data-testid="event-parent-series"]')
        assert parent.is_visible()
        assert "Book Club Collection" in parent.inner_text()

        event.refresh_from_db()
        assert event.event_series_id == collection.pk
        assert event.series_position is None

        # Detach via the "— None —" option.
        page.select_option('select[name="event_series"]', "")
        page.locator('button[type="submit"]:has-text("Save Changes")').click()
        page.wait_for_url(re.compile(rf".*/studio/events/{event.pk}/edit$"))
        assert page.locator(
            '[data-testid="event-parent-series"]'
        ).count() == 0

        event.refresh_from_db()
        assert event.event_series_id is None
        connection.close()
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestStudioCreateCollection:
    @pytest.mark.core
    def test_create_no_fixed_cadence_collection(self, django_server, browser):
        from django.db import connection

        from events.models import EventSeries

        _reset_event_state()
        _create_staff_user("staff-collection-1358@test.com")
        ctx = _auth_context(browser, "staff-collection-1358@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/studio/event-series/new",
            wait_until="domcontentloaded",
        )
        page.fill('input[name="name"]', "Inference Book Club")
        page.select_option('select[name="cadence"]', "none")
        # The weekly-schedule fields hide; submit with them blank.
        page.locator(
            'button[type="submit"]:has-text("Create series")'
        ).click()
        page.wait_for_url(re.compile(r".*/studio/event-series/\d+.*"))

        series = EventSeries.objects.get(name="Inference Book Club")
        assert series.cadence == "none"
        assert series.day_of_week is None
        assert series.start_time is None
        assert series.events.count() == 0
        # The detail page loads with no weekly-cadence claim.
        body = page.locator("body").inner_text()
        assert "Weekly on" not in body
        connection.close()
        ctx.close()
