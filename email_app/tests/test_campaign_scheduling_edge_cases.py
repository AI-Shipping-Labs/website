"""Regression tests for schedule edge cases and due-dispatch races."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from email_app.models import EmailCampaign
from email_app.services.campaign_scheduling import (
    CampaignScheduleValidationError,
    dispatch_due_campaigns,
    parse_scheduled_time,
)
from email_app.testing_campaign_submit import stub_relay_submit


class CampaignScheduleTimeEdgeCasesTest(TestCase):
    def test_nonexistent_local_time_rejected(self):
        now = datetime(2027, 3, 1, 12, 0, tzinfo=dt_timezone.utc)
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(
                "2027-03-28T02:30:00", "Europe/Berlin", now=now,
            )
        self.assertIn("does not exist", ctx.exception.errors["scheduled_at"])

    def test_ambiguous_local_time_requires_explicit_offset(self):
        now = datetime(2027, 10, 1, 12, 0, tzinfo=dt_timezone.utc)
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(
                "2027-10-31T02:30:00", "Europe/Berlin", now=now,
            )
        self.assertIn("occurs twice", ctx.exception.errors["scheduled_at"])

    def test_explicit_offset_disambiguates_dst_fallback(self):
        now = datetime(2027, 10, 1, 12, 0, tzinfo=dt_timezone.utc)
        first, _ = parse_scheduled_time(
            "2027-10-31T02:30:00+02:00", "Europe/Berlin", now=now,
        )
        second, _ = parse_scheduled_time(
            "2027-10-31T02:30:00+01:00", "Europe/Berlin", now=now,
        )
        self.assertEqual(
            first,
            datetime(2027, 10, 31, 0, 30, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(
            second,
            datetime(2027, 10, 31, 1, 30, tzinfo=dt_timezone.utc),
        )


class ScheduledDispatchRaceTest(TestCase):
    def setUp(self):
        self.due = EmailCampaign.objects.create(
            subject="Due",
            body="body",
            status="draft",
            scheduled_at=timezone.now() - timedelta(minutes=1),
            scheduled_timezone="UTC",
        )

    def _dispatch_after_schedule_change(self, scheduled_at, scheduled_timezone):
        from email_app.services.campaign_dispatch import claim_and_submit_campaign

        def change_then_claim(campaign_id, **kwargs):
            EmailCampaign.objects.filter(pk=campaign_id).update(
                scheduled_at=scheduled_at,
                scheduled_timezone=scheduled_timezone,
            )
            return claim_and_submit_campaign(campaign_id, **kwargs)

        with patch(
            "email_app.services.campaign_dispatch.claim_and_submit_campaign",
            side_effect=change_then_claim,
        ):
            return dispatch_due_campaigns()

    def _assert_schedule_change_skipped(self, mock_async):
        result = self._dispatch_after_schedule_change(None, None)
        self.assertEqual(result["dispatched"], [])
        self.assertEqual(result["skipped"], [{
            "campaign_id": self.due.pk,
            "status": "draft",
        }])
        mock_async.assert_not_called()
        self.due.refresh_from_db()
        self.assertEqual(self.due.status, "draft")

    def test_unschedule_after_due_scan_skips_dispatch(self):
        with stub_relay_submit() as mock_async:
            self._assert_schedule_change_skipped(mock_async)
        self.assertIsNone(self.due.scheduled_at)
        self.assertIsNone(self.due.scheduled_timezone)

    def test_reschedule_to_future_after_due_scan_skips_dispatch(self):
        future_time = timezone.now() + timedelta(days=1)
        with stub_relay_submit() as mock_async:
            result = self._dispatch_after_schedule_change(future_time, "UTC")
        self.assertEqual(result["dispatched"], [])
        self.assertEqual(result["skipped"], [{
            "campaign_id": self.due.pk,
            "status": "draft",
        }])
        mock_async.assert_not_called()
        self.due.refresh_from_db()
        self.assertEqual(self.due.scheduled_at, future_time)
        self.assertEqual(self.due.scheduled_timezone, "UTC")
