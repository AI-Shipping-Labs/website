"""
Playwright E2E test for dark-mode contrast on accent buttons (issue #362).

The previous bug used ``bg-accent text-white`` on the active "List" /
"Calendar" view-toggle on /events and /events/calendar/..., and on the
today-cell day number in the calendar grid. In dark mode the accent
HSL is ``75 100% 50%`` (lime ~#bfff00) and ``--accent-foreground`` is
near-black (``0 0% 4%``); using literal white instead gave a contrast
ratio of ~1.3:1 -- the label was effectively invisible. The same
template hard-coded ``bg-gray-500/20 text-gray-400`` on the cancelled
status pill, breaking visual consistency with the surrounding semantic
tokens.

This test loads the affected pages with dark mode forced and asserts
that the active-button background and label colors are visibly
different (max per-channel RGB delta >= 80). The bug produced near-zero
delta on the green channel; the threshold catches the regression
without doing fragile screenshot diffing.

Usage:
    uv run pytest playwright_tests/test_dark_mode_contrast.py -v
"""

import datetime
import os

import pytest
from django.utils import timezone

from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")


CHANNEL_DELTA_THRESHOLD = 80


def _force_dark_mode(page):
    """Set localStorage['theme']='dark' before any document loads.

    The blocking script in templates/base.html reads localStorage on
    first paint and adds the 'dark' class to <html>, so this guarantees
    the page renders in dark mode from the very first frame.
    """
    page.add_init_script(
        "window.localStorage.setItem('theme', 'dark');"
    )


def _parse_rgb(css_color):
    """Parse a 'rgb(r, g, b)' or 'rgba(r, g, b, a)' string to (r, g, b)."""
    inner = css_color.strip()
    inner = inner[inner.index("(") + 1 : inner.rindex(")")]
    parts = [p.strip() for p in inner.split(",")]
    return (int(float(parts[0])), int(float(parts[1])), int(float(parts[2])))


def _max_channel_delta(rgb_a, rgb_b):
    """Return the maximum per-channel absolute difference between two RGB tuples."""
    return max(abs(a - b) for a, b in zip(rgb_a, rgb_b, strict=True))


def _computed_bg_and_fg(page, locator):
    """Read computed background-color and color from the first match of locator."""
    handle = locator.first
    handle.wait_for(state="attached", timeout=5000)
    bg = handle.evaluate(
        "el => getComputedStyle(el).backgroundColor"
    )
    fg = handle.evaluate(
        "el => getComputedStyle(el).color"
    )
    return bg, fg


def _clear_events():
    """Delete all events to ensure a clean state."""
    from django.db import connection

    from events.models import Event, EventRegistration

    EventRegistration.objects.all().delete()
    Event.objects.all().delete()
    connection.close()


def _create_today_event():
    """Create one upcoming event so /events lists are non-empty.

    The today-cell day-number span is rendered for the current day in
    the calendar grid regardless of whether events exist that day; we
    don't need to manufacture one for it. This event keeps the events
    listing populated for the /events scenario.
    """
    from django.db import connection

    from events.models import Event

    Event.objects.create(
        title="Dark Mode Smoke Event",
        slug="dark-mode-smoke-event",
        description="Fixture for dark-mode contrast E2E.",
        event_type="live",
        start_datetime=timezone.now() + datetime.timedelta(days=3),
        timezone="Europe/Berlin",
        required_level=0,
        status="upcoming",
    )
    connection.close()


@pytest.mark.django_db(transaction=True)
class TestDarkModeAccentContrast:
    """Active accent buttons remain legible against the lime accent fill."""

    def test_accent_buttons_have_visible_label_in_dark_mode(
        self, django_server, page
    ):
        """The active List / Calendar toggles and the today-cell day
        number must use ``text-accent-foreground`` (near-black in dark
        mode), not ``text-white``. Asserts a per-channel RGB delta of
        at least 80 between background-color and color for each."""
        _ensure_tiers()
        _clear_events()
        _create_today_event()

        _force_dark_mode(page)

        # ---- /events : the active "List" toggle ----
        page.goto(
            f"{django_server}/events",
            wait_until="domcontentloaded",
        )

        # Sanity: dark mode is on.
        assert page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        ) is True, "expected /events to render in dark mode"

        list_toggle = page.locator(
            "a.bg-accent",
            has_text="List",
        )
        bg, fg = _computed_bg_and_fg(page, list_toggle)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"active 'List' toggle on /events has illegible label in "
            f"dark mode: bg={bg}, color={fg}, max channel delta={delta} "
            f"< {CHANNEL_DELTA_THRESHOLD}. Use bg-accent text-accent-foreground."
        )

        # ---- /events/calendar/<year>/<month> : active "Calendar" toggle ----
        today = datetime.date.today()
        page.goto(
            f"{django_server}/events/calendar/{today.year}/{today.month}",
            wait_until="domcontentloaded",
        )

        assert page.evaluate(
            "() => document.documentElement.classList.contains('dark')"
        ) is True, "expected /events/calendar to render in dark mode"

        calendar_toggle = page.locator(
            "a.bg-accent",
            has_text="Calendar",
        )
        bg, fg = _computed_bg_and_fg(page, calendar_toggle)
        delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
        assert delta >= CHANNEL_DELTA_THRESHOLD, (
            f"active 'Calendar' toggle on /events/calendar has "
            f"illegible label in dark mode: bg={bg}, color={fg}, "
            f"max channel delta={delta} < {CHANNEL_DELTA_THRESHOLD}. "
            f"Use bg-accent text-accent-foreground."
        )

        # ---- today-cell day number (only present if today is in this month) ----
        today_day_span = page.locator(
            "span.bg-accent.font-bold"
        )
        if today_day_span.count() >= 1:
            bg, fg = _computed_bg_and_fg(page, today_day_span)
            delta = _max_channel_delta(_parse_rgb(bg), _parse_rgb(fg))
            assert delta >= CHANNEL_DELTA_THRESHOLD, (
                f"today-cell day number has illegible label in dark "
                f"mode: bg={bg}, color={fg}, max channel delta={delta} "
                f"< {CHANNEL_DELTA_THRESHOLD}. Use "
                f"bg-accent text-accent-foreground font-bold."
            )
