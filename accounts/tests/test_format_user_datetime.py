"""Unit tests for ``accounts.services.timezones.format_user_datetime``.

Issue #666: transactional emails must render times in the recipient's
preferred IANA timezone, falling back to literal ``UTC`` when no valid
preference is set. The helper is the single source of truth for that
conversion across email senders.

Covers:
- Valid IANA timezone (string contains converted time + IANA label).
- Empty timezone falls back to UTC and renders the literal ``UTC`` token.
- Invalid IANA string falls back to UTC (does NOT crash).
- DST winter (CET, UTC+01:00) and DST summer (CEST, UTC+02:00) — same
  helper call produces both correctly for the same Berlin user.
- Half-hour offset zone (Asia/Kolkata, UTC+05:30).
- Naive datetime input is treated as UTC.
- ``user=None`` falls back to UTC.
- ``fmt`` parameter overrides the default format string.
"""

from datetime import datetime
from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.services.timezones import (
    DEFAULT_USER_DATETIME_FORMAT,
    format_user_datetime,
)

User = get_user_model()


def _utc(year, month, day, hour, minute=0):
    """Build a UTC-aware datetime for fixtures."""
    from datetime import UTC
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


class FormatUserDatetimeValidTimezoneTest(TestCase):
    """A user with a valid IANA timezone sees the time in that zone."""

    def test_berlin_user_in_summer_renders_cest_offset(self):
        """2026-06-01 16:00 UTC is 18:00 CEST in Europe/Berlin (DST)."""
        user = User.objects.create_user(
            email='berlin@example.com',
            preferred_timezone='Europe/Berlin',
        )

        result = format_user_datetime(_utc(2026, 6, 1, 16, 0), user)

        self.assertEqual(result, 'June 01, 2026, 18:00 Europe/Berlin')

    def test_berlin_user_in_winter_renders_cet_offset(self):
        """2026-01-15 16:00 UTC is 17:00 CET in Europe/Berlin (no DST)."""
        user = User.objects.create_user(
            email='berlin-winter@example.com',
            preferred_timezone='Europe/Berlin',
        )

        result = format_user_datetime(_utc(2026, 1, 15, 16, 0), user)

        self.assertEqual(result, 'January 15, 2026, 17:00 Europe/Berlin')

    def test_kolkata_half_hour_offset_renders_correctly(self):
        """Asia/Kolkata is UTC+05:30 year-round (no DST)."""
        user = User.objects.create_user(
            email='kolkata@example.com',
            preferred_timezone='Asia/Kolkata',
        )

        result = format_user_datetime(_utc(2026, 6, 1, 12, 30), user)

        self.assertEqual(result, 'June 01, 2026, 18:00 Asia/Kolkata')


class FormatUserDatetimeDstBoundaryTest(TestCase):
    """A single Europe/Berlin user crossing the DST boundary picks up
    the right offset for each side without needing a separate helper.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='dst-boundary@example.com',
            preferred_timezone='Europe/Berlin',
        )

    def test_winter_offset_applies_in_january(self):
        result = format_user_datetime(_utc(2026, 1, 15, 16, 0), self.user)
        self.assertIn('17:00 Europe/Berlin', result)

    def test_summer_offset_applies_in_june(self):
        result = format_user_datetime(_utc(2026, 6, 15, 16, 0), self.user)
        self.assertIn('18:00 Europe/Berlin', result)


class FormatUserDatetimeFallbackTest(TestCase):
    """When the user's preference is missing or invalid the helper
    formats in UTC and appends the literal ``UTC`` token so the
    recipient can convert unambiguously.
    """

    def test_empty_preferred_timezone_falls_back_to_utc(self):
        user = User.objects.create_user(
            email='no-tz@example.com',
            preferred_timezone='',
        )

        result = format_user_datetime(_utc(2026, 6, 1, 16, 0), user)

        self.assertEqual(result, 'June 01, 2026, 16:00 UTC')

    def test_invalid_iana_string_falls_back_to_utc_without_crashing(self):
        user = User.objects.create_user(
            email='bogus-tz@example.com',
            preferred_timezone='Not/AZone',
        )

        result = format_user_datetime(_utc(2026, 6, 1, 16, 0), user)

        self.assertEqual(result, 'June 01, 2026, 16:00 UTC')

    def test_user_none_falls_back_to_utc(self):
        result = format_user_datetime(_utc(2026, 6, 1, 16, 0), None)

        self.assertEqual(result, 'June 01, 2026, 16:00 UTC')

    def test_naive_datetime_is_treated_as_utc(self):
        """The helper interprets a naive datetime as UTC (matching the
        project's stored-datetime convention) rather than crashing.
        """
        naive = datetime(2026, 6, 1, 16, 0)
        user = User.objects.create_user(
            email='naive@example.com',
            preferred_timezone='Europe/Berlin',
        )

        result = format_user_datetime(naive, user)

        # Naive 16:00 -> UTC 16:00 -> Berlin 18:00 CEST.
        self.assertEqual(result, 'June 01, 2026, 18:00 Europe/Berlin')


class FormatUserDatetimeFormatStringTest(TestCase):
    """The ``fmt`` keyword overrides the default format string."""

    def test_default_format_matches_constant(self):
        """A sanity check so a future format change is visible here."""
        self.assertEqual(DEFAULT_USER_DATETIME_FORMAT, '%B %d, %Y, %H:%M')

    def test_custom_fmt_overrides_default(self):
        user = User.objects.create_user(
            email='fmt@example.com',
            preferred_timezone='Europe/Berlin',
        )

        result = format_user_datetime(
            _utc(2026, 6, 1, 16, 0), user, fmt='%Y-%m-%d %H:%M',
        )

        self.assertEqual(result, '2026-06-01 18:00 Europe/Berlin')


class FormatUserDatetimeTypeErrorTest(TestCase):
    """Passing a non-datetime value fails fast rather than silently
    producing garbage in the email body.
    """

    def test_non_datetime_raises_type_error(self):
        user_mock = Mock(preferred_timezone='UTC')
        with self.assertRaises(TypeError):
            format_user_datetime('2026-06-01', user_mock)
