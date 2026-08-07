"""Playwright E2E for Book Club chapter notes + comments (issue #1365).

Exercises the rendered per-chapter reader page:

1. A member writes a note in the composer; it appears in the "Your note" card
   and in the group feed flagged "You", and an edit updates it in place.
2. A second member comments on the first member's note via the shared thread;
   the comment appears under that note and the author gets a bell notification.
3. Chapter rows on the book page are real links to the chapter page.
4. A free member is tier-gated: header + gate, no composer.

Model-level rules (one-note-per-member, scope/tier gating, the member API,
the notification resolver) are covered faster by Django TestCase modules.

Usage:
    uv run pytest playwright_tests/test_book_club_notes_1365.py -v
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


def _reset_books():
    from bookclub.models import Book
    from notifications.models import Notification

    Notification.objects.all().delete()
    Book.objects.all().delete()
    connection.close()


def _create_book(*, slug="inference-engineering", required_level=20, chapters=3):
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


def _create_note(email, slug, number, body):
    from accounts.models import User
    from bookclub.models import Book, Note

    user = User.objects.get(email=email)
    chapter = Book.objects.get(slug=slug).chapters.get(number=number)
    note = Note.objects.create(chapter=chapter, user=user, body=body)
    content_id = str(note.comment_content_id)
    connection.close()
    return content_id


def _set_public(email):
    """Opt a reader into a public profile so others see their notes (#1366)."""
    from accounts.models import User
    from bookclub.models import ReaderProfile

    user = User.objects.get(email=email)
    ReaderProfile.objects.update_or_create(
        user=user, defaults={"visibility": "public"},
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestChapterNoteFlow:
    def test_member_writes_note_and_edits_in_place(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering/chapters/0",
                wait_until="domcontentloaded",
            )
            # Empty composer to start.
            assert page.locator('[data-testid="own-note-composer"]').is_visible()

            page.locator('[data-testid="own-note-body"]').fill(
                "The KV cache is the whole game."
            )
            page.locator('[data-testid="own-note-post"]').click()
            page.wait_for_load_state("domcontentloaded")

            # Saved in the "Your note" card.
            written = page.locator('[data-testid="own-note-written"]')
            assert written.is_visible()
            assert "KV cache is the whole game" in written.inner_text()

            # And in the group feed flagged "You" (exactly one).
            assert page.locator('[data-testid="note-you-chip"]').count() == 1

            # Edit updates the same note in place.
            page.locator('[data-testid="own-note-edit"]').click()
            page.wait_for_load_state("domcontentloaded")
            body = page.locator('[data-testid="own-note-body"]')
            body.fill("Batching plus the KV cache is the whole game.")
            page.locator('[data-testid="own-note-post"]').click()
            page.wait_for_load_state("domcontentloaded")

            written = page.locator('[data-testid="own-note-written"]')
            assert "Batching plus the KV cache" in written.inner_text()
            # Still exactly one of my notes in the feed.
            assert page.locator('[data-testid="note-you-chip"]').count() == 1
        finally:
            context.close()

    def test_member_comments_on_another_members_note(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("author@test.com", tier_slug="main")
        _create_user("reader@test.com", tier_slug="main")
        # The author opts public so their note appears in the reader's feed.
        _set_public("author@test.com")
        _create_note(
            "author@test.com", "inference-engineering", 0,
            "Speculative decoding is underrated.",
        )

        # Reader opens the chapter and comments on the author's note.
        reader_ctx = _auth_context(browser, "reader@test.com")
        try:
            page = reader_ctx.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering/chapters/0",
                wait_until="domcontentloaded",
            )
            note_card = page.locator("article").filter(
                has_text="Speculative decoding is underrated."
            )
            note_card.locator(".qa-new-question").fill("Totally agree — great point.")
            note_card.locator(".qa-post-btn").click()

            # The comment renders under that note's thread.
            page.wait_for_function(
                "document.querySelector('.qa-thread .qa-list')"
                " && document.querySelector('.qa-thread .qa-list').textContent"
                ".includes('Totally agree')"
            )
        finally:
            reader_ctx.close()

        # The note author gets a bell notification about their note.
        author_ctx = _auth_context(browser, "author@test.com")
        try:
            page = author_ctx.new_page()
            page.goto(
                f"{django_server}/notifications", wait_until="domcontentloaded",
            )
            assert "your note" in page.content().lower()
        finally:
            author_ctx.close()

    def test_chapter_rows_are_real_links(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            link = page.locator('[data-testid="book-chapter-link"]').first
            link.click()
            page.wait_for_load_state("domcontentloaded")
            assert "/chapters/0" in page.url
            assert page.locator('[data-testid="chapter-participation-body"]').is_visible()
        finally:
            context.close()

    def test_free_member_is_gated(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("free@test.com", tier_slug="free")

        context = _auth_context(browser, "free@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering/chapters/0",
                wait_until="domcontentloaded",
            )
            assert page.locator('[data-testid="book-guest-gate"]').is_visible()
            assert page.locator('[data-testid="own-note-composer"]').count() == 0
        finally:
            context.close()
