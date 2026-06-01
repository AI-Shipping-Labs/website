"""Unit tests for the shared event multi-timezone time strip (issue #817).

The helper was extracted from ``notifications.services.slack_announcements``
into ``events.services.display_time`` so the Slack announcer and the SEO/OG
tag builder share a single source of truth. These focused tests (originally
issue #691) track the helper's new home.
"""

from datetime import datetime
from datetime import timezone as dt_tz

from django.test import TestCase

from events.services.display_time import EVENT_TZ_STRIP, format_event_tz_strip


class FormatEventTzStripTest(TestCase):
    """Focused unit tests for `format_event_tz_strip` (issues #691, #817)."""

    def test_summer_datetime(self):
        """2026-05-21T14:00:00Z renders the expected NYC/UTC/CET/IST strip.

        2026-05-21 is a Thursday. NYC is on EDT (UTC-4) in May, CET is on
        CEST (UTC+2) in May, IST is UTC+5:30 year-round.
        """
        dt = datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc)
        result = format_event_tz_strip(dt)
        self.assertEqual(
            result,
            'Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST',
        )

    def test_winter_datetime(self):
        """A January date renders CET as UTC+1 (winter standard time).

        2026-01-15 is a Thursday. NYC is on EST (UTC-5), CET is UTC+1.
        """
        dt = datetime(2026, 1, 15, 9, 0, 0, tzinfo=dt_tz.utc)
        result = format_event_tz_strip(dt)
        self.assertEqual(
            result,
            'Thu, Jan 15 · 04:00 NYC · 09:00 UTC · 10:00 CET · 14:30 IST',
        )

    def test_eu_dst_boundary_is_cest(self):
        """2026-03-29T09:00:00Z is the EU spring-forward Sunday.

        CET must render as 11:00 (CEST, UTC+2), not 10:00 (CET, UTC+1).
        US already on DST since March 8, so NYC is on EDT (UTC-4).
        """
        dt = datetime(2026, 3, 29, 9, 0, 0, tzinfo=dt_tz.utc)
        result = format_event_tz_strip(dt)
        self.assertIn('11:00 CET', result)
        self.assertNotIn('10:00 CET', result)
        self.assertEqual(
            result,
            'Sun, Mar 29 · 05:00 NYC · 09:00 UTC · 11:00 CET · 14:30 IST',
        )

    def test_us_standard_time_after_fall_back(self):
        """2026-11-04T14:00:00Z is after the US fall-back (Nov 1).

        NYC must render as 09:00 (EST, UTC-5). CET is also on standard
        time by then (UTC+1) — EU fell back Oct 25.
        """
        dt = datetime(2026, 11, 4, 14, 0, 0, tzinfo=dt_tz.utc)
        result = format_event_tz_strip(dt)
        self.assertIn('09:00 NYC', result)
        # Note: %d is zero-padded — Nov 04, not Nov 4 — matching the spec's
        # literal "%a, %b %d" format string.
        self.assertEqual(
            result,
            'Wed, Nov 04 · 09:00 NYC · 14:00 UTC · 15:00 CET · 19:30 IST',
        )

    def test_returns_none_when_start_datetime_is_none(self):
        """Defensive: missing datetime returns None, never raises."""
        self.assertIsNone(format_event_tz_strip(None))

    def test_naive_datetime_treated_as_utc(self):
        """A naive datetime (no tzinfo) is interpreted as UTC."""
        naive = datetime(2026, 5, 21, 14, 0, 0)
        aware = datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc)
        self.assertEqual(
            format_event_tz_strip(naive),
            format_event_tz_strip(aware),
        )

    def test_strip_lists_all_four_zones_west_to_east(self):
        """The public tuple carries NYC, UTC, CET, IST in west-to-east order."""
        self.assertEqual(
            [label for label, _iana in EVENT_TZ_STRIP],
            ['NYC', 'UTC', 'CET', 'IST'],
        )
