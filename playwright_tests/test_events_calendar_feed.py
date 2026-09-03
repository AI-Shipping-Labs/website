"""Playwright E2E for the subscribable events feed (issue #578).

Browser journeys from the groomed spec:

1. Visitor discovers the subscribe option from the events page —
   the View Toggle row carries a "Subscribe to all events" trigger,
   opening it reveals Google / Apple / Copy options with the right
   URL shape.
2. A member subscribes via Google Calendar one-click — clicking the
   Google option navigates to ``calendar.google.com`` with a ``cid``
   parameter that decodes back to the platform's webcal:// URL.

HTTP/calendar payload contracts live in
``events/tests/test_events_calendar_feed_http.py`` (#1483).

Usage:
    uv run pytest playwright_tests/test_events_calendar_feed.py -v
"""

import datetime
import os
from urllib.parse import unquote

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _clear_events():
    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_event(
    *,
    slug,
    title,
    status="upcoming",
    external_host="",
    required_level=0,
    published=True,
    start_datetime=None,
    description="",
    ics_sequence=0,
):
    from events.models import Event

    if start_datetime is None:
        start_datetime = timezone.now() + datetime.timedelta(days=7)
    event = Event.objects.create(
        slug=slug,
        title=title,
        description=description or f"Body for {title}.",
        status=status,
        external_host=external_host,
        required_level=required_level,
        published=published,
        start_datetime=start_datetime,
        ics_sequence=ics_sequence,
    )
    connection.close()
    return event


# --- Scenario 1: discover the subscribe option ----------------------------


@pytest.mark.django_db(transaction=True)
class TestVisitorDiscoversSubscribeOption:
    """The View Toggle row carries a Subscribe trigger; opening it
    surfaces Google, Apple, and Copy-feed-URL options with the
    canonical URL shape.
    """

    def test_anonymous_sees_subscribe_options_with_correct_urls(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        _create_event(slug="anchor-evt", title="Anchor Event")

        response = page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )
        assert response.status == 200

        # Trigger lives in the current list toolbar beside Calendar and the
        # Upcoming/Past filters.
        toggle_row = page.locator(
            '[data-testid="events-list-toolbar"]',
        )
        assert toggle_row.count() == 1
        assert toggle_row.locator(
            '[data-testid="events-subscribe-trigger"]',
        ).count() == 1
        assert "Calendar" in toggle_row.inner_text()
        assert "Upcoming" in toggle_row.inner_text()
        assert "Past" in toggle_row.inner_text()
        assert "Subscribe to all events" in toggle_row.inner_text()

        # Open the popover.
        page.locator(
            '[data-testid="events-subscribe-trigger"]',
        ).click()

        google = page.locator('[data-testid="events-subscribe-google"]')
        apple = page.locator('[data-testid="events-subscribe-apple"]')
        copy_input = page.locator(
            '[data-testid="events-subscribe-feed-input"]',
        )
        copy_button = page.locator(
            '[data-testid="events-subscribe-copy-button"]',
        )
        assert google.count() == 1
        assert apple.count() == 1
        assert copy_input.count() == 1
        assert copy_button.count() == 1

        # Apple option points at webcal:// — exact match.
        apple_href = apple.get_attribute("href")
        assert apple_href.startswith("webcal://")
        assert apple_href.endswith("/events/calendar.ics")

        # Google option points at calendar.google.com/calendar/r?cid=...
        # and the cid value, when URL-decoded, matches the webcal URL.
        google_href = google.get_attribute("href")
        assert google_href.startswith(
            "https://calendar.google.com/calendar/r?cid=",
        )
        cid_value = google_href.split("cid=", 1)[1]
        assert unquote(cid_value).startswith("webcal://")
        assert unquote(cid_value).endswith("/events/calendar.ics")

        # Copy input exposes the canonical https URL.
        copy_value = copy_input.get_attribute("value")
        assert copy_value.startswith("http")
        assert copy_value.endswith("/events/calendar.ics")


# --- Scenario 2: member subscribes via Google ----------------------------


@pytest.mark.django_db(transaction=True)
class TestMemberSubscribesViaGoogle:
    """A logged-in member clicks the Google option and lands on a
    calendar.google.com URL whose ``cid`` parameter resolves back to
    the platform's webcal:// feed URL. Playwright stops at the
    Google landing — the actual confirmation dialog is the [HUMAN]
    criterion.
    """

    def test_logged_in_user_google_link_targets_calendar_google(
        self, django_server, browser,
    ):
        _clear_events()
        _ensure_tiers()
        _create_user("main@test.com", tier_slug="main")
        _create_event(slug="member-evt", title="Member Event")

        ctx = _auth_context(browser, "main@test.com")
        page = ctx.new_page()

        page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )
        page.locator(
            '[data-testid="events-subscribe-trigger"]',
        ).click()

        google_href = page.locator(
            '[data-testid="events-subscribe-google"]',
        ).get_attribute("href")
        assert google_href.startswith(
            "https://calendar.google.com/calendar/r?cid=",
        )
        cid_value = google_href.split("cid=", 1)[1]
        decoded = unquote(cid_value)
        assert decoded.startswith("webcal://")
        assert decoded.endswith("/events/calendar.ics")
