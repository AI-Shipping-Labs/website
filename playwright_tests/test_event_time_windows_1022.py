"""Playwright coverage for centralized event time-window querysets (#1022)."""

import datetime
import os
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone
from freezegun import freeze_time

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_user as _create_user

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection

pytestmark = pytest.mark.local_only


def _clear_event_data():
    from content.models import Workshop, WorkshopPage
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    *,
    title,
    slug,
    start_datetime,
    end_datetime=None,
    status="upcoming",
    published=True,
    recording_url="",
):
    from events.models import Event

    event = Event.objects.create(
        title=title,
        slug=slug,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        status=status,
        published=published,
        recording_url=recording_url,
    )
    connection.close()
    return event


def _register(user, event):
    from events.models import EventRegistration

    EventRegistration.objects.create(user=user, event=event)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestVisitorEventTimeWindows1022:
    @pytest.mark.core
    def test_visitor_switches_upcoming_and_past_event_filters(self, django_server, page):
        _clear_event_data()
        now = timezone.now()
        _create_event(
            title="Upcoming Workshop 1022",
            slug="upcoming-workshop-1022",
            start_datetime=now + datetime.timedelta(days=3),
            end_datetime=now + datetime.timedelta(days=3, hours=1),
            status="upcoming",
        )
        _create_event(
            title="Completed Future Hidden 1022",
            slug="completed-future-hidden-1022",
            start_datetime=now + datetime.timedelta(days=4),
            end_datetime=now + datetime.timedelta(days=4, hours=1),
            status="completed",
        )
        _create_event(
            title="Finished Recording 1022",
            slug="finished-recording-1022",
            start_datetime=now - datetime.timedelta(days=3),
            end_datetime=now - datetime.timedelta(days=3, hours=-1),
            status="completed",
            recording_url="https://video.test/finished-1022",
        )

        page.goto(f"{django_server}/events", wait_until="domcontentloaded")
        body = page.content()
        assert "Upcoming Workshop 1022" in body
        assert "Completed Future Hidden 1022" not in body
        assert "Finished Recording 1022" not in body
        assert page.locator('[data-testid="events-upcoming-section"]').count() == 1
        assert page.locator('[data-testid="events-past-section"]').count() == 0

        page.locator('[data-testid="events-filter-upcoming"]').click()
        page.wait_for_load_state("domcontentloaded")
        body = page.content()
        assert "Upcoming Workshop 1022" in body
        assert "Completed Future Hidden 1022" not in body
        assert "Finished Recording 1022" not in body

        page.locator('[data-testid="events-filter-past"]').click()
        page.wait_for_load_state("domcontentloaded")
        body = page.content()
        assert "Finished Recording 1022" in body
        assert "Upcoming Workshop 1022" not in body
        assert "Completed Future Hidden 1022" not in body


@pytest.mark.django_db(transaction=True)
class TestDashboardEventTimeWindows1022:
    @pytest.mark.core
    @freeze_time("2026-08-12T14:00:00Z")
    def test_dashboard_lists_only_eligible_registered_future_events(self, django_server, browser):
        _clear_event_data()
        user = _create_user("events-1022@test.com", tier_slug="main")
        user.preferred_timezone = "America/New_York"
        user.save(update_fields=["preferred_timezone"])
        connection.close()

        member_timezone = ZoneInfo(user.preferred_timezone)
        now = timezone.now()
        # Freeze on a member-local Wednesday because `_get_this_week_events`
        # ends at Sunday 23:59:59.999999 in that timezone. An unfrozen
        # `now + 2 days` crosses that cutoff when this test runs on weekends.
        first_start = datetime.datetime(2026, 8, 13, 10, 0, tzinfo=member_timezone)
        near_cutoff_start = datetime.datetime(2026, 8, 16, 23, 59, 59, 999998, tzinfo=member_timezone)
        after_cutoff_start = datetime.datetime(2026, 8, 17, 0, 0, tzinfo=member_timezone)

        first = _create_event(
            title="Soon Eligible 1022",
            slug="soon-eligible-1022",
            start_datetime=first_start,
            end_datetime=first_start + datetime.timedelta(hours=1),
        )
        second = _create_event(
            title="Later Eligible 1022",
            slug="later-eligible-1022",
            start_datetime=near_cutoff_start,
            end_datetime=near_cutoff_start + datetime.timedelta(hours=1),
        )
        next_week = _create_event(
            title="Next Week Hidden Dashboard 1022",
            slug="next-week-hidden-dashboard-1022",
            start_datetime=after_cutoff_start,
            end_datetime=after_cutoff_start + datetime.timedelta(hours=1),
        )
        completed_future = _create_event(
            title="Completed Future Dashboard 1022",
            slug="completed-future-dashboard-1022",
            start_datetime=first_start + datetime.timedelta(hours=2),
            end_datetime=first_start + datetime.timedelta(hours=3),
            status="completed",
        )
        draft = _create_event(
            title="Draft Hidden Dashboard 1022",
            slug="draft-hidden-dashboard-1022",
            start_datetime=first_start + datetime.timedelta(hours=4),
            end_datetime=first_start + datetime.timedelta(hours=5),
            status="draft",
        )
        cancelled = _create_event(
            title="Cancelled Hidden Dashboard 1022",
            slug="cancelled-hidden-dashboard-1022",
            start_datetime=first_start + datetime.timedelta(hours=6),
            end_datetime=first_start + datetime.timedelta(hours=7),
            status="cancelled",
        )
        past = _create_event(
            title="Past Hidden Dashboard 1022",
            slug="past-hidden-dashboard-1022",
            start_datetime=now - datetime.timedelta(days=1, hours=2),
            end_datetime=now - datetime.timedelta(days=1, hours=1),
            status="completed",
        )

        for event in [
            first,
            second,
            next_week,
            completed_future,
            draft,
            cancelled,
            past,
        ]:
            _register(user, event)

        context = _auth_context(browser, "events-1022@test.com")
        page = context.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        body = page.content()
        commitment = page.locator('[data-testid="dashboard-commitment-list"]')
        commitment_text = commitment.inner_text()

        assert "Soon Eligible 1022" in commitment_text
        assert "Later Eligible 1022" in commitment_text
        assert "Next Week Hidden Dashboard 1022" not in commitment_text
        assert "Completed Future Dashboard 1022" not in body
        assert "Draft Hidden Dashboard 1022" not in body
        assert "Cancelled Hidden Dashboard 1022" not in body
        assert "Past Hidden Dashboard 1022" not in body

        first_pos = commitment_text.index("Soon Eligible 1022")
        second_pos = commitment_text.index("Later Eligible 1022")
        assert first_pos < second_pos
