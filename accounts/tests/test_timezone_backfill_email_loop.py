"""End-to-end loop for issue #961: passive backfill -> Relay context.

The existing #666 tests prove the reminder email renders UTC for an empty
``preferred_timezone`` and the local zone once a valid IANA value is set.
This test pins the new #961 link in that chain: after the passive backfill
endpoint persists the browser zone for a previously empty user, the next
reminder email renders that local zone instead of UTC.

The reminder runs through the durable package delivery worker. The test
asserts on the resolved context handed to Relay, where recipient-local time
is now computed.
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.testing import StubRelayClient, deliver_pending_mail
from events.models import Event
from notifications.services.notification_service import NotificationService

User = get_user_model()


class TimezoneBackfillEmailLoopTest(TestCase):
    # 2026-06-01 16:00 UTC -> 18:00 Europe/Berlin (CEST). Same instant the
    # #666 acceptance pin uses.
    EVENT_INSTANT = datetime(2026, 6, 1, 16, 0, tzinfo=UTC)
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="loop@example.com", preferred_timezone=""
        )

    def _resolved_reminder_context(self):
        event = Event.objects.create(
            title="AI Workshop",
            slug="timezone-backfill-workshop",
            start_datetime=self.EVENT_INSTANT,
            status="upcoming",
        )
        NotificationService.create_event_reminder(
            event, self.user, "24h", "Reminder", "Soon",
        )
        stub = StubRelayClient()
        with patch(
            "community_base.mail.backends.relay.configured_client",
            return_value=stub,
        ):
            deliver_pending_mail()
        self.assertEqual(len(stub.calls), 1)
        return stub.calls[0]["context"]

    def test_reminder_uses_utc_before_backfill(self):
        context = self._resolved_reminder_context()

        self.assertIn("16:00 UTC", context["event_datetime"])
        self.assertNotIn("Europe/Berlin", context["event_datetime"])

    def test_reminder_uses_local_zone_after_passive_backfill(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/account/api/timezone-preference",
            data=json.dumps({"timezone": "Europe/Berlin", "passive": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_timezone, "Europe/Berlin")
        context = self._resolved_reminder_context()
        self.assertIn("18:00 Europe/Berlin", context["event_datetime"])
        self.assertNotIn("16:00 UTC", context["event_datetime"])
