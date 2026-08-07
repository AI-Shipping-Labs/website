"""Playwright E2E for the Book Club meetings + chapter-event wiring (#1369).

Covers the rendered flows that cross pages:

1. A Main member sees the "When we meet" rows and clicks through to the event.
2. A guest is gated and no meeting rows leak below the gate.
3. Staff attach a series event to a chapter from Studio, then detach it.
4. The Studio chapter-event selector is disabled/hinted without a book series.

Model/API/view rules (the series-only constraint, the attach/reject matrix,
the derived label, the empty state) are covered faster as Django TestCase
modules; these tests exercise the rendered flows.

Usage:
    uv run pytest playwright_tests/test_book_club_meetings_1369.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402
from django.utils import timezone  # noqa: E402

pytestmark = pytest.mark.local_only


def _clear():
    from bookclub.models import Book
    from events.models import Event, EventSeries

    Book.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _create_book_with_series(*, slug="inference-engineering", with_series=True):
    from bookclub.models import Book, Chapter
    from events.models import Event, EventSeries

    series = None
    kickoff = None
    if with_series:
        series = EventSeries.objects.create(
            name="Inference Engineering Book Club",
            slug="inference-engineering-book-club", cadence="none",
            day_of_week=None, start_time=None, timezone="Europe/Berlin",
            required_level=0,
        )
        # Derive dates from now() so the fixture never rots (date-rot guard).
        start = timezone.now() + datetime.timedelta(days=7)
        kickoff = Event.objects.create(
            title="Book club kickoff", slug="book-club-kickoff",
            start_datetime=start, end_datetime=start + datetime.timedelta(hours=1),
            status="upcoming", origin="studio", event_series=series,
        )
    book = Book.objects.create(
        title="Inference Engineering", slug=slug, author="Philip Kiely",
        required_level=20, status="current",
        start_date=(timezone.now() + datetime.timedelta(days=4)).date(),
        event_series=series,
    )
    Chapter.objects.create(book=book, number=0, title="Inference")
    connection.close()
    return book, series, kickoff


@pytest.mark.django_db(transaction=True)
class TestMemberMeetings:
    def test_member_sees_meeting_rows_and_clicks_through(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_book_with_series()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            section = page.locator('[data-testid="book-when-we-meet"]')
            section.wait_for(state="visible")
            rows = page.locator('[data-testid="book-meeting-row"]')
            assert rows.count() == 1
            assert "Book club kickoff" in rows.first.inner_text()
            # Click the row -> land on the event detail page.
            rows.first.click()
            page.wait_for_url("**/events/**/book-club-kickoff")
        finally:
            context.close()

    def test_guest_is_gated_with_no_meeting_rows(self, django_server, browser):
        _ensure_tiers()
        _clear()
        _create_book_with_series()

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-guest-gate"]').wait_for(
                state="visible",
            )
            assert page.locator(
                '[data-testid="book-when-we-meet"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="book-meeting-row"]',
            ).count() == 0
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioChapterEvent:
    def test_staff_attaches_then_detaches_chapter_event(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        book, _series, kickoff = _create_book_with_series()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/books/{book.pk}/",
                wait_until="domcontentloaded",
            )
            selector = page.locator('[data-testid="book-chapter-event"]').first
            selector.wait_for(state="visible")
            selector.select_option(str(kickoff.pk))
            page.locator('[data-testid="book-chapter-save"]').first.click()
            page.wait_for_url(f"**/studio/books/{book.pk}/")
            # After save the chapter row reflects the linked event.
            assert page.locator(
                '[data-testid="book-chapter-event"]',
            ).first.input_value() == str(kickoff.pk)

            # Detach via "— None —".
            page.locator(
                '[data-testid="book-chapter-event"]',
            ).first.select_option("")
            page.locator('[data-testid="book-chapter-save"]').first.click()
            page.wait_for_url(f"**/studio/books/{book.pk}/")
            assert page.locator(
                '[data-testid="book-chapter-event"]',
            ).first.input_value() == ""
        finally:
            context.close()

    def test_selector_disabled_without_book_series(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        book, _series, _kickoff = _create_book_with_series(
            slug="no-series-book", with_series=False,
        )
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/books/{book.pk}/",
                wait_until="domcontentloaded",
            )
            selector = page.locator('[data-testid="book-chapter-event"]').first
            selector.wait_for(state="attached")
            assert selector.is_disabled()
            assert page.locator(
                '[data-testid="book-chapter-event-hint"]',
            ).first.is_visible()
        finally:
            context.close()
