"""Playwright E2E for the Book Club public reading surfaces (issue #1363).

Eight BDD-style user journeys across the hub, the status-branched detail, and
the lighter secondary pages, layered on the #1362/#1364 foundation:

1. A visitor discovers the Book Club from the Community nav.
2. A guest opens the current book, hits the gate, and the Upgrade CTA lands on
   /membership with no participation body.
3. A Main member reads the current book's roadmap (This week callout + chapter
   rows) with no gate and no broken links.
4. A visitor browses upcoming/past and opens a finished book (secondary page,
   past-tone badge, finished recap, no dead links).
5. A visitor opens an upcoming book and sees when it starts (no fake notify).
6. The hub stays graceful with nothing being read (empty state).
7. A draft book is hidden from the public but staff can preview it.
8. A member returns to the hub from a book detail via the Books back-link.

Model-level rules (grouping, branching, current-chapter derivation, nav
placement) are covered faster as Django TestCase modules; these tests exercise
the rendered flows end to end.

Usage:
    uv run pytest playwright_tests/test_book_club_hub_1363.py -v
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

FIXED_KICKOFF = datetime.date(2026, 8, 10)
FUTURE_KICKOFF = datetime.date(2026, 12, 1)
PAST_KICKOFF = datetime.date(2026, 2, 1)


def _clear_books():
    from bookclub.models import Book

    Book.objects.all().delete()
    connection.close()


def _create_book(
    *, slug, status="current", required_level=20, title="Inference Engineering",
    start_date=FIXED_KICKOFF, with_chapters=False,
):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title=title, slug=slug, author="Philip Kiely",
        required_level=required_level, status=status,
        start_date=start_date, meeting_cadence="Weekly · 17:00 CET",
        description="The technologies behind every AI product in production.",
    )
    if with_chapters:
        today = datetime.date.today()
        Chapter.objects.create(
            book=book, number=0, title="Inference", week_label="Week 1",
            deadline=today - datetime.timedelta(days=7),
        )
        Chapter.objects.create(
            book=book, number=1, title="Prerequisites", week_label="Week 2",
            deadline=today + datetime.timedelta(days=3),
        )
    connection.close()
    return book


@pytest.mark.django_db(transaction=True)
class TestBookClubHubJourneys:
    def test_visitor_discovers_book_club_from_community_nav(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering")

        context = browser.new_context(viewport={"width": 1280, "height": 800})
        try:
            page = context.new_page()
            page.goto(f"{django_server}/", wait_until="domcontentloaded")

            page.get_by_test_id("nav-community-trigger").hover()
            menu = page.get_by_test_id("nav-community-menu")
            menu.wait_for(state="visible")

            link_ids = menu.evaluate(
                "el => [...el.querySelectorAll('a[data-testid]')]"
                ".map(a => a.getAttribute('data-testid'))"
            )
            # Books sits immediately after Community Sprints.
            assert link_ids.index("nav-community-link-books") == (
                link_ids.index("nav-community-link-sprints") + 1
            )

            page.get_by_test_id("nav-community-link-books").click()
            page.wait_for_url(f"{django_server}/books")
            assert "Read together, ship together" in page.locator("h1").inner_text()
            page.locator('[data-testid="featured-book-card"]').wait_for(state="visible")
        finally:
            context.close()

    def test_guest_gate_on_current_book_and_upgrade_lands_on_pricing(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering", with_chapters=True)

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(f"{django_server}/books", wait_until="domcontentloaded")
            page.locator('[data-testid="featured-book-card"]').click()
            page.wait_for_url(f"{django_server}/books/inference-engineering")

            page.locator('[data-testid="book-guest-gate"]').wait_for(state="visible")
            assert page.locator(
                '[data-testid="book-participation-body"]',
            ).count() == 0

            page.locator('[data-testid="book-guest-gate-cta"]').first.click()
            page.wait_for_url(f"{django_server}/membership")
        finally:
            context.close()

    def test_main_member_reads_current_book_roadmap(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering", with_chapters=True)
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

            week = page.locator('[data-testid="book-this-week"]')
            week.wait_for(state="visible")
            # The derived current chapter is the one whose deadline is still ahead.
            assert "Ch. 1 — Prerequisites" in week.inner_text()
            assert "read by" in week.inner_text()

            assert "Ch. 0 — Inference" in body.inner_text()
            assert page.locator('[data-testid="book-guest-gate"]').count() == 0
        finally:
            context.close()

    def test_visitor_browses_and_opens_finished_book(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering", status="current")
        _create_book(
            slug="designing-ml", status="upcoming", title="Designing ML Systems",
            start_date=FUTURE_KICKOFF,
        )
        _create_book(
            slug="pragmatic", status="finished", title="The Pragmatic Programmer",
            start_date=PAST_KICKOFF, with_chapters=True,
        )

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(f"{django_server}/books", wait_until="domcontentloaded")
            assert page.locator('[data-testid="upcoming-book-card"]').count() == 1
            assert page.locator('[data-testid="past-book-card"]').count() == 1

            page.locator('[data-testid="past-book-card"]').click()
            page.wait_for_url(f"{django_server}/books/pragmatic")

            page.locator('[data-testid="book-status-badge"]').wait_for(state="visible")
            assert page.locator('[data-testid="book-finished-recap"]').count() == 1
            # The full-book summary route (#1368) is built, so the finished recap
            # links to it. Standings/progress route is still unbuilt — no link.
            summary_cta = page.locator('[data-testid="book-finished-summary-cta"]')
            assert summary_cta.count() == 1
            assert "/books/pragmatic/summary" in summary_cta.get_attribute("href")
            page.goto(
                f"{django_server}/books/pragmatic/summary",
                wait_until="domcontentloaded",
            )
            assert page.locator('[data-testid="summary-back-link"]').count() == 1
            page.go_back(wait_until="domcontentloaded")
            assert page.locator('a[href*="/progress"]').count() == 0
            # No participation body — the group is not reading it now.
            assert page.locator('[data-testid="book-participation-body"]').count() == 0
        finally:
            context.close()

    def test_visitor_opens_upcoming_book_and_sees_kickoff(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(
            slug="designing-ml", status="upcoming", title="Designing ML Systems",
            start_date=FUTURE_KICKOFF,
        )

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/designing-ml",
                wait_until="domcontentloaded",
            )
            callout = page.locator('[data-testid="book-upcoming-callout"]')
            callout.wait_for(state="visible")
            assert "Starts" in callout.inner_text()
            # No fake GET-state "Get notified" control ships in this issue.
            assert callout.locator('input[name="notify"]').count() == 0
            assert callout.get_by_role("button", name="Get notified").count() == 0
        finally:
            context.close()

    def test_hub_stays_graceful_with_nothing_being_read(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(
            slug="pragmatic", status="finished", title="The Pragmatic Programmer",
            start_date=PAST_KICKOFF,
        )

        context = browser.new_context()
        try:
            page = context.new_page()
            page.goto(f"{django_server}/books", wait_until="domcontentloaded")
            assert page.locator('[data-testid="featured-book-card"]').count() == 0
            body = page.locator("main").inner_text()
            assert "No book in progress right now" in body
            # Header and How it works still render.
            assert "Read together, ship together" in page.locator("h1").inner_text()
            assert "How it works" in body
        finally:
            context.close()

    def test_draft_hidden_from_public_but_staff_can_preview(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="secret-draft", status="draft", title="Secret Draft")

        # Anonymous: not on hub, 404 on detail.
        anon = browser.new_context()
        try:
            page = anon.new_page()
            page.goto(f"{django_server}/books", wait_until="domcontentloaded")
            assert "Secret Draft" not in page.locator("main").inner_text()
            response = page.goto(
                f"{django_server}/books/secret-draft",
                wait_until="domcontentloaded",
            )
            assert response.status == 404
        finally:
            anon.close()

        # Staff: draft shows on the hub and detail returns 200.
        _create_staff_user("admin@test.com")
        staff = _auth_context(browser, "admin@test.com")
        try:
            page = staff.new_page()
            page.goto(f"{django_server}/books", wait_until="domcontentloaded")
            assert page.locator('[data-testid="draft-book-card"]').count() == 1
            assert "Secret Draft" in page.locator("main").inner_text()
            response = page.goto(
                f"{django_server}/books/secret-draft",
                wait_until="domcontentloaded",
            )
            assert response.status == 200
        finally:
            staff.close()

    def test_member_returns_to_hub_from_detail_back_link(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_books()
        _create_book(slug="inference-engineering", with_chapters=True)
        _create_user("main@test.com", tier_slug="main")

        context = _auth_context(browser, "main@test.com")
        try:
            page = context.new_page()
            page.goto(
                f"{django_server}/books/inference-engineering",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-back-link"]').click()
            page.wait_for_url(f"{django_server}/books")
            page.locator('[data-testid="featured-book-card"]').wait_for(state="visible")
        finally:
            context.close()
