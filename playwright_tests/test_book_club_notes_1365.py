"""Playwright E2E for Book Club chapter notes + comments (issue #1365).

Exercises the rendered per-chapter reader page:

1. A member writes a note in the composer; it appears in the "Your note" card
   (once — #1461 removed the group-feed duplicate) and an edit updates it in
   place.
2. A second member comments on the first member's note via the shared thread;
   the comment appears under that note and the author gets a bell notification.
3. Chapter rows on the book page are real links to the chapter page.
4. A free member is tier-gated: header + gate, no composer.
5. #1461: the own note renders once, the notes count keeps counting it in one
   place, the own-note card hosts its own comment thread and the API hint, a
   heavily formatted note does not outrank the page, and chapter 0's bottom
   nav offers a route back to the roadmap.

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

            # #1461: the own-note card is the single self surface — the note
            # is NOT repeated in the group feed below.
            assert page.locator('[data-testid="note-body"]').count() == 0

            # Edit updates the same note in place.
            page.locator('[data-testid="own-note-edit"]').click()
            page.wait_for_load_state("domcontentloaded")
            body = page.locator('[data-testid="own-note-body"]')
            body.fill("Batching plus the KV cache is the whole game.")
            page.locator('[data-testid="own-note-post"]').click()
            page.wait_for_load_state("domcontentloaded")

            written = page.locator('[data-testid="own-note-written"]')
            assert "Batching plus the KV cache" in written.inner_text()
            # Still exactly one copy of my note on the page.
            assert page.locator('[data-testid="own-note-written"]').count() == 1
            assert page.locator('[data-testid="note-body"]').count() == 0
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


# ---------------------------------------------------------------------------
# Issue #1461: the own-note card is the single self surface, and the chapter
# page's hierarchy (notes count, API hint, prose scale, nav ends) is fixed.
# ---------------------------------------------------------------------------

PHONE = {"width": 393, "height": 851}
CHAPTER_URL = "/books/inference-engineering/chapters"


@pytest.mark.django_db(transaction=True)
class TestOwnNoteRendersOnce:
    def test_own_note_renders_once_and_the_count_stays_in_one_place(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")
        _create_user("author@test.com", tier_slug="main")
        _set_public("author@test.com")
        _create_note("main@test.com", "inference-engineering", 0,
                     "My own takeaway line.")
        _create_note("author@test.com", "inference-engineering", 0,
                     "Their takeaway line.")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}{CHAPTER_URL}/0",
                wait_until="domcontentloaded",
            )
            own = page.locator('[data-testid="own-note-written"]')
            own.wait_for(state="visible")
            assert "My own takeaway line." in own.inner_text()
            assert page.inner_text("body").count("My own takeaway line.") == 1

            feed = page.locator('[data-testid="note-body"]')
            assert feed.count() == 1
            assert "Their takeaway line." in feed.first.inner_text()

            # Both notes counted, printed once, in the Notes section header.
            assert page.inner_text("body").count("2 notes") == 1
            meta = page.locator("h1").first.locator(
                "xpath=following-sibling::div[1]",
            ).text_content()
            assert "note" not in (meta or "").lower()
        finally:
            context.close()

    def test_member_comments_on_their_own_note_inside_the_card(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")
        _create_note("main@test.com", "inference-engineering", 0,
                     "My own takeaway line.")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}{CHAPTER_URL}/0",
                wait_until="domcontentloaded",
            )
            card = page.locator('[data-testid="own-note-written"]')
            card.wait_for(state="visible")
            card.locator(".qa-new-question").fill("Following up on my own note.")
            card.locator(".qa-post-btn").click()
            page.wait_for_function(
                "document.querySelector('.qa-thread .qa-list')"
                " && document.querySelector('.qa-thread .qa-list').textContent"
                ".includes('Following up on my own note')"
            )

            page.reload(wait_until="domcontentloaded")
            page.wait_for_function(
                "document.querySelector('.qa-thread .qa-list')"
                " && document.querySelector('.qa-thread .qa-list').textContent"
                ".includes('Following up on my own note')"
            )
            assert page.inner_text("body").count("My own takeaway line.") == 1
        finally:
            context.close()

    def test_first_note_flow_keeps_the_api_hint_inside_the_card(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}{CHAPTER_URL}/1",
                wait_until="domcontentloaded",
            )
            composer = page.locator('[data-testid="own-note-composer"]')
            composer.wait_for(state="visible")
            assert composer.locator(
                '[data-testid="own-note-api-hint"]',
            ).count() == 1

            page.locator('[data-testid="own-note-body"]').fill(
                "Prerequisites are the whole game."
            )
            page.locator('[data-testid="own-note-post"]').click()
            page.wait_for_load_state("domcontentloaded")

            written = page.locator('[data-testid="own-note-written"]')
            written.wait_for(state="visible")
            assert written.locator(
                '[data-testid="own-note-api-hint"]',
            ).count() == 1
            assert page.inner_text("body").count(
                "Prerequisites are the whole game.",
            ) == 1
            assert page.locator('[data-testid="note-body"]').count() == 0
        finally:
            context.close()

    def test_formatted_note_does_not_outrank_the_page(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")
        _create_user("author@test.com", tier_slug="main")
        _set_public("author@test.com")
        _create_note(
            "author@test.com", "inference-engineering", 0,
            "## Their loud heading\n\nA paragraph of context.\n\n"
            "```\n" + ("x" * 400) + "\n```",
        )

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}{CHAPTER_URL}/0",
                wait_until="domcontentloaded",
            )
            note_heading = page.locator(
                '[data-testid="note-body"] :is(h1, h2, h3)',
            ).first
            note_heading.wait_for(state="visible")
            section_heading = page.get_by_role(
                "heading", name="Notes", exact=True,
            ).first
            note_size = note_heading.evaluate(
                "el => parseFloat(getComputedStyle(el).fontSize)",
            )
            section_size = section_heading.evaluate(
                "el => parseFloat(getComputedStyle(el).fontSize)",
            )
            assert section_size >= note_size, (section_size, note_size)

            page.set_viewport_size(PHONE)
            pre = page.locator('[data-testid="note-body"] pre').first
            assert pre.evaluate(
                "el => el.scrollWidth > el.clientWidth"
                " && getComputedStyle(el).overflowX === 'auto'",
            )
            assert page.evaluate(
                "() => document.documentElement.scrollWidth"
                " <= document.documentElement.clientWidth + 1",
            )
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestChapterNavigationEnds:
    def test_first_chapter_offers_a_route_back_to_the_roadmap(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}{CHAPTER_URL}/0",
                wait_until="domcontentloaded",
            )
            back = page.locator('[data-testid="chapter-back-to-book"]')
            back.wait_for(state="visible")
            assert page.locator('[data-testid="chapter-prev"]').count() == 0
            back.click()
            page.wait_for_url("**/books/inference-engineering")

            # The top back link from chapter 1 lands in the same place.
            page.goto(
                f"{django_server}{CHAPTER_URL}/1",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="chapter-back-link"]').click()
            page.wait_for_url("**/books/inference-engineering")
        finally:
            context.close()
