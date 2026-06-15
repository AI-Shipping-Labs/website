"""Unit tests for the issue #963 timezone-email-line helper.

``build_timezone_email_line`` returns the contextual "set/update your
timezone" sentence whose wording matches how ``format_user_datetime``
rendered the time: the prominent "Set your timezone" UTC-fallback variant
when the recipient has no valid ``preferred_timezone``, and the quieter
"Change your timezone" variant when a valid IANA zone is set. The variant
is chosen off the SAME ``is_valid_timezone`` check the time formatter uses,
so copy can never contradict the rendered time.
"""

from types import SimpleNamespace

from django.test import TestCase

from accounts.services.timezones import (
    build_timezone_account_url,
    build_timezone_email_line,
)

LINK = "https://example.com/account/#display-preferences-section"


class BuildTimezoneAccountUrlTest(TestCase):
    def test_appends_display_preferences_fragment(self):
        self.assertEqual(
            build_timezone_account_url("https://example.com"),
            "https://example.com/account/#display-preferences-section",
        )

    def test_strips_trailing_slash_on_site_url(self):
        self.assertEqual(
            build_timezone_account_url("https://example.com/"),
            "https://example.com/account/#display-preferences-section",
        )


class BuildTimezoneEmailLineTest(TestCase):
    def test_empty_timezone_returns_utc_variant(self):
        user = SimpleNamespace(preferred_timezone="")
        line = build_timezone_email_line(user, LINK)

        self.assertIn("Set your timezone", line)
        self.assertIn("shown in UTC", line)
        self.assertIn(LINK, line)
        self.assertNotIn("Change your timezone", line)

    def test_invalid_timezone_returns_utc_variant(self):
        user = SimpleNamespace(preferred_timezone="Not/AZone")
        line = build_timezone_email_line(user, LINK)

        self.assertIn("Set your timezone", line)
        self.assertIn(LINK, line)

    def test_none_user_returns_utc_variant(self):
        line = build_timezone_email_line(None, LINK)

        self.assertIn("Set your timezone", line)
        self.assertIn(LINK, line)

    def test_valid_timezone_returns_set_variant(self):
        user = SimpleNamespace(preferred_timezone="Europe/Berlin")
        line = build_timezone_email_line(user, LINK)

        self.assertIn("Change your timezone", line)
        self.assertIn("shown in your timezone", line)
        self.assertIn(LINK, line)
        self.assertNotIn("Set your timezone", line)

    def test_line_carries_markdown_link_with_account_fragment(self):
        user = SimpleNamespace(preferred_timezone="")
        link = build_timezone_account_url("https://env.example.com")
        line = build_timezone_email_line(user, link)

        # Markdown link target must point at the account display-preferences
        # fragment so the recipient lands on the timezone control.
        self.assertIn(
            "(https://env.example.com/account/#display-preferences-section)",
            line,
        )
