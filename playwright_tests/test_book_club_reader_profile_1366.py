"""Playwright E2E for the Book Club reading-profile page (issue #1366).

Exercises the rendered profile surface at /books/<slug>/readers/<user_id> and
the visibility mechanism it owns:

1. A member opens a public reader's profile from the progress board, sees the
   Public badge, the two stats, the progress strip, and the reader's notes,
   then comments on a note via the shared thread.
2. An owner opts their profile public with the toggle and then appears as a
   named, linked row on the board to a second member.
3. A free member is tier-gated out of a public profile: header + gate, no notes.

Model-level rules (helper partition, owner-scoped toggle 403/400, member API)
are covered faster by Django TestCase modules.

No hard-coded near-2026 dates: the profile page never renders a kickoff date,
so the book is created without one (date-rot guard clean).

Usage:
    uv run pytest playwright_tests/test_book_club_reader_profile_1366.py -v
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


def _create_book(*, required_level=20, chapters=5):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title="Inference Engineering", slug=SLUG, author="Philip Kiely",
        required_level=required_level, status="current",
        description="The technologies behind every AI product in production.",
    )
    for i in range(chapters):
        Chapter.objects.create(book=book, number=i, title=f"Chapter {i}")
    connection.close()
    return book


def _mark_read(email, numbers):
    from accounts.models import User
    from bookclub.models import Book, ChapterRead

    user = User.objects.get(email=email)
    book = Book.objects.get(slug=SLUG)
    for number in numbers:
        ChapterRead.objects.get_or_create(
            user=user, chapter=book.chapters.get(number=number),
        )
    connection.close()


def _create_note(email, number, body):
    from accounts.models import User
    from bookclub.models import Book, Note

    user = User.objects.get(email=email)
    chapter = Book.objects.get(slug=SLUG).chapters.get(number=number)
    Note.objects.create(chapter=chapter, user=user, body=body)
    connection.close()


def _set_visibility(email, visibility):
    from accounts.models import User
    from bookclub.models import ReaderProfile

    user = User.objects.get(email=email)
    ReaderProfile.objects.update_or_create(
        user=user, defaults={"visibility": visibility},
    )
    connection.close()


def _user_pk(email):
    from accounts.models import User

    pk = User.objects.get(email=email).pk
    connection.close()
    return pk


@pytest.mark.django_db(transaction=True)
class TestReaderProfile:
    def test_open_public_profile_from_board_and_comment(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("reader-b@test.com", tier_slug="main")
        _mark_read("reader-b@test.com", [0, 1, 2])
        _create_note("reader-b@test.com", 0, "The KV cache is the whole game.")
        _create_note("reader-b@test.com", 1, "Batching amortizes the load.")
        _set_visibility("reader-b@test.com", "public")
        _set_visibility("main@test.com", "public")
        _mark_read("main@test.com", [0])
        b_id = _user_pk("reader-b@test.com")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            page.locator(f'[data-testid="progress-name-{b_id}"]').click()
            page.wait_for_load_state("domcontentloaded")

            assert page.url.endswith(f"/books/{SLUG}/readers/{b_id}")
            # The visibility badge is owner-only; a non-owner viewer never sees it.
            assert page.locator(
                '[data-testid="reader-visibility-badge"]',
            ).count() == 0
            assert "3" in page.locator(
                '[data-testid="reader-chapters-read"]',
            ).inner_text()
            assert "2" in page.locator(
                '[data-testid="reader-notes-shared"]',
            ).inner_text()
            assert page.locator(
                '[data-testid="reader-progress-strip"]',
            ).is_visible()
            feed = page.locator('[data-testid="reader-notes-feed"]')
            assert "KV cache is the whole game" in feed.inner_text()

            # Comment on one of B's notes via the shared composer.
            note_card = page.locator("article").filter(
                has_text="KV cache is the whole game"
            )
            note_card.locator(".qa-new-question").fill("Great point!")
            note_card.locator(".qa-post-btn").click()
            # Scope the wait to this note's own thread — the profile shows more
            # than one note card, each with its own comment thread.
            note_card.locator(".qa-list").get_by_text(
                "Great point!",
            ).wait_for(timeout=15000)
        finally:
            context.close()

    def test_owner_opts_public_and_appears_on_board(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5)
        _create_user("main@test.com", tier_slug="main")
        _create_user("second@test.com", tier_slug="main")
        _mark_read("main@test.com", [0, 1])
        _mark_read("second@test.com", [0])
        _set_visibility("second@test.com", "public")
        me = _user_pk("main@test.com")

        # main@ starts private (default) -> not named on the board to others.
        second_ctx = _auth_context(browser, "second@test.com")
        try:
            page = second_ctx.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            assert page.locator(
                f'[data-testid="progress-row-{me}"]',
            ).count() == 0
        finally:
            second_ctx.close()

        # Owner opens their own profile and flips to public.
        owner_ctx = _auth_context(browser, "main@test.com")
        try:
            page = owner_ctx.new_page()
            page.goto(f"{django_server}/books/{SLUG}", wait_until="domcontentloaded")
            page.locator('[data-testid="book-reader-profile-link"]').click()
            page.wait_for_load_state("domcontentloaded")
            assert page.url.endswith(f"/readers/{me}")
            assert "private" in page.locator(
                '[data-testid="reader-visibility-badge"]',
            ).inner_text().lower()
            page.locator('[data-testid="reader-make-public"]').click()
            page.wait_for_load_state("domcontentloaded")
            assert "public" in page.locator(
                '[data-testid="reader-visibility-badge"]',
            ).inner_text().lower()
        finally:
            owner_ctx.close()

        # Now the second member sees main@ as a named, linked row.
        second_ctx = _auth_context(browser, "second@test.com")
        try:
            page = second_ctx.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/progress",
                wait_until="domcontentloaded",
            )
            row = page.locator(f'[data-testid="progress-row-{me}"]')
            assert row.count() == 1
            name = page.locator(f'[data-testid="progress-name-{me}"]')
            assert name.get_attribute("href").endswith(f"/readers/{me}")
        finally:
            second_ctx.close()

    def test_free_member_is_gated_out_of_public_profile(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(chapters=5, required_level=20)
        _create_user("main@test.com", tier_slug="main")
        _create_user("free@test.com", tier_slug="free")
        _mark_read("main@test.com", [0, 1])
        _create_note("main@test.com", 0, "Members-only insight.")
        _set_visibility("main@test.com", "public")
        target = _user_pk("main@test.com")

        context = _auth_context(browser, "free@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/{SLUG}/readers/{target}",
                wait_until="domcontentloaded",
            )
            # Public header renders (name), then a single gated-access card.
            # The visibility badge is owner-only, so it is absent here.
            assert page.get_by_role("heading", level=1).is_visible()
            assert page.locator(
                '[data-testid="reader-visibility-badge"]',
            ).count() == 0
            assert page.locator('[data-testid="book-guest-gate"]').is_visible()
            # No notes feed leaks below the gate.
            assert page.locator(
                '[data-testid="reader-notes-feed"]',
            ).count() == 0
            assert "Members-only insight" not in page.locator("main").inner_text()
        finally:
            context.close()
