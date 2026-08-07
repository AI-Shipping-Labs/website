"""Playwright E2E for Book Club summaries (issue #1368).

Exercises the rendered surfaces:

1. A staff organizer publishes a chapter summary in Studio; a member then reads
   the published card on the chapter page. Unpublishing hides the body again.
2. A member seeding a chapter draft from everyone's notes via the Studio
   "Pull all notes" button.
3. The full-book /summary page: Overall + By-chapter index for a member, a row
   linking to the chapter #summary anchor, and a free member tier-gated out.

Publish-state model rules and the admin API are covered faster by Django
TestCase / API modules.

Usage:
    uv run pytest playwright_tests/test_book_club_summaries_1368.py -v
"""

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

pytestmark = pytest.mark.local_only

SLUG = "inference-engineering"


def _reset_books():
    from bookclub.models import Book

    Book.objects.all().delete()
    connection.close()


def _create_book(chapters=3):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title="Inference Engineering", slug=SLUG, author="Philip Kiely",
        required_level=20, status="current",
        description="The technologies behind every AI product in production.",
    )
    for i in range(chapters):
        Chapter.objects.create(
            book=book, number=i, title=f"Chapter {i}", week_label=f"Week {i + 1}",
        )
    connection.close()
    return book


def _book_pk():
    from bookclub.models import Book

    pk = Book.objects.get(slug=SLUG).pk
    connection.close()
    return pk


def _create_note(email, number, body):
    from accounts.models import User
    from bookclub.models import Book, Note

    user = User.objects.get(email=email)
    chapter = Book.objects.get(slug=SLUG).chapters.get(number=number)
    Note.objects.create(chapter=chapter, user=user, body=body)
    connection.close()


def _publish_overall(body):
    from django.utils import timezone

    from bookclub.models import Book

    book = Book.objects.get(slug=SLUG)
    book.summary = body
    book.summary_published_at = timezone.now()
    book.save()
    connection.close()


def _publish_chapter(number, body):
    from django.utils import timezone

    from bookclub.models import Book

    chapter = Book.objects.get(slug=SLUG).chapters.get(number=number)
    chapter.summary = body
    chapter.summary_published_at = timezone.now()
    chapter.save()
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestChapterSummaryStudioFlow:
    def test_organizer_publishes_chapter_summary_member_reads_it(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        book_pk = _book_pk()

        # Staff publishes the Ch. 0 summary from the Studio detail page.
        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            page.goto(
                f"{django_server}/studio/books/{book_pk}/",
                wait_until="domcontentloaded",
            )
            row = page.locator('[data-testid="book-chapter-row"]').first
            row.locator('[data-testid="book-chapter-summary"]').fill(
                "The KV cache framing was the key idea for the group."
            )
            row.locator('[data-testid="book-chapter-summary-published"]').check()
            row.locator('[data-testid="book-chapter-save"]').click()
            page.wait_for_load_state("domcontentloaded")
        finally:
            staff_ctx.close()

        # The member sees the published card on the chapter page.
        member_ctx = _auth_context(browser, "main@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/chapters/0",
                wait_until="domcontentloaded",
            )
            card = page.locator('[data-testid="chapter-summary"]')
            assert card.is_visible()
            assert "KV cache framing was the key idea" in card.inner_text()
            assert (
                page.locator('[data-testid="chapter-summary-draft-chip"]').count()
                == 0
            )
        finally:
            member_ctx.close()

        # Unpublish in Studio; the member no longer sees the body.
        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            page.goto(
                f"{django_server}/studio/books/{book_pk}/",
                wait_until="domcontentloaded",
            )
            row = page.locator('[data-testid="book-chapter-row"]').first
            row.locator('[data-testid="book-chapter-summary-published"]').uncheck()
            row.locator('[data-testid="book-chapter-save"]').click()
            page.wait_for_load_state("domcontentloaded")
        finally:
            staff_ctx.close()

        member_ctx = _auth_context(browser, "main@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/chapters/0",
                wait_until="domcontentloaded",
            )
            assert "KV cache framing was the key idea" not in page.content()
        finally:
            member_ctx.close()

    def test_pull_all_notes_seeds_draft(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("ada@test.com", tier_slug="main", first_name="Ada")
        _create_user("lin@test.com", tier_slug="main", first_name="Lin")
        _create_note("ada@test.com", 0, "Ada takeaway body.")
        _create_note("lin@test.com", 0, "Lin takeaway body.")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            page.goto(
                f"{django_server}/studio/books/{book_pk}/",
                wait_until="domcontentloaded",
            )
            row = page.locator('[data-testid="book-chapter-row"]').first
            row.locator('[data-testid="book-chapter-pull-notes"]').click()
            page.wait_for_load_state("domcontentloaded")

            # The digest seeded the chapter 0 draft textarea.
            row = page.locator('[data-testid="book-chapter-row"]').first
            draft = row.locator('[data-testid="book-chapter-summary"]').input_value()
            assert "- Ada: Ada takeaway body." in draft
            assert "- Lin: Lin takeaway body." in draft
        finally:
            staff_ctx.close()


@pytest.mark.django_db(transaction=True)
class TestFullBookSummaryPage:
    def test_member_reads_overall_and_by_chapter(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("main@test.com", tier_slug="main")
        _publish_overall("The group treated the material as a system.")
        _publish_chapter(0, "Ch0 takeaway line for the index.")
        _publish_chapter(1, "Ch1 takeaway line for the index.")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/summary",
                wait_until="domcontentloaded",
            )
            overall = page.locator('[data-testid="summary-overall"]')
            assert overall.is_visible()
            assert "treated the material as a system" in overall.inner_text()

            by_chapter = page.locator('[data-testid="summary-by-chapter"]')
            assert by_chapter.is_visible()
            rows = page.locator('[data-testid="summary-chapter-row"]')
            assert rows.count() == 2

            # A row lands on the chapter #summary anchor.
            rows.first.click()
            page.wait_for_load_state("domcontentloaded")
            assert "/chapters/0" in page.url
            assert "#summary" in page.url
        finally:
            context.close()

    def test_free_member_is_gated(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_user("free@test.com", tier_slug="free")
        _publish_overall("The group treated the material as a system.")

        context = _auth_context(browser, "free@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/summary",
                wait_until="domcontentloaded",
            )
            assert page.locator('[data-testid="book-guest-gate"]').is_visible()
            assert "treated the material as a system" not in page.content()
            assert page.locator('[data-testid="summary-overall"]').count() == 0
        finally:
            context.close()
