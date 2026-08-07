"""Playwright E2E for Book Club summary-publish notifications (issue #1374).

Exercises the member-facing outcomes of publishing a Book Club summary:

1. An organizer publishes a chapter summary in Studio; a member sees the bell
   notification on /notifications and clicking it lands on the chapter
   #summary section.
2. Publishing the full-book summary notifies the member with a link to
   /books/<slug>/summary.
3. Editing an already-published summary does not spam members again.
4. Republishing after an unpublish re-announces.
5. A below-tier (Free) member is not notified.
6. A member who opts out of Book Club emails still sees the bell but receives
   no email (verified via the EmailLog).
7. Publishing a summary on a draft book notifies nobody.
8. Admin API publish is observable via GET /api/notifications and is
   idempotent on re-PATCH.

Publish-transition + audience rules are covered faster by the Django TestCase
suite; this file asserts the rendered member surfaces.

Usage:
    uv run pytest playwright_tests/test_book_club_summary_notifications_1374.py -v
"""

import os

import pytest
import requests

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
CHAPTER_TITLE = "New chapter summary: Inference Engineering (Ch. 0)"
CHAPTER1_TITLE = "New chapter summary: Inference Engineering (Ch. 1)"
BOOK_TITLE = "Book summary published: Inference Engineering"


def _reset_books():
    from bookclub.models import Book
    from notifications.models import Notification

    Book.objects.all().delete()
    Notification.objects.filter(notification_type="bookclub_summary").delete()
    connection.close()


def _create_book(status="current", chapters=3):
    from bookclub.models import Book, Chapter

    book = Book.objects.create(
        title="Inference Engineering", slug=SLUG, author="Philip Kiely",
        required_level=20, status=status,
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


def _admin_token(email):
    from accounts.models import Token, User

    user = User.objects.get(email=email)
    token = Token.objects.create(user=user, name="e2e-admin")
    key = token.key
    connection.close()
    return key


def _notification_count(email, title):
    from accounts.models import User
    from notifications.models import Notification

    user = User.objects.get(email=email)
    count = Notification.objects.filter(
        user=user, notification_type="bookclub_summary", title=title,
    ).count()
    connection.close()
    return count


def _bookclub_email_count(email):
    from accounts.models import User
    from email_app.models import EmailLog

    user = User.objects.get(email=email)
    count = EmailLog.objects.filter(
        user=user,
        email_type__in=("bookclub_chapter_summary", "bookclub_book_summary"),
    ).count()
    connection.close()
    return count


def _publish_chapter_in_studio(page, django_server, book_pk, row_index, body):
    """Fill + publish a chapter summary from the Studio book detail page."""
    page.goto(
        f"{django_server}/studio/books/{book_pk}/",
        wait_until="domcontentloaded",
    )
    row = page.locator('[data-testid="book-chapter-row"]').nth(row_index)
    row.locator('[data-testid="book-chapter-summary"]').fill(body)
    checkbox = row.locator('[data-testid="book-chapter-summary-published"]')
    if not checkbox.is_checked():
        checkbox.check()
    row.locator('[data-testid="book-chapter-save"]').click()
    page.wait_for_load_state("domcontentloaded")


def _unpublish_chapter_in_studio(page, django_server, book_pk, row_index):
    page.goto(
        f"{django_server}/studio/books/{book_pk}/",
        wait_until="domcontentloaded",
    )
    row = page.locator('[data-testid="book-chapter-row"]').nth(row_index)
    row.locator('[data-testid="book-chapter-summary-published"]').uncheck()
    row.locator('[data-testid="book-chapter-save"]').click()
    page.wait_for_load_state("domcontentloaded")


@pytest.mark.django_db(transaction=True)
class TestBellNotifications:
    def test_member_alerted_on_chapter_summary_publish(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            _publish_chapter_in_studio(
                page, django_server, book_pk, 0,
                "The KV cache framing was the takeaway.",
            )
        finally:
            staff_ctx.close()

        member_ctx = _auth_context(browser, "main@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/notifications", wait_until="domcontentloaded",
            )
            link = page.get_by_role("link", name=CHAPTER_TITLE, exact=True)
            assert link.count() == 1
            # The row JS marks the notification read via fetch, then sets
            # window.location, so wait for the deep-link navigation.
            link.first.click()
            page.wait_for_url(f"**/books/{SLUG}/chapters/0#summary")
            assert f"/books/{SLUG}/chapters/0" in page.url
            assert "#summary" in page.url
            card = page.locator('[data-testid="chapter-summary"]')
            assert card.is_visible()
        finally:
            member_ctx.close()

    def test_member_alerted_on_full_book_summary(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            page.goto(
                f"{django_server}/studio/books/{book_pk}/edit",
                wait_until="domcontentloaded",
            )
            page.locator('[data-testid="book-summary"]').fill(
                "The group treated the material as one system."
            )
            page.locator('[data-testid="book-summary-published"]').check()
            page.locator('[data-testid="sticky-save-action"]').click()
            page.wait_for_load_state("domcontentloaded")
        finally:
            staff_ctx.close()

        member_ctx = _auth_context(browser, "main@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/notifications", wait_until="domcontentloaded",
            )
            link = page.get_by_role("link", name=BOOK_TITLE, exact=True)
            assert link.count() == 1
            # The row JS marks the notification read via fetch, then sets
            # window.location, so wait for the deep-link navigation.
            link.first.click()
            page.wait_for_url(f"**/books/{SLUG}/summary")
            assert page.url.rstrip("/").endswith(f"/books/{SLUG}/summary")
            assert page.locator('[data-testid="summary-overall"]').is_visible()
        finally:
            member_ctx.close()

    def test_editing_published_summary_does_not_spam(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            _publish_chapter_in_studio(
                page, django_server, book_pk, 0, "Original body.",
            )
            assert _notification_count("main@test.com", CHAPTER_TITLE) == 1
            # Edit the body, still published -> no re-fire.
            _publish_chapter_in_studio(
                page, django_server, book_pk, 0, "Edited body, still live.",
            )
        finally:
            staff_ctx.close()

        assert _notification_count("main@test.com", CHAPTER_TITLE) == 1

    def test_republish_after_unpublish_reannounces(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            _publish_chapter_in_studio(
                page, django_server, book_pk, 1, "Ch1 body v1.",
            )
            assert _notification_count("main@test.com", CHAPTER1_TITLE) == 1
            _unpublish_chapter_in_studio(page, django_server, book_pk, 1)
            assert _notification_count("main@test.com", CHAPTER1_TITLE) == 1
            _publish_chapter_in_studio(
                page, django_server, book_pk, 1, "Ch1 body v2.",
            )
        finally:
            staff_ctx.close()

        assert _notification_count("main@test.com", CHAPTER1_TITLE) == 2

    def test_free_member_not_notified(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("free@test.com", tier_slug="free")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            _publish_chapter_in_studio(
                page, django_server, book_pk, 0, "A published body.",
            )
        finally:
            staff_ctx.close()

        member_ctx = _auth_context(browser, "free@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(
                f"{django_server}/notifications", wait_until="domcontentloaded",
            )
            assert page.get_by_role(
                "link", name=CHAPTER_TITLE, exact=True,
            ).count() == 0
        finally:
            member_ctx.close()
        assert _notification_count("free@test.com", CHAPTER_TITLE) == 0

    def test_draft_book_notifies_nobody(self, django_server, browser):
        _ensure_tiers()
        _reset_books()
        _create_book(status="draft")
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        book_pk = _book_pk()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            _publish_chapter_in_studio(
                page, django_server, book_pk, 0, "Draft-preview body.",
            )
        finally:
            staff_ctx.close()

        assert _notification_count("main@test.com", CHAPTER_TITLE) == 0


@pytest.mark.django_db(transaction=True)
class TestEmailOptOut:
    def test_opt_out_member_gets_bell_but_no_email(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        # opted_out toggles emails off; opted_in stays on to prove the send
        # path actually ran (so the opted-out zero is meaningful).
        _create_user("optout@test.com", tier_slug="main")
        _create_user("optin@test.com", tier_slug="main")
        book_pk = _book_pk()

        # Member turns Book Club summary emails off on the account page.
        member_ctx = _auth_context(browser, "optout@test.com")
        try:
            page = member_ctx.new_page()
            page.goto(f"{django_server}/account/", wait_until="domcontentloaded")
            toggle = page.locator('[data-testid="bookclub-emails-toggle"]')
            toggle.scroll_into_view_if_needed()
            toggle.click()
            page.wait_for_selector(
                '[data-testid="bookclub-emails-status"]:visible',
            )
        finally:
            member_ctx.close()

        staff_ctx = _auth_context(browser, "admin@test.com")
        try:
            page = staff_ctx.new_page()
            _publish_chapter_in_studio(
                page, django_server, book_pk, 0, "A published chapter body.",
            )
        finally:
            staff_ctx.close()

        # Opted-out member: bell present, email absent.
        assert _notification_count("optout@test.com", CHAPTER_TITLE) == 1
        assert _bookclub_email_count("optout@test.com") == 0
        # Opted-in member: email was sent, so the path genuinely ran.
        assert _bookclub_email_count("optin@test.com") == 1


@pytest.mark.django_db(transaction=True)
class TestAdminApi:
    def test_admin_publish_observable_and_idempotent(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _reset_books()
        _create_book()
        _create_staff_user("admin@test.com")
        _create_user("main@test.com", tier_slug="main")
        token = _admin_token("admin@test.com")

        headers = {"Authorization": f"Token {token}"}
        patch_url = f"{django_server}/api/books/{SLUG}/chapters/0"
        payload = {"summary": "Chapter takeaway.", "summary_published": True}

        resp = requests.patch(patch_url, json=payload, headers=headers, timeout=15)
        assert resp.status_code == 200

        # Observable via the member notifications API.
        member_ctx = _auth_context(browser, "main@test.com")
        try:
            page = member_ctx.new_page()
            api = page.request.get(f"{django_server}/api/notifications")
            body = api.json()
            titles = [n["title"] for n in body["notifications"]]
            assert CHAPTER_TITLE in titles
            urls = [
                n["url"] for n in body["notifications"]
                if n["title"] == CHAPTER_TITLE
            ]
            assert all("#summary" in url for url in urls)
        finally:
            member_ctx.close()

        # Idempotent re-PATCH: no additional notification.
        resp = requests.patch(patch_url, json=payload, headers=headers, timeout=15)
        assert resp.status_code == 200
        assert _notification_count("main@test.com", CHAPTER_TITLE) == 1
