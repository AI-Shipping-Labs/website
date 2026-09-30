"""A cohort member joins a live course session from its syllabus unit.

Inside the join window the session unit renders the event page's join block,
and its link sends a dated cohort member (who never registered separately)
straight to the call.
"""

import datetime
import os

import pytest
from django.db import connection
from django.utils import timezone
from playwright.sync_api import expect

from playwright_tests.conftest import auth_context, create_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
pytestmark = pytest.mark.local_only

ZOOM_URL = "https://zoom.us/j/555000111"


def _session_starting_in_two_minutes(email):
    """A dated cohort member and a hidden-series session inside its join window."""
    from content.models import Cohort, CohortEnrollment, Course, Module, Unit
    from events.models import Event, EventSeries

    user = create_user(email)
    course = Course.objects.create(
        title="Join window course", slug="join-window-course",
        status="published", required_level=0,
    )
    week = Module.objects.create(
        course=course, title="Week 3", slug="week-3", sort_order=1,
    )
    unit = Unit.objects.create(
        module=week, title="Session 3", slug="session", kind="event",
        sort_order=1, session_position=3,
    )
    series = EventSeries.objects.create(
        name="Join window sessions", slug="join-window-sessions",
        visibility="hidden",
    )
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course, name="Cohort 4", mode="cohort", external_key="4",
        start_date=today - datetime.timedelta(days=14),
        end_date=today + datetime.timedelta(days=30), event_series=series,
    )
    CohortEnrollment.objects.create(user=user, cohort=cohort)
    start = timezone.now() + datetime.timedelta(minutes=2)
    event = Event.objects.create(
        event_series=series, series_position=3, slug="join-window-session-3",
        title="Session 3", status="upcoming", published=True,
        start_datetime=start, end_datetime=start + datetime.timedelta(hours=1),
        zoom_join_url=ZOOM_URL,
    )
    connection.close()
    return user, unit, event


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_cohort_member_joins_live_session_from_the_unit(django_server, browser):
    user, unit, event = _session_starting_in_two_minutes("join-window@test.com")
    context = auth_context(browser, user.email)
    page = context.new_page()
    try:
        page.goto(
            f"{django_server}{unit.get_absolute_url()}?cohort=4",
            wait_until="domcontentloaded",
        )
        join_block = page.get_by_test_id("unit-session-content").get_by_test_id(
            "event-join-now",
        )
        expect(join_block).to_be_visible()
        expect(page.get_by_test_id("unit-session-join-note")).to_have_count(0)
        join_link = join_block.get_by_role("link", name="Click here to join")
        expect(join_link).to_have_attribute("href", event.get_join_url())

        response = page.request.get(
            f"{django_server}{event.get_join_url()}", max_redirects=0,
        )
        assert response.status == 302
        assert response.headers["location"] == ZOOM_URL
    finally:
        context.close()
