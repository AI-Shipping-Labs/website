"""End-to-end loop for issue #961: passive backfill -> local-zone email.

The existing #666 tests prove the reminder email renders UTC for an empty
``preferred_timezone`` and the local zone once a valid IANA value is set.
This test pins the new #961 link in that chain: after the passive backfill
endpoint persists the browser zone for a previously empty user, the next
reminder email renders that local zone instead of UTC.

We send the reminder through ``EmailService`` (the production reminder
caller in ``notifications.services.notification_service``) with SES stubbed,
so a regression in the backfill -> render handoff is caught here.
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.services.email_service import EmailService

User = get_user_model()


class TimezoneBackfillEmailLoopTest(TestCase):
    # 2026-06-01 16:00 UTC -> 18:00 Europe/Berlin (CEST). Same instant the
    # #666 acceptance pin uses.
    EVENT_INSTANT = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)
    REMINDER_CONTEXT = {
        "event_title": "AI Workshop",
        "event_datetime": EVENT_INSTANT,
        "event_url": "https://zoom.us/j/123",
    }

    def setUp(self):
        self.user = User.objects.create_user(
            email="loop@example.com", preferred_timezone=""
        )

    def _render_reminder_html(self, mock_ses):
        EmailService().send(self.user, "event_reminder", self.REMINDER_CONTEXT)
        return mock_ses.call_args[0][2]

    @patch.object(EmailService, "_send_ses", return_value="loop-before")
    def test_reminder_renders_utc_before_backfill(self, mock_ses):
        html = self._render_reminder_html(mock_ses)
        self.assertIn("16:00 UTC", html)
        self.assertNotIn("Europe/Berlin", html)

    @patch.object(EmailService, "_send_ses", return_value="loop-after")
    def test_reminder_renders_local_zone_after_passive_backfill(self, mock_ses):
        # The passive client backfill persists the detected browser zone.
        self.client.force_login(self.user)
        response = self.client.post(
            "/account/api/timezone-preference",
            data=json.dumps({"timezone": "Europe/Berlin", "passive": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        # Same instant now renders the recipient's local zone, not UTC.
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "Europe/Berlin")
        html = self._render_reminder_html(mock_ses)
        self.assertIn("18:00 Europe/Berlin", html)
        self.assertNotIn("16:00 UTC", html)
