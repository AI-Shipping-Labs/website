"""Playwright E2E for series subscriber calendar invites (issue #869).

SES is disabled in tests (``_send_raw_email`` returns a synthetic id), so
these scenarios assert the registration/enrollment flow and the
``EmailLog`` rows produced — the calendar-client behaviour (entries
added/updated/removed) is the ``[HUMAN]`` acceptance criterion. The exact
``.ics`` structure (VEVENT-per-occurrence, METHOD, SEQUENCE) is asserted
in the unit tests ``events/tests/test_series_invite.py``.

Usage:
    uv run pytest playwright_tests/test_series_calendar_invite_869.py -v
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

    from email_app.models import EmailLog
    from events.models import (
        Event,
        EventRegistration,
        EventSeries,
        SeriesRegistration,
    )

    EmailLog.objects.filter(
        email_type__in=(
            "series_registration",
            "series_update",
            "series_cancellation",
        ),
    ).delete()
    SeriesRegistration.objects.all().delete()
    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_series(slug, name, occurrences, *, main_only_positions=(),
                 premium_only_positions=()):
    from django.db import connection
    from django.utils import timezone

    from content.access import LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
    from events.models import Event, EventSeries

    series = EventSeries(
        name=name,
        slug=slug,
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        timezone="UTC",
    )
    series.save()
    for i in range(1, occurrences + 1):
        if i in premium_only_positions:
            level = LEVEL_PREMIUM
        elif i in main_only_positions:
            level = LEVEL_MAIN
        else:
            level = LEVEL_OPEN
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
class TestSeriesRegistrationLogsInvite:
    def test_register_logs_series_invite_email(self, django_server, browser):
        _reset_event_state()
        _create_user("member-869a@test.com", tier_slug="main")
        series = _make_series("woh-869a", "Calendar Series A", 3)

        ctx = _auth_context(browser, "member-869a@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )

        page.locator('[data-testid="series-register-button"]').click()
        page.locator(
            '[data-testid="series-registered-state"]'
        ).wait_for(state="visible")

        from accounts.models import User
        from email_app.models import EmailLog
        from events.models import EventRegistration

        user = User.objects.get(email="member-869a@test.com")
        # Enrolled in all 3 sessions.
        assert EventRegistration.objects.filter(user=user).count() == 3
        # Exactly one series registration confirmation email logged.
        assert (
            EmailLog.objects.filter(
                user=user, email_type="series_registration",
            ).count()
            == 1
        )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestPartialAccessLogsInvite:
    def test_free_member_partial_enroll_logs_one_invite(
        self, django_server, browser,
    ):
        # A free member registers for a series with some Main-only sessions.
        # They are enrolled only in the accessible sessions and still get
        # exactly one series invite covering those sessions (the .ics
        # subset is asserted in the unit tests).
        _reset_event_state()
        _create_user("member-869c@test.com", tier_slug="free")
        series = _make_series(
            "woh-869c", "Calendar Series C", 4, main_only_positions=(3, 4),
        )

        ctx = _auth_context(browser, "member-869c@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="series-register-button"]').click()
        page.locator(
            '[data-testid="series-registered-state"]'
        ).wait_for(state="visible")

        from accounts.models import User
        from email_app.models import EmailLog
        from events.models import EventRegistration

        user = User.objects.get(email="member-869c@test.com")
        # Only the 2 accessible (open) sessions enrolled.
        assert EventRegistration.objects.filter(user=user).count() == 2
        # Exactly one invite logged.
        assert (
            EmailLog.objects.filter(
                user=user, email_type="series_registration",
            ).count()
            == 1
        )

        ctx.close()


@pytest.mark.django_db(transaction=True)
class TestPartialGatedSeriesDashboardEnrollment:
    """Issue #934: free member registers for a partly-gated series.

    Confirms the register entry point enrolls the member in only the open
    sessions (the Premium-only session is not added to their dashboard
    upcoming-events list). The emailed upsell note itself is asserted in
    ``events/tests/test_series_invite.py`` (SES is mocked there).
    """

    def test_free_member_enrolled_in_open_sessions_only(
        self, django_server, browser,
    ):
        _reset_event_state()
        _create_user("member-934@test.com", tier_slug="free")
        series = _make_series(
            "woh-934", "Calendar Series 934", 3,
            premium_only_positions=(3,),
        )

        ctx = _auth_context(browser, "member-934@test.com")
        page = ctx.new_page()
        page.goto(
            f"{django_server}{series.get_absolute_url()}",
            wait_until="domcontentloaded",
        )
        page.locator('[data-testid="series-register-button"]').click()
        page.locator(
            '[data-testid="series-registered-state"]'
        ).wait_for(state="visible")

        # The dashboard upcoming-events section collapses the 2 open
        # registered sessions to the next occurrence; the Premium-only
        # session is not enrolled or shown.
        page.goto(f"{django_server}/", wait_until="domcontentloaded")
        upcoming = page.locator(
            "section", has=page.get_by_role(
                "heading", name="Upcoming Events",
            ),
        )
        upcoming.wait_for(state="visible")
        upcoming_text = upcoming.inner_text()
        assert "Calendar Series 934 — Session 1" in upcoming_text
        assert "Calendar Series 934 — Session 2" not in upcoming_text
        assert "Calendar Series 934 — Session 3" not in upcoming_text
        assert "Event series" in upcoming_text
        assert upcoming.locator(
            '[data-testid="dashboard-event-series-see-more"]'
        ).count() == 1

        from accounts.models import User
        from events.models import EventRegistration

        user = User.objects.get(email="member-934@test.com")
        assert EventRegistration.objects.filter(user=user).count() == 2

        ctx.close()
