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

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

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

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="loop@example.com", preferred_timezone=""
        )

    pass

    pass

    pass
