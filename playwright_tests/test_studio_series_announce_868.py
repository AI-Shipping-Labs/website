"""Playwright E2E tests for series-level Notify / Post to Slack (issue #868).

Scenarios:
1. Staff announces a whole series to Slack in one click (success state).
2. Staff notifies subscribers; an eligible member sees the bell/notification
   deep-linking to the public series page.
3. Staff re-notifies the same series — the 24h guard fires, no duplicates.
4. Staff announces a series with no upcoming sessions — clear "no upcoming
   sessions" message, no success claimed.
5. A free member who cannot access any (paid) session is not notified.
6. A subscriber follows the notification link to the public series page and
   sees the "Register for all upcoming sessions" panel.

Usage:
    uv run pytest playwright_tests/test_studio_series_announce_868.py -v
"""

import os
from datetime import datetime, timedelta
from unittest.mock import patch

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

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _clear_state():
    from events.models import (
        Event,
        EventRegistration,
        EventSeries,
        SeriesRegistration,
    )
    from notifications.models import Notification

    Notification.objects.all().delete()
    SeriesRegistration.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_series(slug, name, occurrences, *, paid_positions=()):
    from django.utils import timezone

    from content.access import LEVEL_BASIC, LEVEL_OPEN
    from events.models import Event, EventSeries

    series = EventSeries(
        name=name,
        slug=slug,
        description="Weekly shipping sessions.",
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    for i in range(1, occurrences + 1):
        level = LEVEL_BASIC if i in paid_positions else LEVEL_OPEN
        Event(
            title=f"{name} — Session {i}",
            slug=f"{slug}-session-{i}",
            start_datetime=timezone.now() + timedelta(days=7 * i),
            end_datetime=timezone.now() + timedelta(days=7 * i, hours=1),
            status="upcoming",
            origin="studio",
            required_level=level,
            event_series=series,
            series_position=i,
        ).save()
    connection.close()
    return series


def _make_past_only_series(slug, name):
    from django.utils import timezone

    from content.access import LEVEL_OPEN
    from events.models import Event, EventSeries

    series = EventSeries(
        name=name,
        slug=slug,
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    Event(
        title=f"{name} — Past Session",
        slug=f"{slug}-past",
        start_datetime=timezone.now() - timedelta(days=7),
        end_datetime=timezone.now() - timedelta(days=7, hours=-1),
        status="completed",
        origin="studio",
        required_level=LEVEL_OPEN,
        event_series=series,
        series_position=1,
    ).save()
    connection.close()
    return series


@pytest.mark.django_db(transaction=True)
class TestScenarioSlackOneClick:
    def test_announce_whole_series_to_slack(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-868a@test.com")
        series = _make_series("series-868a", "Build Club A", 3)

        ctx = _auth_context(browser, "admin-868a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()

        # The in-process server shares this process, so patching the
        # view-level symbol intercepts the real chat.postMessage transport
        # (Slack is not configured in the test env).
        with patch(
            "studio.views.event_series.post_series_slack_announcement",
            return_value=True,
        ):
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator('[data-testid="event-series-announce-slack"]').click()
            status = page.locator('[data-testid="series-slack-status"]')
            status.wait_for(state="visible", timeout=10000)
            page.wait_for_function(
                """() => {
                    var el = document.querySelector('[data-testid="series-slack-status"]');
                    return el && el.textContent.length > 0;
                }""",
                timeout=10000,
            )
            text = status.inner_text()

        assert "posted successfully" in text
        assert "Error" not in text
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioNotifySubscribers:
    def test_notify_lands_in_member_bell(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-868b@test.com")
        _create_user("member-868b@test.com", tier_slug="free")
        series = _make_series("series-868b", "Build Club B", 2)

        staff_ctx = _auth_context(browser, "admin-868b@test.com")
        staff_page = staff_ctx.new_page()
        staff_page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        staff_page.once("dialog", lambda dialog: dialog.accept())
        staff_page.locator('[data-testid="event-series-notify"]').click()
        status = staff_page.locator('[data-testid="series-notify-status"]')
        status.wait_for(state="visible", timeout=10000)
        staff_page.wait_for_function(
            """() => {
                var el = document.querySelector('[data-testid="series-notify-status"]');
                return el && el.textContent.includes('Notified');
            }""",
            timeout=10000,
        )
        assert "Notified" in status.inner_text()
        staff_ctx.close()

        # The eligible member sees one notification deep-linking to the
        # public series page.
        user_ctx = _auth_context(browser, "member-868b@test.com")
        user_page = user_ctx.new_page()
        user_page.goto(f"{django_server}/", wait_until="domcontentloaded")
        badge = user_page.locator("#notification-badge")
        badge.wait_for(state="visible", timeout=10000)

        user_page.locator("#notification-bell-btn").click()
        dropdown = user_page.locator("#notification-dropdown")
        dropdown.wait_for(state="visible", timeout=5000)
        user_page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )
        assert "New event series: Build Club B" in dropdown.inner_text()
        link = user_page.locator(
            f'#notification-list a[href="{series.get_absolute_url()}"]',
        )
        assert link.count() >= 1
        assert "/events/groups/" not in dropdown.inner_html()
        user_ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioReNotifyGuard:
    def test_second_notify_is_deduped(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-868c@test.com")
        _create_user("member-868c@test.com", tier_slug="free")
        series = _make_series("series-868c", "Build Club C", 2)

        ctx = _auth_context(browser, "admin-868c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        notify_btn = page.locator('[data-testid="event-series-notify"]')
        page.once("dialog", lambda dialog: dialog.accept())
        notify_btn.click()
        status = page.locator('[data-testid="series-notify-status"]')
        status.wait_for(state="visible", timeout=10000)
        page.wait_for_function(
            """() => {
                var el = document.querySelector('[data-testid="series-notify-status"]');
                return el && el.textContent.includes('Notified');
            }""",
            timeout=10000,
        )

        page.once("dialog", lambda dialog: dialog.accept())
        notify_btn.click()
        page.wait_for_function(
            """() => {
                var el = document.querySelector('[data-testid="series-notify-status"]');
                return el && el.textContent.includes('Already notified');
            }""",
            timeout=10000,
        )
        assert "Already notified" in status.inner_text()

        from notifications.models import Notification
        connection.close()
        assert Notification.objects.filter(
            title="New event series: Build Club C",
            user__email="member-868c@test.com",
        ).count() == 1
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioEmptySeries:
    def test_no_upcoming_sessions_message(self, django_server, browser):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-868d@test.com")
        series = _make_past_only_series("series-868d", "Build Club D")

        ctx = _auth_context(browser, "admin-868d@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.get_by_label("More actions").click()
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="event-series-announce-slack"]').click()
        status = page.locator('[data-testid="series-slack-status"]')
        status.wait_for(state="visible", timeout=10000)
        page.wait_for_function(
            """() => {
                var el = document.querySelector('[data-testid="series-slack-status"]');
                return el && el.textContent.length > 0;
            }""",
            timeout=10000,
        )
        text = status.inner_text()
        assert "No upcoming sessions" in text
        assert "posted successfully" not in text
        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioTierGatedMember:
    def test_free_member_not_notified_for_paid_only_series(
        self, django_server, browser,
    ):
        _ensure_tiers()
        _clear_state()
        _create_staff_user("admin-868e@test.com")
        _create_user("free-868e@test.com", tier_slug="free")
        # Every upcoming session requires basic; free member cannot attend.
        series = _make_series(
            "series-868e", "Build Club E", 2, paid_positions=(1, 2),
        )

        ctx = _auth_context(browser, "admin-868e@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator('[data-testid="event-series-notify"]').click()
        status = page.locator('[data-testid="series-notify-status"]')
        status.wait_for(state="visible", timeout=10000)
        page.wait_for_function(
            """() => {
                var el = document.querySelector('[data-testid="series-notify-status"]');
                return el && el.textContent.includes('Notified');
            }""",
            timeout=10000,
        )
        ctx.close()

        from notifications.models import Notification
        connection.close()
        assert not Notification.objects.filter(
            title="New event series: Build Club E",
            user__email="free-868e@test.com",
        ).exists()


@pytest.mark.django_db(transaction=True)
class TestScenarioFollowLinkToRegister:
    def test_notification_link_lands_on_register_panel(
        self, django_server, browser,
    ):
        from notifications.models import Notification

        _ensure_tiers()
        _clear_state()
        user = _create_user("member-868f@test.com", tier_slug="main")
        series = _make_series("series-868f", "Build Club F", 3)

        Notification.objects.create(
            user=user,
            title=f"New event series: {series.name}",
            body="Weekly shipping sessions.",
            url=series.get_absolute_url(),
            notification_type="new_content",
            read=False,
        )
        connection.close()

        ctx = _auth_context(browser, "member-868f@test.com")
        page = ctx.new_page()
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        page.locator("#notification-bell-btn").click()
        dropdown = page.locator("#notification-dropdown")
        dropdown.wait_for(state="visible", timeout=5000)
        page.wait_for_function(
            """() => {
                var list = document.getElementById('notification-list');
                return list && !list.textContent.includes('Loading');
            }""",
            timeout=10000,
        )
        link = page.locator(
            f'#notification-list a[href="{series.get_absolute_url()}"]',
        )
        assert link.count() >= 1
        link.first.click()

        page.wait_for_url(f"**{series.get_absolute_url()}**", timeout=10000)
        assert page.locator(
            '[data-testid="series-register-panel"]'
        ).is_visible()
        ctx.close()
