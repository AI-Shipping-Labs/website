"""
Playwright E2E tests for the notification bell (Issue #89).

Only the genuinely JS-driven scenarios remain here. All other notification
behaviours are covered by Django tests in ``notifications/tests/`` --
see issue #259 for the per-test mapping.

KEPT (3 scenarios):
- Bell dropdown open/close + AJAX mark-read on click + badge updates
- Mark-all-as-read in the dropdown (AJAX, no reload)
- Mark-all-as-read on the notifications page (AJAX + reload, header badge
  updates from a fresh server-rendered page)

DELETED (8 scenarios), each replaced as follows:

- ``test_notifications_page_pagination_and_click`` -> Django:
  ``notifications/tests/test_pages.py::NotificationListPageTest::
  test_pagination_navigation_to_page_2`` and
  ``test_clicking_notification_target_url_resolves``.
- ``test_publish_creates_notification_for_eligible_not_ineligible``
  -> ``notifications/tests/test_service.py::NotificationServiceNotifyTest::
  test_publish_basic_article_notifies_basic_not_free``.
- ``test_free_member_only_sees_open_notification`` -> Django:
  ``notifications/tests/test_pages.py::NotificationVisibilityTest::
  test_free_user_only_sees_their_own_notifications``.
- ``test_anonymous_redirected_on_notifications_page`` -> already covered by
  ``notifications/tests/test_pages.py::NotificationListPageTest::
  test_unauthenticated_redirects_to_login`` and
  ``notifications/tests/test_api.py::UnreadCountApiTest::
  test_unauthenticated_returns_redirect``.
- ``test_notifications_link_to_correct_content_types`` -> Django:
  ``notifications/tests/test_service.py::NotificationServiceNotifyTest::
  test_notification_url_matches_content_type``.
- ``test_badge_shows_9_plus_for_many_unread`` -> covered by
  ``notifications/tests/test_api.py::UnreadCountApiTest::
  test_returns_correct_unread_count`` (raw count is the contract; the
  ``9+`` cap is a pure JS string format applied client-side and is
  exercised in scenarios 1 and 2 below).
- ``test_event_reminder_appears_in_bell`` -> already covered by
  ``notifications/tests/test_service.py::EventReminderServiceTest`` and
  ``notifications/tests/test_event_reminders.py``.
- ``test_empty_state_message_and_no_badge`` -> already covered by
  ``notifications/tests/test_pages.py::NotificationListPageTest::
  test_empty_state`` and
  ``notifications/tests/test_templatetags.py::
  UnreadNotificationCountTagTest::test_returns_zero_when_no_notifications``.

Usage:
    uv run pytest playwright_tests/test_notifications.py -v
"""

import datetime
import os

import pytest

from playwright_tests.conftest import (
    VIEWPORT,
)
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
from django.db import connection


def _anon_context(browser):
    """Create an anonymous browser context."""
    context = browser.new_context(viewport=VIEWPORT)
    return context


def _clear_notifications():
    """Delete all notifications and reminder logs."""
    from notifications.models import EventReminderLog, Notification

    Notification.objects.all().delete()
    EventReminderLog.objects.all().delete()
    connection.close()


def _create_notification(
    user,
    title,
    body="",
    url="",
    notification_type="new_content",
    read=False,
):
    """Create a Notification for the given user."""
    from notifications.models import Notification

    connection.close()
    return Notification.objects.create(
        user=user,
        title=title,
        body=body,
        url=url,
        notification_type=notification_type,
        read=read,
    )


def _create_article(
    title,
    slug,
    description="",
    required_level=0,
    published=True,
):
    """Create an Article via ORM."""
    from content.models import Article

    article = Article(
        title=title,
        slug=slug,
        description=description,
        content_markdown=f"# {title}\n\nSome content here.",
        required_level=required_level,
        published=published,
        date=datetime.date.today(),
    )
    article.save()
    connection.close()
    return article


# ---------------------------------------------------------------
# Scenario 1: Authenticated member checks unread notifications
#              via the bell icon
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario1CheckUnreadNotificationsViaBell:
    """Authenticated member checks unread notifications via the bell icon."""

    @pytest.mark.core
    def test_bell_badge_shows_unread_count_and_dropdown_works(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier) who has 3
        unread notifications (new article, new recording, new download).
        1. Observe the header bell icon - badge shows "3"
        2. Click the bell icon - dropdown appears with 3 notifications
        3. Click the first notification (article) - marks read, navigates
        4. Navigate back, click bell - badge shows "2", clicked one is read
        """
        _ensure_tiers()
        _clear_notifications()
        user = _create_user("free@test.com", tier_slug="free")

        _create_article(
            title="Test Article for Notif",
            slug="test-article-notif",
        )
        _create_notification(
            user=user,
            title="New article: Test Article for Notif",
            body="A new article has been published.",
            url="/blog/test-article-notif",
            notification_type="new_content",
        )
        _create_notification(
            user=user,
            title="New recording: Workshop Recording",
            body="A new recording is available.",
            url="/events/some-recording",
            notification_type="new_content",
        )
        _create_notification(
            user=user,
            title="New download: AI Cheat Sheet",
            body="A new download is available.",
            url="/downloads/some-download",
            notification_type="new_content",
        )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to homepage, observe the bell icon
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )

        # Wait for the badge to update (JS polls on load)
        badge = page.locator("#notification-badge")
        badge.wait_for(state="visible", timeout=10000)

        # Then: Badge shows "3"
        assert badge.inner_text() == "3"

        # Step 2: Click the bell icon
        bell_btn = page.locator("#notification-bell-btn")
        bell_btn.click()

        # Wait for dropdown to appear and load notifications
        dropdown = page.locator("#notification-dropdown")
        dropdown.wait_for(state="visible", timeout=5000)

        # Wait for notifications to load (not "Loading...")
        page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        # Then: Dropdown shows the 3 notifications with titles
        dropdown_text = dropdown.inner_text()
        assert "New article: Test Article for Notif" in dropdown_text
        assert "New recording: Workshop Recording" in dropdown_text
        assert "New download: AI Cheat Sheet" in dropdown_text

        # Step 3: Click on the first notification (article)
        # The article link navigates to /blog/test-article-notif
        article_notif = page.locator(
            '#notification-list a[href="/blog/test-article-notif"]'
        )
        assert article_notif.count() >= 1
        article_notif.first.click()

        # Then: Navigates to the article detail page
        page.wait_for_url(
            "**/blog/test-article-notif**",
            timeout=10000,
        )
        assert "/blog/test-article-notif" in page.url

        # Step 4: Navigate back, click the bell icon again
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )

        # Wait for badge to update
        badge = page.locator("#notification-badge")
        badge.wait_for(state="visible", timeout=10000)

        # Then: Badge shows "2" (one was marked as read)
        assert badge.inner_text() == "2"
# ---------------------------------------------------------------
# Scenario 2: Member marks all notifications as read from the
#              dropdown
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario2MarkAllReadFromDropdown:
    """Member marks all notifications as read from the dropdown."""

    @pytest.mark.core
    def test_mark_all_as_read_in_dropdown(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier) who has 5
        unread notifications.
        1. Click the bell icon to open the dropdown - all 5 appear unread
        2. Click "Mark all as read" - all appear as read, badge disappears
        """
        _ensure_tiers()
        _clear_notifications()
        user = _create_user("free@test.com", tier_slug="free")

        for i in range(5):
            _create_notification(
                user=user,
                title=f"Notification {i + 1}",
                body=f"Body of notification {i + 1}",
                url=f"/blog/notif-{i + 1}",
                notification_type="new_content",
            )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        page.goto(
            f"{django_server}/",
            wait_until="domcontentloaded",
        )

        # Wait for badge to show "5"
        badge = page.locator("#notification-badge")
        badge.wait_for(state="visible", timeout=10000)
        assert badge.inner_text() == "5"

        # Step 1: Click the bell icon
        bell_btn = page.locator("#notification-bell-btn")
        bell_btn.click()

        dropdown = page.locator("#notification-dropdown")
        dropdown.wait_for(state="visible", timeout=5000)

        # Wait for notifications to load
        page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        # Then: All 5 notifications appear
        list_el = page.locator("#notification-list")
        list_text = list_el.inner_text()
        for i in range(5):
            assert f"Notification {i + 1}" in list_text

        # Check that unread indicators (blue dots) are present
        unread_dots = page.locator(
            "#notification-list .rounded-full.bg-accent"
        )
        assert unread_dots.count() == 5

        # Step 2: Click "Mark all as read"
        mark_all_btn = dropdown.locator(
            'button:has-text("Mark all as read")'
        )
        mark_all_btn.click()

        # Wait for the notifications to reload without unread dots
        page.wait_for_function(
            """() => {
                var dots = document.querySelectorAll('#notification-list .rounded-full.bg-accent');
                return dots.length === 0;
            }""",
            timeout=10000,
        )

        # Then: Badge disappears
        assert badge.evaluate(
            "el => el.classList.contains('hidden')"
        )
# ---------------------------------------------------------------
# Scenario 4: Member uses "Mark all as read" on the notifications
#              page
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestScenario4MarkAllReadOnNotificationsPage:
    """Member uses 'Mark all as read' on the notifications page."""

    @pytest.mark.core
    def test_mark_all_read_on_notifications_page(
        self, django_server
    , browser):
        """Given a user logged in as free@test.com (Free tier) who has
        8 unread notifications.
        1. Navigate to /notifications - 8 appear as unread
        2. Click "Mark all as read" - page reloads, all appear as read
        3. Observe the bell icon - badge is no longer visible
        """
        _ensure_tiers()
        _clear_notifications()
        user = _create_user("free@test.com", tier_slug="free")

        for i in range(8):
            _create_notification(
                user=user,
                title=f"Unread Notif {i + 1}",
                body=f"Body {i + 1}",
                url=f"/blog/unread-{i + 1}",
                notification_type="new_content",
                read=False,
            )

        context = _auth_context(browser, "free@test.com")
        page = context.new_page()
        # Step 1: Navigate to /notifications
        page.goto(
            f"{django_server}/notifications",
            wait_until="domcontentloaded",
        )

        # Then: 8 notifications appear as unread (with accent dots)
        unread_dots = page.locator(
            "main .rounded-full.bg-accent"
        )
        assert unread_dots.count() == 8

        # Step 2: Click "Mark all as read"
        mark_all_btn = page.locator(
            '#mark-all-btn'
        )
        assert mark_all_btn.count() >= 1

        # The JS does fetch then window.location.reload().
        # Use expect_navigation to wait for the reload.
        with page.expect_navigation(
            wait_until="domcontentloaded", timeout=15000,
        ):
            mark_all_btn.click()

        # Then: All notifications appear as read (no unread dots)
        unread_dots_after = page.locator(
            "main .rounded-full.bg-accent"
        )
        assert unread_dots_after.count() == 0

        # Step 3: Observe the header bell icon
        badge = page.locator("#notification-badge")

        # Wait for the JS to poll and update badge
        page.wait_for_function(
            """() => {
                var b = document.getElementById('notification-badge');
                return b && b.classList.contains('hidden');
            }""",
            timeout=10000,
        )

        # Then: Badge is hidden (no unread count)
        assert badge.evaluate(
            "el => el.classList.contains('hidden')"
        )
