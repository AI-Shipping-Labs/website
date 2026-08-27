"""Playwright E2E for the Book Club Progress board (issue #1367).

Exercises the rendered, de-gamified, you-first roster at
/books/<slug>/progress:

1. A member reaches the board from book detail and finds their own row first,
   highlighted, with other readers ordered by chapters read.
2. Reading more chapters advances the member's own count.
3. A first reader is welcomed with a nudge, not a dead-end.
4. A below-tier member and an anonymous visitor see the gate, no roster.
5. The board is calm — no points / streak / rank / leaderboard chrome.
6. Every reader is named and links to a profile, even with private notes.
7. A non-member never sees the board link on book detail.
8. A draft book keeps its board private (404).

No hard-coded near-2026 dates: the board never renders dates, so the book is
created without a kickoff date (date-rot guard clean).

Usage:
    uv run pytest playwright_tests/test_book_club_progress_1367.py -v
"""

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

SLUG = "inference-engineering"


def _clear_books():
    from bookclub.models import Book

    Book.objects.all().delete()
    connection.close()


def _create_book(*, slug=SLUG, required_level=20, chapters=5, status="current"):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title="Inference Engineering", slug=slug, author="Philip Kiely",
        required_level=required_level, status=status,
        description="The technologies behind every AI product in production.",
    )
    for i in range(chapters):
        Chapter.objects.create(book=book, number=i, title=f"Chapter {i}")
    connection.close()
    return book


def _mark_read(email, slug, numbers):
    from accounts.models import User
    from bookclub.models import Book, ChapterRead

    user = User.objects.get(email=email)
    book = Book.objects.get(slug=slug)
    for number in numbers:
        chapter = book.chapters.get(number=number)
        ChapterRead.objects.get_or_create(user=user, chapter=chapter)
    connection.close()


def _set_visibility(email, visibility):
    """Set whether other members can read this reader's notes."""
    from accounts.models import User
    from bookclub.models import ReaderProfile

    user = User.objects.get(email=email)
    ReaderProfile.objects.update_or_create(
        user=user, defaults={"visibility": visibility},
    )
    connection.close()


def _create_note(email, number, body):
    from accounts.models import User
    from bookclub.models import Book, Note

    user = User.objects.get(email=email)
    chapter = Book.objects.get(slug=SLUG).chapters.get(number=number)
    Note.objects.create(chapter=chapter, user=user, body=body)
    connection.close()


def _user_pk(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


@pytest.mark.django_db(transaction=True)
class TestProgressBoard:
    def test_member_finds_own_row_first_and_readers_ordered(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("reader-fast@test.com", tier_slug="main")
        _create_user("reader-slow@test.com", tier_slug="main")
        _mark_read("main@test.com", SLUG, [0, 1])
        _mark_read("reader-fast@test.com", SLUG, [0, 1, 2, 3])
        _mark_read("reader-slow@test.com", SLUG, [0])
        me = _user_pk("main@test.com")
        fast = _user_pk("reader-fast@test.com")
        slow = _user_pk("reader-slow@test.com")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-progress-link"]').click()
            page.wait_for_load_state("domcontentloaded")

            assert page.url.endswith(f"/books/{SLUG}/progress")
            assert page.get_by_role(
                "heading", name="Progress", exact=True,
            ).is_visible()
            # The "Community · Books" eyebrow and reader-count header stat.
            assert page.get_by_text("Community · Books").is_visible()
            assert page.locator(
                '[data-testid="progress-reader-count"]',
            ).is_visible()
            assert "3 readers reading along" in page.locator(
                '[data-testid="progress-reader-count"]',
            ).inner_text()

            own_row = page.locator(f'[data-testid="progress-row-{me}"]')
            assert own_row.get_attribute("aria-current") == "true"
            assert "(you)" in own_row.inner_text()
            assert "2 of 5" in own_row.inner_text()

            # Fast reader (4) appears above the slow reader (1).
            rows = page.locator('[data-testid^="progress-row-"]')
            order = [
                rows.nth(i).get_attribute("data-testid")
                for i in range(rows.count())
            ]
            assert order[0] == f"progress-row-{me}"
            assert order.index(f"progress-row-{fast}") < order.index(
                f"progress-row-{slow}",
            )
        finally:
            context.close()

    def test_reading_more_advances_own_count(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _mark_read("main@test.com", SLUG, [0])
        me = _user_pk("main@test.com")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            # Mark two more chapters read on the detail roadmap.
            page.goto(
                f"{django_server}/books/{SLUG}",
                wait_until="domcontentloaded",
            )
            for _ in range(2):
                page.locator(
                    '[data-testid="book-chapter-mark-read"]',
                ).first.click()
                page.wait_for_load_state("domcontentloaded")

            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            own_row = page.locator(f'[data-testid="progress-row-{me}"]')
            assert "3 of 5" in own_row.inner_text()
        finally:
            context.close()

    def test_first_reader_is_welcomed(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        me = _user_pk("main@test.com")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="progress-first-mover"]',
            ).is_visible()
            assert "0 of 5" in page.locator(
                f'[data-testid="progress-row-{me}"]',
            ).inner_text()
            assert page.locator('[data-testid="progress-empty"]').count() == 0
        finally:
            context.close()

    def test_below_tier_member_sees_gate_not_roster(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5, required_level=20)
        _create_user("main@test.com", tier_slug="main")
        _create_user("free@test.com", tier_slug="free")
        _mark_read("main@test.com", SLUG, [0, 1])

        context = _auth_context(browser, "free@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-guest-gate"]').wait_for(
                state="visible",
            )
            assert page.locator('[data-testid="progress-board"]').count() == 0
            assert page.locator(
                '[data-testid^="progress-row-"]',
            ).count() == 0
        finally:
            context.close()

    def test_anonymous_cannot_read_roster(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5, required_level=20)
        _create_user("main@test.com", tier_slug="main")
        _mark_read("main@test.com", SLUG, [0, 1])

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="book-guest-gate"]',
            ).is_visible()
            assert page.locator(
                '[data-testid^="progress-row-"]',
            ).count() == 0
        finally:
            context.close()

    def test_board_is_calm_no_gamification(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("reader-fast@test.com", tier_slug="main")
        _mark_read("main@test.com", SLUG, [0, 1])
        _mark_read("reader-fast@test.com", SLUG, [0, 1, 2, 3])
        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            body = page.locator("main").inner_text().lower()
            for banned in ("leaderboard", "ranked", "climb", "points", "streak"):
                assert banned not in body
            assert page.locator('[data-lucide="flame"]').count() == 0
        finally:
            context.close()

    def test_default_public_reader_name_links_to_profile(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("reader-fast@test.com", tier_slug="main")
        _mark_read("main@test.com", SLUG, [0])
        _mark_read("reader-fast@test.com", SLUG, [0, 1])
        other = _user_pk("reader-fast@test.com")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            name = page.locator(f'[data-testid="progress-name-{other}"]')
            assert name.is_visible()
            # The name is now an anchor into the reader-profile route.
            assert name.evaluate("el => el.tagName").lower() == "a"
            assert name.get_attribute("href").endswith(f"/readers/{other}")
            # Clicking it opens that reader's profile.
            name.click()
            page.wait_for_load_state("domcontentloaded")
            assert page.url.endswith(f"/books/{SLUG}/readers/{other}")
        finally:
            context.close()

    def test_private_notes_reader_is_named_locked_and_has_progress_profile(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("hidden@test.com", tier_slug="main")
        _mark_read("main@test.com", SLUG, [0])
        _mark_read("hidden@test.com", SLUG, [0, 1, 2, 3])
        _create_note("hidden@test.com", 0, "Private batching note one.")
        _create_note("hidden@test.com", 1, "Private batching note two.")
        _set_visibility("hidden@test.com", "private")
        hidden = _user_pk("hidden@test.com")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            row = page.locator(f'[data-testid="progress-row-{hidden}"]')
            assert row.is_visible()
            assert "4 of 5" in row.inner_text()
            notes = page.locator(f'[data-testid="progress-notes-{hidden}"]')
            assert "2" in notes.inner_text()
            assert notes.get_by_text("Notes private", exact=True).count() == 1
            assert notes.locator('[data-lucide="lock"]').count() == 1
            assert "2 readers reading along" in page.locator(
                '[data-testid="progress-reader-count"]',
            ).inner_text()
            page.locator(f'[data-testid="progress-name-{hidden}"]').click()
            page.wait_for_load_state("domcontentloaded")
            assert page.url.endswith(f"/books/{SLUG}/readers/{hidden}")
            assert page.locator(
                '[data-testid="reader-progress-strip"]',
            ).is_visible()
            assert "4" in page.locator(
                '[data-testid="reader-chapters-read"]',
            ).inner_text()
            assert "2" in page.locator(
                '[data-testid="reader-notes-shared"]',
            ).inner_text()
            assert page.get_by_text("Notes are private", exact=True).is_visible()
            body = page.locator("main").inner_text()
            assert "Private batching note one" not in body
            assert "Private batching note two" not in body
        finally:
            context.close()

    def test_non_member_never_sees_board_link(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5, required_level=20)
        _create_user("free@test.com", tier_slug="free")

        context = _auth_context(browser, "free@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                '[data-testid="book-progress-link"]',
            ).count() == 0
        finally:
            context.close()

    def test_draft_board_is_404_for_non_staff(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="draft-book", chapters=3, status="draft")
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            response = page.goto(
                f"{django_server}/books/draft-book/progress",
                wait_until="domcontentloaded",
            )
            assert response.status == 404
        finally:
            context.close()
