from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from django.test import SimpleTestCase

from booking.services.availability import (
    Policy,
    SlotOffer,
    expand_viewer_range,
    generate_slots,
    local_to_utc,
    open_windows_utc,
    overlaps,
    parse_hhmm,
    subtract_own_event,
    weekly_windows_for_date,
)


class AvailabilityEngineTest(SimpleTestCase):
    def test_overlaps(self):
        # Half-open intervals: [10, 11) vs [11, 12) -> False
        t10 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
        t11 = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)
        t12 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
        self.assertFalse(overlaps(t10, t11, t11, t12))
        self.assertTrue(overlaps(t10, t11, t10, t12))
        self.assertTrue(overlaps(t10, t12, t11, t12))

    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("09:30"), 9 * 60 + 30)
        self.assertEqual(parse_hhmm("18:00"), 18 * 60)

    def test_local_to_utc_standard(self):
        tz = ZoneInfo("Europe/Berlin")
        # In summer (CEST is UTC+2)
        summer = datetime(2026, 7, 1, 10, 0)
        utc_dt = local_to_utc(summer, tz)
        self.assertIsNotNone(utc_dt)
        self.assertEqual(utc_dt.hour, 8)

        # In winter (CET is UTC+1)
        winter = datetime(2026, 1, 15, 10, 0)
        utc_winter = local_to_utc(winter, tz)
        self.assertIsNotNone(utc_winter)
        self.assertEqual(utc_winter.hour, 9)

    def test_open_windows_utc_weekly_and_override(self):
        schedule = {
            "timezone": "Europe/Berlin",
            "weekly_windows": {
                0: [("10:00", "12:00")],  # Monday
            },
            "overrides": {
                "2026-10-19": {"is_unavailable": True},  # Monday closed
                "2026-10-20": {"is_unavailable": False, "start_time": "14:00", "end_time": "16:00"},  # Tuesday custom
            },
        }

        # 2026-10-19 is Monday, but overridden to closed
        target_mon = date(2026, 10, 19)
        windows = open_windows_utc(target_mon, schedule)
        self.assertEqual(windows, [])

        # 2026-10-20 is Tuesday, overridden to 14:00-16:00
        target_tue = date(2026, 10, 20)
        windows = open_windows_utc(target_tue, schedule)
        self.assertEqual(len(windows), 1)

    def test_generate_slots_with_busy_and_policy(self):
        schedule = {
            "timezone": "UTC",
            "weekly_windows": {
                0: [("10:00", "12:00")],  # Monday 10:00 - 12:00
            },
            "overrides": {},
        }
        policy = Policy(
            grid_min=30,
            notice_hours=0.0,
            horizon_days=60,
        )
        target_mon = date(2026, 10, 19)
        now_utc = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)

        # 10:00-10:30, 10:30-11:00, 11:00-11:30, 11:30-12:00 -> 4 slots
        result = generate_slots(
            schedule=schedule,
            policy=policy,
            duration_min=30,
            from_date=target_mon,
            to_date=target_mon,
            now_utc=now_utc,
        )
        self.assertEqual(len(result["slots"]), 4)

        # With busy time at 10:30 - 11:00
        busy_span = [(
            datetime(2026, 10, 19, 10, 30, tzinfo=timezone.utc),
            datetime(2026, 10, 19, 11, 0, tzinfo=timezone.utc),
        )]
        result_busy = generate_slots(
            schedule=schedule,
            policy=policy,
            duration_min=30,
            busy=busy_span,
            from_date=target_mon,
            to_date=target_mon,
            now_utc=now_utc,
        )
        self.assertEqual(len(result_busy["slots"]), 3)
        starts = [s.start.strftime("%H:%M") for s in result_busy["slots"]]
        self.assertIn("10:00", starts)
        self.assertNotIn("10:30", starts)
        self.assertIn("11:00", starts)
        self.assertIn("11:30", starts)

    def test_subtract_own_event(self):
        b1 = (datetime(2026, 10, 19, 10, 0), datetime(2026, 10, 19, 10, 30))
        b2 = (datetime(2026, 10, 19, 11, 0), datetime(2026, 10, 19, 11, 30))
        busy = [b1, b2]
        # Rescheduling b1: subtract b1 from busy
        remaining = subtract_own_event(busy, b1)
        self.assertEqual(remaining, [b2])

    def test_expand_viewer_range(self):
        v_from = date(2026, 10, 19)
        v_to = date(2026, 10, 20)
        h_from, h_to = expand_viewer_range(v_from, v_to, "America/New_York", "Europe/Berlin")
        self.assertLessEqual(h_from, v_from)
        self.assertGreaterEqual(h_to, v_to)
