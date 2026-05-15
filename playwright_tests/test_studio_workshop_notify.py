"""
Playwright E2E tests for the workshop notification flow (issue #647).

Scenarios:
1. Staff announces a new workshop and confirms subscribers were notified —
   the "Notify subscribers" button creates a Notification row that lands
   in the bell dropdown for a non-staff user with the right tier.
2. Staff cannot re-notify the same workshop within 24 hours — the second
   click surfaces the 409 "Already notified" warning.
3. Draft workshop hides the notify controls from staff — the buttons only
   render when ``workshop.status == 'published'``.
4. Member discovers a new workshop via the bell — clicking the
   notification deep-links to ``/workshops/<slug>``, marks read, and
   decrements the badge.

Notification fan-out by tier is covered by the Django tests in
``notifications/tests/test_service.py``; these scenarios only exercise
the JS-driven Studio click flow and the user-visible bell dropdown.
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


def _clear_state():
    """Reset workshops + notifications between scenarios."""
    from content.models import Workshop, WorkshopPage
    from events.models import Event
    from notifications.models import EventReminderLog, Notification

    Notification.objects.all().delete()
    EventReminderLog.objects.all().delete()
    WorkshopPage.objects.all().delete()
    Workshop.objects.all().delete()
    Event.objects.filter(kind='workshop').delete()
    connection.close()


def _create_workshop(
    slug='build-a-rag-app',
    title='Build a RAG App',
    status='published',
    landing=0,
    pages=10,
    recording=20,
    date=None,
):
    """Create a Workshop row for the scenario under test."""
    from content.models import Workshop

    workshop = Workshop.objects.create(
        slug=slug,
        title=title,
        date=date or datetime.date(2026, 4, 21),
        description='Hands-on intro to RAG.',
        tags=['agents'],
        status=status,
        landing_required_level=landing,
        pages_required_level=pages,
        recording_required_level=recording,
    )
    connection.close()
    return workshop


# ---------------------------------------------------------------
# Scenario 1: Staff announces a new workshop
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopNotifyFlow:
    def test_notify_creates_notifications_visible_in_bell(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='build-a-rag-app', title='Build a RAG App',
            status='published', landing=0, pages=10, recording=20,
        )
        # Three non-staff active users with various tiers (landing=0 covers
        # all of them).
        _create_user('free-user@test.com', tier_slug='free')
        _create_user('basic-user@test.com', tier_slug='basic')
        _create_user('main-user@test.com', tier_slug='main')

        # --- Staff clicks "Notify subscribers" in Studio ---
        staff_ctx = _auth_context(browser, 'admin@test.com')
        staff_page = staff_ctx.new_page()

        staff_page.goto(
            f'{django_server}/studio/workshops/{workshop.pk}/edit',
            wait_until='domcontentloaded',
        )

        # The notification panel buttons are visible on a published workshop.
        notify_btn = staff_page.locator('#notify-subscribers-btn')
        slack_btn = staff_page.locator('#post-to-slack-btn')
        assert notify_btn.count() == 1
        assert slack_btn.count() == 1

        notify_btn.click()

        # Wait for the status line to appear with the success message.
        status = staff_page.locator('#notify-status')
        status.wait_for(state='visible', timeout=10000)
        status_text = status.inner_text()
        assert 'Notified' in status_text

        staff_ctx.close()

        # --- A non-staff user sees the notification in the bell ---
        # Pick one of the three active users.
        user_ctx = _auth_context(browser, 'free-user@test.com')
        user_page = user_ctx.new_page()
        user_page.goto(
            f'{django_server}/',
            wait_until='domcontentloaded',
        )

        badge = user_page.locator('#notification-badge')
        badge.wait_for(state='visible', timeout=10000)
        # Exactly one new workshop notification for this user.
        assert badge.inner_text() == '1'

        # Open the bell dropdown and verify the notification.
        user_page.locator('#notification-bell-btn').click()
        dropdown = user_page.locator('#notification-dropdown')
        dropdown.wait_for(state='visible', timeout=5000)

        user_page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        dropdown_text = dropdown.inner_text()
        assert 'New workshop: Build a RAG App' in dropdown_text

        # The link points at the workshop landing page.
        link = user_page.locator(
            '#notification-list a[href="/workshops/build-a-rag-app"]',
        )
        assert link.count() >= 1

        user_ctx.close()


# ---------------------------------------------------------------
# Scenario 2: 24h re-notify guard
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopNotifyDoubleGuard:
    def test_second_notify_within_24h_shows_warning(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='build-a-rag-app', title='Build a RAG App',
            status='published', landing=0, pages=10, recording=20,
        )
        # At least one active user so the first notify creates a row.
        _create_user('free-user@test.com', tier_slug='free')

        ctx = _auth_context(browser, 'admin@test.com')
        page = ctx.new_page()

        page.goto(
            f'{django_server}/studio/workshops/{workshop.pk}/edit',
            wait_until='domcontentloaded',
        )

        notify_btn = page.locator('#notify-subscribers-btn')
        notify_btn.click()

        # First click should land a success message.
        status = page.locator('#notify-status')
        status.wait_for(state='visible', timeout=10000)
        page.wait_for_function(
            """() => {
                var el = document.getElementById('notify-status');
                return el && el.textContent.includes('Notified');
            }""",
            timeout=10000,
        )

        # Second click — same workshop, same 24h window.
        notify_btn.click()

        # Wait until the message switches to the duplicate warning. The
        # button is re-enabled between requests so we re-fetch the status
        # text once the new fetch resolves.
        page.wait_for_function(
            """() => {
                var el = document.getElementById('notify-status');
                return el && el.textContent.includes('Already notified');
            }""",
            timeout=10000,
        )
        assert 'Already notified in the last 24 hours' in status.inner_text()

        # No second Notification row was created — count stayed at one per
        # eligible user (only ``free-user@test.com`` is eligible at level 0
        # in this test once duplicates are excluded).
        from notifications.models import Notification
        connection.close()
        unique_titles = set(
            Notification.objects
            .filter(title='New workshop: Build a RAG App')
            .values_list('user__email', flat=True),
        )
        # The free user is the only non-staff eligible recipient; staff also
        # gets a row because they're active. The point is no duplicate per
        # user.
        assert 'free-user@test.com' in unique_titles
        assert Notification.objects.filter(
            title='New workshop: Build a RAG App',
            user__email='free-user@test.com',
        ).count() == 1

        ctx.close()


# ---------------------------------------------------------------
# Scenario 3: Draft workshop hides the notify controls
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestStudioWorkshopNotifyDraftHidden:
    def test_draft_workshop_hides_notify_controls(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_state()
        _create_staff_user('admin@test.com')
        workshop = _create_workshop(
            slug='draft-workshop', title='Draft Workshop',
            status='draft', landing=0, pages=10, recording=20,
        )

        ctx = _auth_context(browser, 'admin@test.com')
        page = ctx.new_page()

        page.goto(
            f'{django_server}/studio/workshops/{workshop.pk}/edit',
            wait_until='domcontentloaded',
        )

        # The form still renders, but the notification panel does not.
        assert page.locator(
            '[data-testid="workshop-edit-form"]',
        ).count() == 1
        assert page.locator('#notify-subscribers-btn').count() == 0
        assert page.locator('#post-to-slack-btn').count() == 0

        # Defense-in-depth: full body content has neither string.
        body = page.content()
        assert 'Notify subscribers' not in body
        assert 'Post to Slack' not in body

        ctx.close()


# ---------------------------------------------------------------
# Scenario 4: Member discovers a new workshop via the bell
# ---------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
class TestMemberDiscoversWorkshopViaBell:
    def test_clicking_workshop_notification_lands_on_landing(
        self, django_server, browser,
    ):
        from notifications.models import Notification

        _ensure_tiers()
        _clear_state()
        # A Main-tier user is the audience for a basic-landing workshop.
        user = _create_user('main@test.com', tier_slug='main')
        workshop = _create_workshop(
            slug='gated-rag', title='Gated RAG Workshop',
            status='published', landing=10, pages=10, recording=20,
        )

        # Simulate the row that staff's "Notify subscribers" click would
        # create — keeps this scenario focused on the bell-to-landing path
        # rather than re-testing the staff flow.
        Notification.objects.create(
            user=user,
            title=f'New workshop: {workshop.title}',
            body='Hands-on intro to RAG.',
            url=workshop.get_absolute_url(),
            notification_type='new_content',
            read=False,
        )
        connection.close()

        ctx = _auth_context(browser, 'main@test.com')
        page = ctx.new_page()
        page.goto(f'{django_server}/', wait_until='domcontentloaded')

        badge = page.locator('#notification-badge')
        badge.wait_for(state='visible', timeout=10000)
        assert badge.inner_text() == '1'

        page.locator('#notification-bell-btn').click()
        dropdown = page.locator('#notification-dropdown')
        dropdown.wait_for(state='visible', timeout=5000)

        page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )

        # Click the workshop notification — landing page is /workshops/<slug>.
        link = page.locator(
            '#notification-list a[href="/workshops/gated-rag"]',
        )
        assert link.count() >= 1
        link.first.click()

        page.wait_for_url('**/workshops/gated-rag**', timeout=10000)
        assert '/workshops/gated-rag' in page.url

        # Reload home and confirm the badge decremented (or hid).
        page.goto(f'{django_server}/', wait_until='domcontentloaded')
        page.wait_for_function(
            """() => {
                var b = document.getElementById('notification-badge');
                return b && (b.classList.contains('hidden') || b.innerText === '0');
            }""",
            timeout=10000,
        )

        ctx.close()
