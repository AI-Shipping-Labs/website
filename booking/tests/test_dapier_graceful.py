"""Dapier-missing graceful degradation (maven-webhook-e2e follow-up).

When DAPIER_API_TOKEN is unset, booking must keep working without Google
Calendar sync: availability ignores calendar busy spans, and event
create/update/delete become logged no-ops instead of raising.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from django.test import TestCase

from booking.services import booking_service
from booking.services.calendar import (
    create_calendar_event,
    delete_calendar_event,
    update_calendar_event,
)
from booking.services.calendar import (
    query_busy as calendar_query_busy,
)
from booking.services.dapier import DapierNotConfigured, get_calendar_access_token
from integrations.models import IntegrationSetting


def _set(key, value):
    IntegrationSetting.objects.update_or_create(key=key, defaults={"value": value})


def _clear_config_cache():
    from integrations.config import clear_config_cache

    clear_config_cache()


class DapierMissingGracefulTest(TestCase):
    def setUp(self):
        _set("DAPIER_API_TOKEN", "")
        _set("DAPIER_BASE_URL", "https://dapier.example.invalid")
        _set("DAPIER_AGENT_NAME", "ai-shipping-labs")
        _clear_config_cache()
        self.addCleanup(_clear_config_cache)

    def test_access_token_raises_not_configured(self):
        with self.assertRaises(DapierNotConfigured):
            get_calendar_access_token()

    def test_query_busy_returns_empty(self):
        start = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(calendar_query_busy(start, end), [])

    def test_create_returns_empty_dict(self):
        start = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 27, 10, 30, tzinfo=timezone.utc)
        self.assertEqual(
            create_calendar_event(
                summary="t",
                description="d",
                start=start,
                end=end,
                attendee_email="a@example.com",
            ),
            {},
        )

    def test_update_returns_empty_dict(self):
        start = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 27, 10, 30, tzinfo=timezone.utc)
        self.assertEqual(update_calendar_event("evt_1", start=start, end=end), {})

    def test_delete_returns_false(self):
        self.assertFalse(delete_calendar_event("evt_1"))

    def test_availability_ignores_calendar(self):
        # query_busy is what booking_service.get_available_slots calls; with no
        # token it must be [] so local schedule/buffer policy still applies.
        start = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
        with patch(
            "booking.services.booking_service.query_busy",
            wraps=calendar_query_busy,
        ):
            self.assertEqual(booking_service.query_busy(start, end), [])
