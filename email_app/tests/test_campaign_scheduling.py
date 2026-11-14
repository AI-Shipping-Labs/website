"""Tests for campaign scheduling: parse, schedule/unschedule, and dispatch.

Covers the ``email_app.services.campaign_scheduling`` contract (timezone
handling, conflict rules, exactly-once dispatch through the shared claim)
plus the claim-side rule that any pending schedule is consumed when a
campaign is claimed for sending.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone

from email_app.models import EmailCampaign
from email_app.services.campaign_scheduling import (
    CampaignScheduleConflict,
    CampaignScheduleValidationError,
    dispatch_due_campaigns,
    localized_schedule_display,
    parse_scheduled_time,
    schedule_campaign,
    unschedule_campaign,
)
from email_app.testing_campaign_submit import stub_relay_submit


def _in_hours(hours):
    return timezone.now() + timedelta(hours=hours)


# parse_scheduled_time validates "Must be in the future" against the `now`
# it is handed. Freeze that reference point so the fixed fixture datetimes
# below never rot as the wall clock passes them.
FROZEN_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=dt_timezone.utc)


class ParseScheduledTimeTest(TestCase):
    def test_naive_datetime_requires_timezone(self):
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(
                "2026-11-05T10:00:00", None, now=FROZEN_NOW,
            )
        self.assertIn("scheduled_timezone", ctx.exception.errors)

    def test_naive_datetime_interpreted_in_timezone(self):
        instant, tz_name = parse_scheduled_time(
            "2026-11-05T10:00:00", "Europe/Berlin", now=FROZEN_NOW,
        )
        self.assertEqual(
            instant,
            datetime(2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(tz_name, "Europe/Berlin")

    def test_aware_datetime_converted_to_utc_instant(self):
        instant, tz_name = parse_scheduled_time(
            "2026-11-05T10:00:00+01:00", None, now=FROZEN_NOW,
        )
        self.assertEqual(
            instant,
            datetime(2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc),
        )
        self.assertIsNone(tz_name)

    def test_aware_datetime_without_timezone_has_no_zone_name(self):
        instant, tz_name = parse_scheduled_time(
            "2026-11-05T10:00:00+01:00", None, now=FROZEN_NOW,
        )
        self.assertIsNone(tz_name)

    def test_offset_timezone_mismatch_rejected(self):
        # +02:00 is never the Europe/Berlin offset in November (CET = +01:00).
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(
                "2026-11-05T10:00:00+02:00", "Europe/Berlin", now=FROZEN_NOW,
            )
        self.assertIn("scheduled_timezone", ctx.exception.errors)

    def test_offset_timezone_match_accepted(self):
        instant, tz_name = parse_scheduled_time(
            "2026-11-05T10:00:00+01:00", "Europe/Berlin", now=FROZEN_NOW,
        )
        self.assertEqual(tz_name, "Europe/Berlin")
        self.assertEqual(
            instant,
            datetime(2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc),
        )

    def test_past_time_rejected(self):
        now = timezone.now()
        past = (now - timedelta(minutes=1)).isoformat()
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(past, "UTC", now=now)
        self.assertIn("Must be in the future", ctx.exception.errors["scheduled_at"])

    def test_unknown_timezone_rejected(self):
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(
                "2026-11-05T10:00:00", "Mars/Olympus", now=FROZEN_NOW,
            )
        self.assertIn("scheduled_timezone", ctx.exception.errors)

    def test_garbage_datetime_rejected(self):
        with self.assertRaises(CampaignScheduleValidationError) as ctx:
            parse_scheduled_time(
                "next tuesday please", "UTC", now=FROZEN_NOW,
            )
        self.assertIn("scheduled_at", ctx.exception.errors)

    def test_non_string_datetime_rejected(self):
        with self.assertRaises(CampaignScheduleValidationError):
            parse_scheduled_time(123, "UTC", now=FROZEN_NOW)


class ScheduleCampaignServiceTest(TestCase):
    def setUp(self):
        self.draft = EmailCampaign.objects.create(
            subject="Schedule me", body="body", status="draft",
        )

    def test_schedule_sets_fields(self):
        campaign = schedule_campaign(
            self.draft.pk,
            scheduled_at="2026-11-05T10:00:00",
            scheduled_timezone="Europe/Berlin",
            now=FROZEN_NOW,
        )
        campaign.refresh_from_db()
        self.assertEqual(
            campaign.scheduled_at,
            datetime(2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(campaign.scheduled_timezone, "Europe/Berlin")
        self.assertEqual(campaign.status, "draft")

    def test_reschedule_overwrites_fields(self):
        schedule_campaign(
            self.draft.pk,
            scheduled_at="2026-11-05T10:00:00",
            scheduled_timezone="UTC",
            now=FROZEN_NOW,
        )
        campaign = schedule_campaign(
            self.draft.pk,
            scheduled_at="2026-12-01T08:00:00",
            scheduled_timezone="Europe/Berlin",
            now=FROZEN_NOW,
        )
        campaign.refresh_from_db()
        self.assertEqual(
            campaign.scheduled_at,
            datetime(2026, 12, 1, 7, 0, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(campaign.scheduled_timezone, "Europe/Berlin")

    def test_schedule_non_draft_conflicts(self):
        self.draft.status = "sent"
        self.draft.save(update_fields=["status"])
        with self.assertRaises(CampaignScheduleConflict):
            schedule_campaign(
                self.draft.pk,
                scheduled_at="2026-11-05T10:00:00",
                scheduled_timezone="UTC",
                now=FROZEN_NOW,
            )

    def test_schedule_unknown_campaign_raises(self):
        with self.assertRaises(EmailCampaign.DoesNotExist):
            schedule_campaign(
                99999,
                scheduled_at="2026-11-05T10:00:00",
                scheduled_timezone="UTC",
                now=FROZEN_NOW,
            )

    def test_unschedule_clears_fields(self):
        schedule_campaign(
            self.draft.pk,
            scheduled_at="2026-11-05T10:00:00",
            scheduled_timezone="Europe/Berlin",
            now=FROZEN_NOW,
        )
        campaign = unschedule_campaign(self.draft.pk)
        campaign.refresh_from_db()
        self.assertIsNone(campaign.scheduled_at)
        self.assertIsNone(campaign.scheduled_timezone)

    def test_unschedule_not_scheduled_conflicts(self):
        with self.assertRaises(CampaignScheduleConflict):
            unschedule_campaign(self.draft.pk)

    def test_unschedule_non_draft_conflicts(self):
        schedule_campaign(
            self.draft.pk,
            scheduled_at="2026-11-05T10:00:00",
            scheduled_timezone="UTC",
            now=FROZEN_NOW,
        )
        self.draft.status = "sending"
        self.draft.save(update_fields=["status"])
        with self.assertRaises(CampaignScheduleConflict):
            unschedule_campaign(self.draft.pk)


class LocalizedScheduleDisplayTest(TestCase):
    def test_display_uses_campaign_timezone(self):
        campaign = EmailCampaign(
            scheduled_at=datetime(2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc),
            scheduled_timezone="Europe/Berlin",
        )
        self.assertEqual(
            localized_schedule_display(campaign),
            "2026-11-05 10:00 (Europe/Berlin)",
        )

    def test_display_without_timezone_falls_back_to_utc(self):
        campaign = EmailCampaign(
            scheduled_at=datetime(2026, 11, 5, 9, 0, tzinfo=dt_timezone.utc),
            scheduled_timezone=None,
        )
        self.assertEqual(localized_schedule_display(campaign), "2026-11-05 09:00")

    def test_display_none_when_unscheduled(self):
        campaign = EmailCampaign(scheduled_at=None, scheduled_timezone=None)
        self.assertIsNone(localized_schedule_display(campaign))


class DispatchDueCampaignsTest(TestCase):
    def setUp(self):
        self.due = EmailCampaign.objects.create(
            subject="Due", body="body", status="draft",
            scheduled_at=timezone.now() - timedelta(minutes=1),
            scheduled_timezone="UTC",
        )
        self.future = EmailCampaign.objects.create(
            subject="Future", body="body", status="draft",
            scheduled_at=timezone.now() + timedelta(hours=1),
            scheduled_timezone="UTC",
        )
        self.unscheduled = EmailCampaign.objects.create(
            subject="Unscheduled", body="body", status="draft",
        )

    def test_dispatches_due_draft_and_clears_schedule(self):
        with stub_relay_submit() as mock_async:
            result = dispatch_due_campaigns()
        self.assertEqual(result["due"], 1)
        self.assertEqual(result["dispatched"], [self.due.pk])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(
            mock_async.call_args.kwargs["external_key"], f"aishl-campaign-{self.due.pk}",
        )
        self.due.refresh_from_db()
        self.assertEqual(self.due.status, "sending")
        self.assertIsNone(self.due.scheduled_at)
        self.assertIsNone(self.due.scheduled_timezone)
        self.future.refresh_from_db()
        self.assertEqual(self.future.status, "draft")
        self.unscheduled.refresh_from_db()
        self.assertEqual(self.unscheduled.status, "draft")

    def test_never_dispatches_before_scheduled_time(self):
        result = dispatch_due_campaigns(now=timezone.now() - timedelta(hours=2))
        self.assertEqual(result["due"], 0)
        self.assertEqual(result["dispatched"], [])

    def test_skips_non_draft_rows_from_the_due_scan(self):
        # A campaign whose status moved on after scheduling (manual claim)
        # must never be dispatched again.
        self.due.status = "sending"
        self.due.save(update_fields=["status"])
        result = dispatch_due_campaigns()
        self.assertEqual(result["due"], 0)
        self.due.refresh_from_db()
        self.assertEqual(self.due.status, "sending")

class ClaimConsumesScheduleTest(TestCase):
    """Manual sends (Studio or API) consume any pending schedule."""

    def test_claim_and_submit_clears_schedule_fields(self):
        from email_app.services.campaign_dispatch import claim_and_submit_campaign

        campaign = EmailCampaign.objects.create(
            subject="Claimed", body="body", status="draft",
            scheduled_at=timezone.now() + timedelta(hours=1),
            scheduled_timezone="Europe/Berlin",
        )
        with stub_relay_submit() as mock_async:
            result = claim_and_submit_campaign(
                campaign.pk, source="Studio campaign detail",
            )
        self.assertTrue(result.claimed)
        campaign.refresh_from_db()
        self.assertEqual(campaign.status, "sending")
        self.assertIsNone(campaign.scheduled_at)
        self.assertIsNone(campaign.scheduled_timezone)
        self.assertEqual(
            mock_async.call_args.kwargs["external_key"], f"aishl-campaign-{campaign.pk}",
        )
