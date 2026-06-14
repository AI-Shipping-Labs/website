"""Playwright E2E for the series access-level guardrail (issue #958).

Studio is the human-controlled override surface. Three scenarios:

1. Adding a session to a gated series with the pre-filled level inherits the
   gate with NO confirmation prompt.
2. Choosing a different level (Free on a Main series) raises a confirmation
   prompt; choosing Cancel creates nothing.
3. Choosing a different level and confirming (Yes) saves the occurrence at
   the differing level — the human confirmation is the override.

These rely on local DB seeding / session-cookie auth, so they are
local_only (cannot run against the deployed dev environment).
"""

import os
import re
from datetime import date, datetime, timedelta

import pytest

from playwright_tests.conftest import (
    auth_context as _auth_context,
)
from playwright_tests.conftest import (
    create_staff_user as _create_staff_user,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytestmark = pytest.mark.local_only


def _reset_event_state():
    from django.db import connection

    from events.models import Event, EventRegistration, EventSeries

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    EventSeries.objects.all().delete()
    connection.close()


def _make_main_series(slug):
    from django.db import connection

    from events.models import EventSeries

    series = EventSeries(
        name="LLM Office Hours",
        slug=slug,
        start_time=datetime(2026, 1, 1, 18, 0).time(),
        required_level=20,
    )
    series.save()
    connection.close()
    return series


def _add_form_select(page):
    return page.locator(
        '[data-testid="add-occurrence-form"] select[name="required_level"]',
    )


@pytest.mark.django_db(transaction=True)
class TestSeriesLevelGuardrail:
    def test_inherits_gate_with_no_prompt(self, django_server, browser):
        _reset_event_state()
        _create_staff_user("staff-958a@test.com")
        series = _make_main_series("llm-office-hours-a")

        dialogs = []

        ctx = _auth_context(browser, "staff-958a@test.com")
        page = ctx.new_page()
        page.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )

        # The selector pre-fills the series level (Main / 20).
        assert _add_form_select(page).input_value() == "20"

        future = date.today() + timedelta(days=80)
        page.fill('input[name="start_date"]', future.strftime("%d/%m/%Y"))
        # Leave the level at the pre-filled Main value.
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series.pk}/$"),
        )

        # No confirmation prompt fired for a matching level.
        assert dialogs == []

        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 1
        level_cell = rows.first.locator(
            '[data-testid="event-access-level"]',
        )
        assert level_cell.get_attribute("data-level") == "20"

        new_event = series.events.order_by("-series_position").first()
        assert new_event.required_level == 20
        ctx.close()

    def test_warn_and_cancel_creates_nothing(self, django_server, browser):
        _reset_event_state()
        _create_staff_user("staff-958b@test.com")
        series = _make_main_series("llm-office-hours-b")

        dialogs = []

        ctx = _auth_context(browser, "staff-958b@test.com")
        page = ctx.new_page()
        # Dismiss (Cancel) the confirmation and record its message.
        page.on(
            "dialog",
            lambda d: (dialogs.append(d.message), d.dismiss()),
        )
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )

        future = date.today() + timedelta(days=81)
        page.fill('input[name="start_date"]', future.strftime("%d/%m/%Y"))
        # Change the level to Free (0) — differs from the Main series.
        _add_form_select(page).select_option("0")
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_timeout(500)

        # The confirmation prompt fired with the differing-level wording.
        assert len(dialogs) == 1
        message = dialogs[0]
        assert "Free" in message
        assert "Main" in message
        assert "Are you sure" in message

        # Cancel aborted the submit: no occurrence created, still on the form.
        assert series.events.count() == 0
        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 0
        ctx.close()

    def test_confirm_yes_creates_free_session(self, django_server, browser):
        _reset_event_state()
        _create_staff_user("staff-958c@test.com")
        series = _make_main_series("llm-office-hours-c")

        dialogs = []

        ctx = _auth_context(browser, "staff-958c@test.com")
        page = ctx.new_page()
        # Accept (Yes) the confirmation.
        page.on(
            "dialog",
            lambda d: (dialogs.append(d.message), d.accept()),
        )
        page.goto(
            f"{django_server}/studio/event-series/{series.pk}/",
            wait_until="domcontentloaded",
        )

        future = date.today() + timedelta(days=82)
        page.fill('input[name="start_date"]', future.strftime("%d/%m/%Y"))
        _add_form_select(page).select_option("0")
        page.locator('[data-testid="add-occurrence-submit"]').click()
        page.wait_for_url(
            re.compile(rf".*/studio/event-series/{series.pk}/$"),
        )

        assert len(dialogs) == 1
        assert "Free" in dialogs[0]

        # The Free occurrence was created alongside the Main series.
        new_event = series.events.order_by("-series_position").first()
        assert new_event is not None
        assert new_event.required_level == 0

        rows = page.locator('[data-testid="event-series-member-row"]')
        assert rows.count() == 1
        level_cell = rows.first.locator(
            '[data-testid="event-access-level"]',
        )
        assert level_cell.get_attribute("data-level") == "0"
        ctx.close()
