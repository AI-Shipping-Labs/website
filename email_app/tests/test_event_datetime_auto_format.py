"""Issue #666 guardrail: ``EmailService._render_template_with_footer``
auto-formats any ``datetime`` value in the context with
``format_user_datetime`` so a future caller can pass a raw datetime
without bypassing the timezone helper.

This is the central enforcement point. Callers may still pass a
pre-formatted string (existing behaviour) — that path is exercised by
``test_email_service.test_event_reminder_template``. This file pins the
new path: raw datetime in, recipient-local string out.
"""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.services.email_service import EmailService

User = get_user_model()


class EmailServiceEventDatetimeAutoFormatTest(TestCase):
    """A raw ``datetime`` in the context is converted to the
    recipient's local time + IANA label."""

    @classmethod
    def setUpTestData(cls):
        cls.berlin_user = User.objects.create_user(
            email='berlin@example.com',
            preferred_timezone='Europe/Berlin',
        )
        cls.no_tz_user = User.objects.create_user(
            email='no-tz@example.com',
            preferred_timezone='',
        )

    @patch.object(EmailService, '_send_ses', return_value='auto-fmt-1')
    def test_raw_datetime_is_formatted_in_user_timezone(self, mock_ses):
        service = EmailService()
        service.send(
            self.berlin_user,
            'event_reminder',
            {
                'event_title': 'AI Workshop',
                # Pass a raw datetime, NOT a pre-formatted string.
                'event_datetime': datetime(2026, 6, 1, 16, 0, tzinfo=UTC),
                'event_url': 'https://zoom.us/j/123',
            },
        )

        html = mock_ses.call_args[0][2]

        self.assertIn('18:00 Europe/Berlin', html)
        self.assertNotIn('16:00 UTC', html)

    @patch.object(EmailService, '_send_ses', return_value='auto-fmt-2')
    def test_raw_datetime_falls_back_to_utc_for_unset_preference(self, mock_ses):
        service = EmailService()
        service.send(
            self.no_tz_user,
            'event_reminder',
            {
                'event_title': 'AI Workshop',
                'event_datetime': datetime(2026, 6, 1, 16, 0, tzinfo=UTC),
                'event_url': 'https://zoom.us/j/123',
            },
        )

        html = mock_ses.call_args[0][2]

        self.assertIn('16:00 UTC', html)

    @patch.object(EmailService, '_send_ses', return_value='auto-fmt-3')
    def test_pre_formatted_string_passes_through_unchanged(self, mock_ses):
        """Existing callers that already pre-formatted the string keep
        working — the guardrail only converts datetimes, never strings.
        """
        service = EmailService()
        service.send(
            self.berlin_user,
            'event_reminder',
            {
                'event_title': 'AI Workshop',
                'event_datetime': 'March 20, 2026 at 18:00 Custom/Zone',
                'event_url': 'https://zoom.us/j/123',
            },
        )

        html = mock_ses.call_args[0][2]

        self.assertIn('March 20, 2026 at 18:00 Custom/Zone', html)

    @patch.object(EmailService, '_send_ses', return_value='auto-fmt-4')
    def test_cancellation_access_until_datetime_is_auto_formatted(self, mock_ses):
        """The cancellation template's ``access_until`` field is
        currently only fed pre-formatted strings, but a future sender
        could pass a datetime. The guardrail must convert it.
        """
        service = EmailService()
        service.send(
            self.berlin_user,
            'cancellation',
            {
                'tier_name': 'Main',
                'access_until': datetime(2026, 6, 1, 16, 0, tzinfo=UTC),
            },
        )

        html = mock_ses.call_args[0][2]

        self.assertIn('18:00 Europe/Berlin', html)
