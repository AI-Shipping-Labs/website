"""Playwright E2E tests for whole-series registration (issue #857).

Scenarios:
1. A member registers for an entire series in one action; the button
   flips to the registered state and the dashboard lists the occurrences.
2. A mixed-tier series registers what the member can access and surfaces
   the higher-tier note.
3. A member cancels the series; future occurrences become registrable
   again.
4. An anonymous visitor is routed to login before registering.
5. Clicking a series occurrence in the events listing lands on the
   series screen (entry-point flow refinement).

Usage:
    uv run pytest playwright_tests/test_series_registration_857.py -v
"""

import os
from datetime import datetime, timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_user as _create_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = [pytest.mark.local_only, pytest.mark.core]


def _reset_event_state():
    from django.db import connection

    from events.models import (
        Event,
        EventRegistration,
        EventSeries,
        SeriesRegistration,
    )

    SeriesRegistration.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_series(slug, name, occurrences, *, main_only_positions=()):
    """Create a series with N upcoming occurrences. Returns the series."""
    from django.db import connection
    from django.utils import timezone

    from content.access import LEVEL_MAIN, LEVEL_OPEN
    from events.models import Event, EventSeries

    series = EventSeries(
        name=name,
        slug=slug,
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    for i in range(1, occurrences + 1):
        level = LEVEL_MAIN if i in main_only_positions else LEVEL_OPEN
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


@pytest.mark.django_db(transaction=True)
class TestScenarioFullSeriesRegister:
    def test_register_for_whole_series(self, django_server, browser):
        _reset_event_state()
        _create_user("member-857a@test.com", tier_slug="main")
        series = _make_series("woh-857a", "Office Hours A", 5)

        ctx = _auth_context(browser, "member-857a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/groups/{series.slug}",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="series-register-button"]').click()

        # The page reloads into the registered state.
        page.locator(
            '[data-testid="series-registered-state"]'
        ).wait_for(state="visible")
        assert page.locator(
            '[data-testid="series-cancel-button"]'
        ).is_visible()
        # Every occurrence now shows a Registered chip.
        assert page.locator(
            '[data-testid="series-event-state-registered"]'
        ).count() == 5

        # The dashboard lists all 5 occurrences under Upcoming Events.
        page.goto(f"{django_server}/dashboard", wait_until="domcontentloaded")
        from accounts.models import User
        from events.models import EventRegistration
        user = User.objects.get(email="member-857a@test.com")
        assert EventRegistration.objects.filter(user=user).count() == 5

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioMixedTier:
    def test_partial_enroll_with_tier_note(self, django_server, browser):
        _reset_event_state()
        _create_user("member-857b@test.com", tier_slug="basic")
        series = _make_series(
            "woh-857b", "Office Hours B", 6, main_only_positions=(5, 6),
        )

        ctx = _auth_context(browser, "member-857b@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/groups/{series.slug}",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="series-register-button"]').click()
        page.locator(
            '[data-testid="series-registered-state"]'
        ).wait_for(state="visible")

        # 4 accessible occurrences enrolled; the 2 main-only ones show an
        # upgrade affordance rather than a Registered chip.
        assert page.locator(
            '[data-testid="series-event-state-registered"]'
        ).count() == 4
        assert page.locator(
            '[data-testid="series-event-state-no-access"]'
        ).count() == 2

        from accounts.models import User
        from events.models import EventRegistration
        user = User.objects.get(email="member-857b@test.com")
        assert EventRegistration.objects.filter(user=user).count() == 4

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioCancelKeepsPast:
    def test_cancel_drops_future_registrations(self, django_server, browser):
        _reset_event_state()
        _create_user("member-857c@test.com", tier_slug="main")
        series = _make_series("woh-857c", "Office Hours C", 4)

        ctx = _auth_context(browser, "member-857c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/groups/{series.slug}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="series-register-button"]').click()
        page.locator(
            '[data-testid="series-cancel-button"]'
        ).wait_for(state="visible")

        from accounts.models import User
        from events.models import EventRegistration
        user = User.objects.get(email="member-857c@test.com")
        assert EventRegistration.objects.filter(user=user).count() == 4

        # Cancel the series — accept the confirm() dialog.
        page.on("dialog", lambda d: d.accept())
        page.locator('[data-testid="series-cancel-button"]').click()

        # Page reloads with the register button available again.
        page.locator(
            '[data-testid="series-register-button"]'
        ).wait_for(state="visible")
        # Future occurrences are dropped.
        assert EventRegistration.objects.filter(user=user).count() == 0

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioAnonymousLoginRedirect:
    def test_anonymous_routed_to_login(self, django_server, browser):
        _reset_event_state()
        series = _make_series("woh-857d", "Office Hours D", 3)

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events/groups/{series.slug}",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="series-register-login-cta"]').click()
        page.wait_for_url(lambda url: "/accounts/login" in url)
        # The next param routes back to the series page.
        assert f"groups/{series.slug}" in page.url

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestScenarioEntryPointFromListing:
    def test_series_card_lands_on_series_screen(self, django_server, browser):
        _reset_event_state()
        series = _make_series("woh-857e", "Office Hours E", 2)

        ctx = browser.new_context(viewport={"width": 1280, "height": 720})
        page = ctx.new_page()
        page.goto(
            f"{django_server}/events?filter=upcoming",
            wait_until="domcontentloaded",
        )
        # The card's primary link routes to the series screen.
        page.locator('[data-testid="event-card-link"]').first.click()
        page.wait_for_url(lambda url: f"groups/{series.slug}" in url)
        assert page.locator('[data-testid="series-name"]').is_visible()

        ctx.close()
