"""Playwright E2E for the Book Club meetings + chapter-event wiring (#1369).

Covers the rendered flows that cross pages:

1. A Main member sees the meeting rows and clicks through to the event.
   (#1461 merged them into the week blocks; the separate section is gone.)
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
import re

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
            rows = page.locator('[data-testid="book-meeting-row"]')
            rows.first.wait_for(state="visible")
            assert rows.count() == 1
            # #1461 merged meetings into the week blocks.
            assert page.locator(
                '[data-testid="book-when-we-meet"]',
            ).count() == 0
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
                '[data-testid="book-meeting-row"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="book-week-group"]',
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


# ---------------------------------------------------------------------------
# Issue #1461: chapters and meetings merged into one week-by-week block.
# ---------------------------------------------------------------------------

PHONE = {"width": 393, "height": 851}
BOOK_URL = "/books/inference-engineering"
SERIES_NAME = "Inference Engineering Book Club"


def _make_event(series, *, title, slug, start, status="upcoming", recap=""):
    from events.models import Event

    return Event.objects.create(
        title=title, slug=slug, start_datetime=start,
        end_datetime=start + datetime.timedelta(hours=1), status=status,
        origin="studio", event_series=series, recap_html=recap,
    )


def _create_week_book(*, week1_past=False, week1_recap=""):
    """Book with a kickoff, two week-linked meetings, and a draft occurrence."""
    from bookclub.models import Book, Chapter
    from events.models import EventSeries

    series = EventSeries.objects.create(
        name=SERIES_NAME, slug="inference-engineering-book-club",
        cadence="none", day_of_week=None, start_time=None,
        timezone="Europe/Berlin", required_level=0,
    )
    now = timezone.now()
    _make_event(
        series, title=f"{SERIES_NAME} — Kickoff", slug="ie-kickoff",
        start=(
            now - datetime.timedelta(days=14) if week1_past
            else now + datetime.timedelta(days=1)
        ),
        status="completed" if week1_past else "upcoming",
    )
    week1 = _make_event(
        series, title=f"{SERIES_NAME} week 1 session", slug="ie-week-1",
        start=(
            now - datetime.timedelta(days=7) if week1_past
            else now + datetime.timedelta(days=8)
        ),
        status="completed" if week1_past else "upcoming",
        recap=week1_recap,
    )
    week2 = _make_event(
        series, title=f"{SERIES_NAME} week 2 session", slug="ie-week-2",
        start=now + datetime.timedelta(days=15),
    )
    # A draft occurrence: the derived schedule label under-reports because of
    # it, which is exactly why the "Meets:" value links to the series page.
    _make_event(
        series, title="Draft session", slug="ie-draft",
        start=now + datetime.timedelta(days=22), status="draft",
    )

    book = Book.objects.create(
        title="Inference Engineering", slug="inference-engineering",
        author="Philip Kiely", required_level=20, status="current",
        start_date=(now - datetime.timedelta(days=20)).date(),
        event_series=series,
    )
    today = timezone.localdate()
    week1_deadline = (
        today - datetime.timedelta(days=3) if week1_past
        else today + datetime.timedelta(days=6)
    )
    Chapter.objects.create(
        book=book, number=0, title="Inference", week_number=1,
        deadline=week1_deadline, event=week1,
    )
    Chapter.objects.create(
        book=book, number=1, title="Prerequisites", week_number=1,
        deadline=week1_deadline, event=week1,
    )
    for number, title in ((2, "Architecture"), (3, "Hardware")):
        Chapter.objects.create(
            book=book, number=number, title=title, week_number=2,
            week_label="Batching",
            deadline=today + datetime.timedelta(days=13), event=week2,
        )
    connection.close()
    return book


def _create_public_note(email, number, body):
    from accounts.models import User
    from bookclub.models import Book, Note, ReaderProfile

    user = User.objects.get(email=email)
    ReaderProfile.objects.update_or_create(
        user=user, defaults={"visibility": "public"},
    )
    chapter = Book.objects.get(slug="inference-engineering").chapters.get(
        number=number,
    )
    Note.objects.create(chapter=chapter, user=user, body=body)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestMergedWeekBlocks:
    def test_week_block_holds_chapters_and_its_meeting(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_week_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}{BOOK_URL}", wait_until="domcontentloaded")
            week2 = page.locator('[data-week-number="2"]')
            week2.wait_for(state="visible")
            block_text = week2.inner_text()
            assert "Ch. 2 — Architecture" in block_text
            assert "Ch. 3 — Hardware" in block_text
            # That week's meeting sits inside the same block, exactly one row.
            rows = week2.locator('[data-testid="book-meeting-row"]')
            assert rows.count() == 1
            assert "When we meet" not in page.inner_text("body")

            rows.first.click()
            page.wait_for_url("**/events/**/ie-week-2")
            assert "week 2 session" in page.inner_text("h1").lower()
        finally:
            context.close()

    def test_meeting_rows_are_distinguishable_on_a_phone(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_week_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.set_viewport_size(PHONE)
            page.goto(f"{django_server}{BOOK_URL}", wait_until="domcontentloaded")
            rows = page.locator('[data-testid="book-meeting-row"]')
            rows.first.wait_for(state="visible")
            assert rows.count() == 3
            names = [
                " ".join((rows.nth(i).text_content() or "").split())
                for i in range(3)
            ]
            assert len(set(names)) == 3, names
            week_rows = [name for name in names if name.startswith("Meeting")]
            assert len(week_rows) == 2, names
            for name in week_rows:
                assert "Inference Engineering" not in name
            assert len([n for n in names if n.startswith("Kickoff")]) == 1, names
        finally:
            context.close()

    def test_recap_affordance_only_on_a_past_meeting_with_notes(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_week_book(
            week1_past=True,
            week1_recap="<p>We covered the KV cache end to end.</p>",
        )
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}{BOOK_URL}", wait_until="domcontentloaded")
            week1 = page.locator('[data-week-number="1"]')
            week1.wait_for(state="visible")
            recap = week1.locator('[data-testid="book-meeting-recap"]')
            assert recap.count() == 1
            recap_href = recap.get_attribute("href")
            assert recap_href is not None
            assert re.fullmatch(r"/events/\d+/ie-week-1/recap", recap_href)

            week2 = page.locator('[data-week-number="2"]')
            assert week2.locator(
                '[data-testid="book-meeting-recap"]',
            ).count() == 0
            assert "recap coming" not in week2.inner_text().lower()

            recap.first.click()
            page.wait_for_url("**/events/**/ie-week-1/recap")
            assert "KV cache end to end" in page.inner_text("body")
        finally:
            context.close()

    def test_themed_week_keeps_its_number_and_the_callout_does_not(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_week_book(week1_past=True)
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}{BOOK_URL}", wait_until="domcontentloaded")
            week2 = page.locator('[data-week-number="2"]')
            week2.wait_for(state="visible")
            heading = week2.locator(
                '[data-testid="book-week-heading"]',
            ).text_content()
            assert heading.strip() == "Week 2 · Batching"

            # Week 1 is past, so week 2 is current: the callout names the theme
            # alone — never "This week · Week 2 · Batching".
            callout = page.locator(
                '[data-testid="book-this-week"]',
            ).text_content()
            assert "This week · Batching" in callout
            assert "Week 2" not in callout
        finally:
            context.close()

    def test_meets_label_links_to_the_full_series_schedule(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_week_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(f"{django_server}{BOOK_URL}", wait_until="domcontentloaded")
            link = page.locator('[data-testid="book-meets-link"]')
            link.wait_for(state="visible")
            link.click()
            page.wait_for_url("**/events/series/**")
            assert SERIES_NAME in page.inner_text("body")
        finally:
            context.close()

    def test_guest_sees_one_gate_and_none_of_the_group_material(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear()
        _create_week_book()
        _create_user("author@test.com", tier_slug="main")
        _create_public_note("author@test.com", 0, "Private group takeaway.")

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(f"{django_server}{BOOK_URL}", wait_until="domcontentloaded")
            page.locator('[data-testid="book-guest-gate"]').wait_for(
                state="visible",
            )
            assert page.locator('[data-testid="book-guest-gate"]').count() == 1
            assert page.locator('[data-testid="book-chapter-link"]').count() == 0
            assert page.locator('[data-testid="book-meeting-row"]').count() == 0
            assert page.locator(
                '[data-testid="book-meeting-recap"]',
            ).count() == 0

            page.goto(
                f"{django_server}{BOOK_URL}/chapters/0",
                wait_until="domcontentloaded",
            )
            assert "Ch. 0 — Inference" in page.inner_text("h1")
            assert page.locator('[data-testid="book-guest-gate"]').count() == 1
            assert page.locator('[data-testid="note-body"]').count() == 0
            assert "Private group takeaway." not in page.inner_text("body")
            # The forward nav must not compete with the gate's primary CTA.
            next_class = page.locator(
                '[data-testid="chapter-next"]',
            ).get_attribute("class")
            assert "bg-accent text-accent-foreground" not in next_class
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestWeeklessBook:
    def test_book_without_weeks_or_linked_events_still_reads_cleanly(
        self, django_server, browser,
    ):
        from bookclub.models import Book, Chapter
        from events.models import EventSeries

        _ensure_tiers()
        _clear()
        series = EventSeries.objects.create(
            name="Flat Book Club", slug="flat-book-club", cadence="none",
            day_of_week=None, start_time=None, timezone="Europe/Berlin",
            required_level=0,
        )
        _make_event(
            series, title="Flat Book Club opening call", slug="flat-open",
            start=timezone.now() + datetime.timedelta(days=2),
        )
        book = Book.objects.create(
            title="Flat Book", slug="flat-book", author="Author",
            required_level=20, status="current", event_series=series,
        )
        Chapter.objects.create(book=book, number=0, title="A")
        Chapter.objects.create(book=book, number=1, title="B")
        connection.close()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/flat-book",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-chapter-link"]').first.wait_for(
                state="visible",
            )
            assert page.locator('[data-testid="book-week-heading"]').count() == 0
            assert page.locator(
                '[data-testid="book-chapter-mark-read"]',
            ).count() == 2
            assert page.locator('[data-testid="book-meeting-row"]').count() == 1
            assert "When we meet" not in page.inner_text("body")
        finally:
            context.close()
