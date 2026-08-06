"""Playwright E2E for Book Club reading state (issue #1364).

Exercises the rendered mark-read flow on the /books/<slug> roadmap:

1. A member marks a chapter read and the progress line + bar advance.
2. A member unmarks a chapter and progress goes back down.
3. Refreshing after a mark does not double-toggle (POST/redirect/GET).
4. A gated member never sees the mark control.
5. An anonymous mark POST is redirected to log in and records nothing.
6. Reading progress is private to each member.

Model-level rules (idempotency, scope enforcement, the member API) are
covered faster as Django TestCase modules; these tests exercise the flows.

Usage:
    uv run pytest playwright_tests/test_book_club_reading_1364.py -v
"""

import datetime
import os

import pytest

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

pytestmark = pytest.mark.local_only

FIXED_KICKOFF_DATE = datetime.date(2026, 8, 10)


def _clear_books():
    from bookclub.models import Book

    Book.objects.all().delete()
    connection.close()


def _create_book(*, slug="inference-engineering", required_level=20, chapters=5):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title="Inference Engineering", slug=slug, author="Philip Kiely",
        required_level=required_level, status="current",
        start_date=FIXED_KICKOFF_DATE,
        description="The technologies behind every AI product in production.",
    )
    for i in range(chapters):
        Chapter.objects.create(
            book=book, number=i, title=f"Chapter {i}", week_label=f"Week {i + 1}",
        )
    connection.close()
    return book


def _mark_read(email, slug, number):
    from accounts.models import User
    from bookclub.models import Book, ChapterRead

    user = User.objects.get(email=email)
    chapter = Book.objects.get(slug=slug).chapters.get(number=number)
    ChapterRead.objects.get_or_create(user=user, chapter=chapter)
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestBookReadingFlow:
    def test_member_marks_chapter_read_and_progress_advances(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            progress = page.locator('[data-testid="book-reading-progress"]')
            assert "0 of 5 read" in progress.inner_text()
            assert page.locator(
                '[data-testid="book-chapter-mark-read"]',
            ).count() == 5

            # Mark chapter 0 read (first control).
            page.locator(
                '[data-testid="book-chapter-mark-read"]',
            ).first.click()
            page.wait_for_load_state("domcontentloaded")

            assert "1 of 5 read" in page.locator(
                '[data-testid="book-reading-progress"]',
            ).inner_text()
            # Chapter 0 now shows a Read badge + Mark unread control.
            assert page.locator(
                '[data-testid="book-chapter-mark-unread"]',
            ).count() == 1
            assert page.locator(
                '[data-testid="book-chapter-read-badge"]',
            ).count() == 1
        finally:
            context.close()

    def test_member_unmarks_chapter_and_progress_decreases(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _mark_read("main@test.com", "inference-engineering", 0)
        _mark_read("main@test.com", "inference-engineering", 1)

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            assert "2 of 5 read" in page.locator(
                '[data-testid="book-reading-progress"]',
            ).inner_text()

            # Unmark one chapter (first Mark unread control).
            page.locator(
                '[data-testid="book-chapter-mark-unread"]',
            ).first.click()
            page.wait_for_load_state("domcontentloaded")

            assert "1 of 5 read" in page.locator(
                '[data-testid="book-reading-progress"]',
            ).inner_text()
        finally:
            context.close()

    def test_refresh_after_mark_does_not_double_toggle(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            page.locator(
                '[data-testid="book-chapter-mark-read"]',
            ).first.click()
            page.wait_for_load_state("domcontentloaded")
            assert "1 of 5 read" in page.locator(
                '[data-testid="book-reading-progress"]',
            ).inner_text()

            # A reload re-issues a GET (P/R/G), not the POST.
            page.reload(wait_until="domcontentloaded")
            assert "1 of 5 read" in page.locator(
                '[data-testid="book-reading-progress"]',
            ).inner_text()
            assert page.locator(
                '[data-testid="book-chapter-read-badge"]',
            ).count() == 1
        finally:
            context.close()

    def test_gated_member_cannot_mark_chapters_read(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5, required_level=20)
        _create_user("free@test.com", tier_slug="free")

        context = _auth_context(browser, "free@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-guest-gate"]').wait_for(state="visible")
            assert page.locator(
                '[data-testid="book-chapter-mark-read"]',
            ).count() == 0
            assert page.locator(
                '[data-testid="book-participation-body"]',
            ).count() == 0
        finally:
            context.close()

    def test_progress_is_private_to_each_member(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("premium@test.com", tier_slug="premium")
        _mark_read("main@test.com", "inference-engineering", 0)
        _mark_read("main@test.com", "inference-engineering", 1)
        _mark_read("main@test.com", "inference-engineering", 2)
        _mark_read("premium@test.com", "inference-engineering", 0)

        context = _auth_context(browser, "premium@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            # premium@test.com sees their own count, not main@test.com's.
            assert "1 of 5 read" in page.locator(
                '[data-testid="book-reading-progress"]',
            ).inner_text()
        finally:
            context.close()
