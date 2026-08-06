"""Playwright E2E for the Book Club foundation (issue #1362).

Covers the user-facing flows that cross pages and matter for the story:

1. A guest hits the Books gate and the upgrade CTA lands on /pricing.
2. A Free member is still gated on a Main book.
3. A Main member sees the participation body.
4. A draft book is not publicly reachable (404).
5. Staff create a book from Studio and build its chapter roadmap, and a
   duplicate chapter number is rejected with a friendly error.

Model-level rules (gating matrix, API, seed idempotency) are covered faster
as Django TestCase modules; these tests exercise the rendered flows.

Usage:
    uv run pytest playwright_tests/test_book_club_1362.py -v
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

pytestmark = pytest.mark.local_only

FIXED_KICKOFF_DATE = datetime.date(2026, 8, 10)


def _clear_books():
    from bookclub.models import Book

    Book.objects.all().delete()
    connection.close()


def _create_book(*, slug, status="current", required_level=20, title="Inference Engineering"):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title=title, slug=slug, author="Philip Kiely",
        required_level=required_level, status=status,
        start_date=FIXED_KICKOFF_DATE,
        description="The technologies behind every AI product in production.",
    )
    Chapter.objects.create(book=book, number=0, title="Inference", week_label="Week 1")
    Chapter.objects.create(book=book, number=1, title="Prerequisites", week_label="Week 2")
    connection.close()
    return book


@pytest.mark.django_db(transaction=True)
class TestBookGate:
    def test_guest_is_gated_and_upgrade_cta_lands_on_pricing(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering")

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            # Header renders publicly.
            assert "Inference Engineering" in page.locator("h1").inner_text()
            # Exactly one gated card; no participation body.
            gate = page.locator('[data-testid="book-guest-gate"]')
            gate.wait_for(state="visible")
            assert page.locator(
                '[data-testid="book-participation-body"]',
            ).count() == 0
            # Follow the upgrade CTA -> /pricing.
            page.locator('[data-testid="book-guest-gate-cta"]').first.click()
            page.wait_for_url(f"{django_server}/pricing")
        finally:
            context.close()

    def test_free_member_is_still_gated(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering")
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
                '[data-testid="book-participation-body"]',
            ).count() == 0
        finally:
            context.close()

    def test_main_member_sees_participation_body(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering")
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            body = page.locator('[data-testid="book-participation-body"]')
            body.wait_for(state="visible")
            assert "Ch. 0 — Inference" in body.inner_text()
            assert page.locator('[data-testid="book-guest-gate"]').count() == 0
        finally:
            context.close()

    def test_draft_book_is_not_publicly_reachable(self, django_server, browser):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="secret-draft", status="draft")

        context = browser.new_context()
        try:
            page = context.new_page()
            response = page.goto(
                f"{django_server}/books/secret-draft",
                wait_until="domcontentloaded",
            )
            assert response.status == 404
        finally:
            context.close()


@pytest.mark.django_db(transaction=True)
class TestStudioBookManagement:
    def test_staff_create_book_and_build_chapter_roadmap(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_staff_user("admin@test.com")

        context = _auth_context(browser, "admin@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/studio/books/new",
                wait_until="domcontentloaded",
            )
            page.fill('input[name="title"]', "Inference Engineering")
            page.fill('input[name="author"]', "Philip Kiely")
            page.select_option('[data-testid="book-required-level"]', "20")
            page.select_option('[data-testid="book-status"]', "current")
            # Dynamic future date so the fixture never rots (see date-rot guard).
            start_date = (
                datetime.date.today() + datetime.timedelta(days=4)
            ).isoformat()
            page.fill('input[name="start_date"]', start_date)
            page.click('button[type="submit"][form="book-edit-form"]')

            # Lands on the book detail page with the auto-derived slug.
            page.wait_for_url("**/studio/books/**/")
            assert "inference-engineering" in page.locator(
                '[data-testid="book-detail-slug"]',
            ).inner_text()

            # Add chapter 0.
            page.fill('[data-testid="book-chapter-add-number"]', "0")
            page.fill('[data-testid="book-chapter-add-title"]', "Inference")
            page.fill('[data-testid="book-chapter-add-deadline"]', "2026-08-17")
            page.click('[data-testid="book-chapter-add-submit"]')
            page.wait_for_selector('[data-testid="book-chapter-row"]')

            # Add chapter 1.
            page.fill('[data-testid="book-chapter-add-number"]', "1")
            page.fill('[data-testid="book-chapter-add-title"]', "Prerequisites")
            page.click('[data-testid="book-chapter-add-submit"]')
            page.wait_for_function(
                "document.querySelectorAll('[data-testid=\\'book-chapter-row\\']').length === 2",
            )

            rows = page.locator('[data-testid="book-chapter-row"]')
            assert rows.count() == 2

            # Duplicate chapter number is rejected with a friendly error.
            page.fill('[data-testid="book-chapter-add-number"]', "0")
            page.fill('[data-testid="book-chapter-add-title"]', "Duplicate")
            page.click('[data-testid="book-chapter-add-submit"]')
            page.wait_for_selector('[data-testid="book-chapter-error"]')
            assert "already has a chapter numbered 0" in page.locator(
                '[data-testid="book-chapter-error"]',
            ).inner_text()
            # Still only two rows — no duplicate created.
            assert page.locator('[data-testid="book-chapter-row"]').count() == 2
        finally:
            context.close()
