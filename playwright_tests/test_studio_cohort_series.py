"""Browser coverage for linking a cohort to its event series in Studio.

Follow-up to the production incident where every Cohort 4 session showed
"Not scheduled" because the cohort had no event series. Django tests own
the banner context and the server-side requirement; this file proves the
operator journey and the mode toggle, which is client-side JavaScript.
"""

import os
from datetime import timedelta

import pytest
from django.utils import timezone

from playwright_tests.conftest import auth_context as _auth_context
from playwright_tests.conftest import create_staff_user as _create_staff_user
from scripts.browser_journey_policy import browser_journey

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Seeds its own staff user and cohort rows, so it runs in the local lane.
pytestmark = pytest.mark.local_only

STAFF_EMAIL = "studio-cohort-series@test.com"


@pytest.mark.django_db(transaction=True)
@browser_journey
def test_staff_links_an_unlinked_cohort_to_its_series(django_server, browser):
    from content.models import Cohort, Course
    from events.models import Event, EventSeries

    _create_staff_user(email=STAFF_EMAIL)
    course = Course.objects.create(
        title="Buildcamp", slug="cohort-series-buildcamp", status="published",
    )
    today = timezone.localdate()
    cohort = Cohort.objects.create(
        course=course, name="Cohort 4", mode="cohort", external_key="4",
        start_date=today, end_date=today + timedelta(days=60),
    )
    series = EventSeries.objects.create(
        name="Buildcamp office hours - Cohort 4",
        slug="cohort-series-oh-4", cadence="none", day_of_week=None, start_time=None,
    )
    Event.objects.create(
        title="Office hours 1", slug="cohort-series-oh-4-1", event_series=series,
        series_position=1, status="upcoming", published=True, origin="studio",
        start_datetime=timezone.now() + timedelta(days=1),
    )
    EventSeries.objects.create(
        name="[DELETE ME in Studio] duplicate", slug="cohort-series-oh-4-dup",
        cadence="none", day_of_week=None, start_time=None,
    )
    connection.close()

    context = _auth_context(browser, STAFF_EMAIL)
    page = context.new_page()
    page.goto(
        f"{django_server}/studio/courses/{course.pk}/cohorts/{cohort.pk}/edit",
        wait_until="domcontentloaded",
    )
    banner = page.locator('[data-testid="cohort-series-warnings"]')
    assert "No event series is linked" in banner.inner_text()

    select = page.locator('[data-testid="cohort-edit-event-series"]')
    assert select.evaluate("el => el.required") is True
    labels = select.locator("option").all_inner_texts()
    assert labels == [
        "Choose an event series",
        "Buildcamp office hours - Cohort 4 (1 event)",
        "[DELETE ME in Studio] duplicate (0 events)",
    ]

    # A self-paced cohort has no series, so the picker switches off.
    page.locator('[data-testid="cohort-edit-mode"]').select_option("self_paced")
    assert select.is_disabled()
    page.locator('[data-testid="cohort-edit-mode"]').select_option("cohort")
    assert select.is_enabled()

    select.select_option(str(series.pk))
    page.locator('[data-testid="cohort-edit-save"]').click()
    page.wait_for_load_state("domcontentloaded")

    assert page.locator('[data-testid="cohort-series-warnings"]').count() == 0
    assert select.input_value() == str(series.pk)
    cohort.refresh_from_db()
    assert cohort.event_series_id == series.pk
    context.close()
