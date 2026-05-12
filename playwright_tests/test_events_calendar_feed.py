"""Playwright E2E for the subscribable events feed (issue #578).

Six scenarios from the groomed spec:

1. Visitor discovers the subscribe option from the events page —
   the View Toggle row carries a "Subscribe to all events" trigger,
   opening it reveals Google / Apple / Copy options with the right
   URL shape.
2. A member subscribes via Google Calendar one-click — clicking the
   Google option navigates to ``calendar.google.com`` with a ``cid``
   parameter that decodes back to the platform's webcal:// URL.
3. Any calendar client can fetch the feed without authentication —
   the feed returns 200 + text/calendar without cookies and contains
   the seeded published events.
4. Subscriber clients short-circuit when nothing has changed —
   the first fetch surfaces an ETag, sending it back returns 304;
   editing a row and resending returns 200 with a new ETag and the
   updated content.
5. Edits to an event propagate as updates, not duplicates — the
   feed always carries exactly one VEVENT per slug-UID and the
   sequence/title reflect the latest save.
6. External partner events are clearly marked in the feed — the
   Maven event's SUMMARY carries the [Hosted on Maven] prefix, its
   LOCATION is the host name, and its URL still points at the
   platform detail page.
7. Gated and draft events stay out of the public feed — drafts,
   cancelled, and Main-tier events never appear in the anonymous
   feed body.

Usage:
    uv run pytest playwright_tests/test_events_calendar_feed.py -v
"""

import datetime
import os
import urllib.error
import urllib.request
from datetime import timedelta
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


def _http_get(url, headers=None):
    """Plain HTTP GET (no browser, no cookies) — for feed fetches."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            status = resp.status
            resp_headers = dict(resp.headers)
            return status, resp_headers, body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


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

        # Trigger lives in the same row as List / Calendar.
        toggle_row = page.locator(
            '[data-testid="events-view-toggle-row"]',
        )
        assert toggle_row.count() == 1
        assert toggle_row.locator(
            '[data-testid="events-subscribe-trigger"]',
        ).count() == 1
        assert "List" in toggle_row.inner_text()
        assert "Calendar" in toggle_row.inner_text()
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


# --- Scenario 3: anonymous HTTP fetch -------------------------------------


@pytest.mark.django_db(transaction=True)
class TestAnonymousHttpFetchOfFeed:
    """Pure HTTP fetch with no cookies — must return 200, the right
    Content-Type, and the seeded events' VEVENT blocks. Draft and
    gated events must not appear.
    """

    def test_feed_returns_200_and_includes_public_events(
        self, django_server, page,  # page unused; pulls server fixture
    ):
        _clear_events()
        _ensure_tiers()
        future = timezone.now() + timedelta(days=3)
        _create_event(
            slug="open-evt",
            title="Open Anonymous Event",
            start_datetime=future,
        )
        _create_event(
            slug="draft-evt-feed",
            title="Draft Should Not Appear",
            status="draft",
            start_datetime=future,
        )
        _create_event(
            slug="gated-evt-feed",
            title="Gated Should Not Appear",
            required_level=20,
            start_datetime=future,
        )

        status, headers, body = _http_get(
            f"{django_server}/events/calendar.ics",
        )
        assert status == 200
        content_type = headers.get(
            "Content-Type", headers.get("content-type", ""),
        )
        assert content_type == "text/calendar; charset=utf-8"

        text = body.decode("utf-8")
        assert text.startswith("BEGIN:VCALENDAR")
        assert text.rstrip().endswith("END:VCALENDAR")
        assert "Open Anonymous Event" in text
        assert "Draft Should Not Appear" not in text
        assert "draft-evt-feed" not in text
        assert "Gated Should Not Appear" not in text
        assert "gated-evt-feed" not in text


# --- Scenario 4: ETag short-circuit ---------------------------------------


@pytest.mark.django_db(transaction=True)
class TestEtagShortCircuit:
    """``If-None-Match`` with the current ETag returns 304. Editing a
    feed-eligible event mints a new ETag so a follow-up conditional
    request returns 200 with the updated body.
    """

    def test_if_none_match_returns_304_then_200_after_edit(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        future = timezone.now() + timedelta(days=4)
        event = _create_event(
            slug="etag-evt",
            title="Etag Event Original",
            start_datetime=future,
        )

        # First fetch — capture ETag.
        status_a, headers_a, _ = _http_get(
            f"{django_server}/events/calendar.ics",
        )
        assert status_a == 200
        etag = headers_a.get("ETag") or headers_a.get("etag")
        assert etag is not None
        assert etag.startswith('W/"')

        # Repeat with the same ETag — must return 304.
        status_b, headers_b, body_b = _http_get(
            f"{django_server}/events/calendar.ics",
            headers={"If-None-Match": etag},
        )
        assert status_b == 304
        assert body_b == b""

        # Edit the event — bumps updated_at.
        from events.models import Event as EventModel

        # Trigger a save to refresh updated_at properly.
        edited = EventModel.objects.get(pk=event.pk)
        edited.title = "Etag Event Updated"
        edited.save()
        connection.close()

        # Re-fetch with the OLD etag — should be 200 with new etag.
        status_c, headers_c, body_c = _http_get(
            f"{django_server}/events/calendar.ics",
            headers={"If-None-Match": etag},
        )
        assert status_c == 200
        new_etag = headers_c.get("ETag") or headers_c.get("etag")
        assert new_etag is not None
        assert new_etag != etag
        assert b"Etag Event Updated" in body_c


# --- Scenario 5: edits propagate as updates, not duplicates --------------


@pytest.mark.django_db(transaction=True)
class TestEditsPropagateAsUpdatesNotDuplicates:
    """The feed must contain exactly one VEVENT per slug-UID even
    after a title + sequence bump. Stable UIDs are how subscriber
    clients dedupe.
    """

    def test_edited_event_appears_once_with_higher_sequence(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        future = timezone.now() + timedelta(days=5)
        event = _create_event(
            slug="evt-demo",
            title="Demo Original",
            description="Stable demo body.",
            start_datetime=future,
            ics_sequence=1,
        )

        # First fetch carries the original title.
        status_a, _, body_a = _http_get(
            f"{django_server}/events/calendar.ics",
        )
        assert status_a == 200
        assert b"SUMMARY:Demo Original" in body_a

        # Update title and bump sequence.
        from events.models import Event as EventModel

        event = EventModel.objects.get(pk=event.pk)
        event.title = "Demo Updated"
        event.ics_sequence = 2
        event.save()
        connection.close()

        status_b, _, body_b = _http_get(
            f"{django_server}/events/calendar.ics",
        )
        assert status_b == 200
        text = body_b.decode("utf-8")

        # Exactly one VEVENT carrying the stable UID — stable UIDs are
        # how subscriber clients de-duplicate edits versus inserts.
        uid_line = "UID:event-evt-demo@aishippinglabs.com"
        assert text.count(uid_line) == 1

        # The SUMMARY (the calendar's user-visible title) carries the
        # new value, the old one is gone, and SEQUENCE bumped to 2 so
        # subscriber clients treat this as an update.
        assert "SUMMARY:Demo Updated" in text
        assert "SUMMARY:Demo Original" not in text
        assert "SEQUENCE:2" in text


# --- Scenario 6: external partner events are clearly marked ---------------


@pytest.mark.django_db(transaction=True)
class TestExternalEventsAreMarkedInFeed:
    """The Maven cohort's VEVENT carries the [Hosted on Maven]
    SUMMARY prefix, the LOCATION is the host name, and the URL still
    points at the platform detail page.
    """

    def test_maven_event_summary_location_url(self, django_server, page):
        _clear_events()
        _ensure_tiers()
        future = timezone.now() + timedelta(days=2)
        _create_event(
            slug="maven-llm",
            title="LLM Engineering Cohort",
            external_host="Maven",
            start_datetime=future,
        )

        status, _, body = _http_get(
            f"{django_server}/events/calendar.ics",
        )
        assert status == 200
        text = body.decode("utf-8")

        # SUMMARY prefix.
        assert "[Hosted on Maven] LLM Engineering Cohort" in text
        # LOCATION is the partner name.
        assert "LOCATION:Maven" in text
        # URL still points at the platform detail page.
        assert "/events/maven-llm" in text


# --- Scenario 7: gating exclusion (extra, mirrors spec acceptance) -------


@pytest.mark.django_db(transaction=True)
class TestGatedAndDraftStayOutOfPublicFeed:
    """A draft, a cancelled, and a Main-tier event exist alongside a
    single published free event. The anonymous feed contains only
    the free event.
    """

    def test_only_open_published_event_appears(
        self, django_server, page,
    ):
        _clear_events()
        _ensure_tiers()
        future = timezone.now() + timedelta(days=2)
        _create_event(
            slug="open-only", title="Open Free Event",
            start_datetime=future,
        )
        _create_event(
            slug="draft-only", title="Draft Only",
            status="draft", start_datetime=future,
        )
        _create_event(
            slug="cancelled-only", title="Cancelled Only",
            status="cancelled", start_datetime=future,
        )
        _create_event(
            slug="main-only", title="Main Tier Only",
            required_level=20, start_datetime=future,
        )

        status, _, body = _http_get(
            f"{django_server}/events/calendar.ics",
        )
        assert status == 200
        text = body.decode("utf-8")
        assert "Open Free Event" in text
        for forbidden in (
            "Draft Only", "draft-only",
            "Cancelled Only", "cancelled-only",
            "Main Tier Only", "main-only",
        ):
            assert forbidden not in text, f"{forbidden!r} leaked into feed"
