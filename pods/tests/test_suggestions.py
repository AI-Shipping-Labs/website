"""Suggested meeting times: per-date DST expansion, 15-minute grid, ranking
and diversity (issue #1918)."""

from datetime import UTC, datetime

from django.test import SimpleTestCase, tag
from freezegun import freeze_time

from pods.services.suggestions import (
    MemberAvailability,
    slots_attendable,
    suggest_slots,
    weekly_overlap_hours,
)

TUESDAY = 1


def member(name, zone, windows):
    """``windows``: ``(weekday, 'HH:MM', 'HH:MM'[, pref])``."""
    user = type('U', (), {'first_name': name, 'last_name': '', 'pk': name})()
    parsed = []
    for weekday, start, end, *rest in windows:
        sh, sm = (int(x) for x in start.split(':'))
        eh, em = (int(x) for x in end.split(':'))
        parsed.append((weekday, sh * 60 + sm, eh * 60 + em, rest[0] if rest else 'preferred'))
    return MemberAvailability(user=user, timezone_name=zone, windows=parsed)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


@tag('core')
class DstPerDateExpansionTest(SimpleTestCase):
    berlin = member('Anna', 'Europe/Berlin', [(TUESDAY, '18:00', '20:00')])
    new_york = member('Mike', 'America/New_York', [(TUESDAY, '12:00', '14:00')])

    @freeze_time('2026-07-06 08:00:00')  # date-rot-ok: frozen July clock (both zones on summer time)
    def test_july_tuesday_overlaps_16_to_18_utc(self):
        slots = suggest_slots([self.berlin, self.new_york], 120, horizon_days=2, count=5)
        self.assertEqual([(s.start, s.end) for s in slots], [(utc(2026, 7, 7, 16), utc(2026, 7, 7, 18))])

    @freeze_time('2026-03-16 08:00:00')  # date-rot-ok: New York on EDT, Berlin still on CET
    def test_march_tuesday_overlaps_only_17_to_18_utc(self):
        self.assertEqual(suggest_slots([self.berlin, self.new_york], 120, horizon_days=2, count=5), [])
        slots = suggest_slots([self.berlin, self.new_york], 60, horizon_days=2, count=5)
        self.assertEqual([s.start for s in slots], [utc(2026, 3, 17, 17)])
        strip = {row['name']: row['time'] for row in slots[0].local_starts}
        self.assertEqual(strip, {'Anna': '18:00', 'Mike': '13:00'})


@tag('core')
class QuarterHourZoneTest(SimpleTestCase):
    @freeze_time('2026-07-06 00:00:00')  # date-rot-ok: frozen Monday
    def test_kathmandu_member_matches_on_15_minute_grid(self):
        kathmandu = member('Raj', 'Asia/Kathmandu', [(2, '20:00', '21:00')])
        london_utc = member('Lea', 'UTC', [(2, '14:00', '16:00')])
        slots = suggest_slots([kathmandu, london_utc], 60, horizon_days=4, count=5)
        self.assertEqual([s.start for s in slots], [utc(2026, 7, 8, 14, 15)])


@tag('core')
class RankingAndDiversityTest(SimpleTestCase):
    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday, one week horizon
    def test_rank_by_attendance_then_if_needed_then_social_hours_then_time(self):
        a = member('A', 'UTC', [(0, '10:00', '11:00'), (1, '12:00', '13:00'), (2, '06:00', '07:00'), (3, '10:00', '11:00')])
        b = member('B', 'UTC', [(0, '10:00', '11:00'), (1, '12:00', '13:00'), (2, '06:00', '07:00'), (3, '10:00', '11:00')])
        c = member('C', 'UTC', [(0, '10:00', '11:00', 'if_needed'), (1, '12:00', '13:00'), (2, '06:00', '07:00')])
        slots = suggest_slots([a, b, c], 60, horizon_days=7, count=5)
        self.assertEqual(
            [s.start for s in slots],
            [utc(2026, 7, 7, 12), utc(2026, 7, 8, 6), utc(2026, 7, 6, 10), utc(2026, 7, 9, 10)],
        )
        self.assertEqual(
            [s.fit_label for s in slots],
            ['Everyone - works well', 'Everyone - works well', 'Everyone - some if needed', 'Missing 1: C'],
        )
        self.assertEqual(len(suggest_slots([a, b, c], 60, horizon_days=7, count=2)), 2)

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday, two week horizon
    def test_never_two_slots_on_same_utc_date_or_same_weekday_and_time(self):
        a = member('A', 'UTC', [(TUESDAY, '12:00', '14:00')])
        b = member('B', 'UTC', [(TUESDAY, '12:00', '14:00')])
        slots = suggest_slots([a, b], 60, horizon_days=14, count=5)
        self.assertEqual([s.start for s in slots], [utc(2026, 7, 7, 12), utc(2026, 7, 14, 12, 15)])

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_four_members_need_three_and_two_members_need_both(self):
        a = member('A', 'UTC', [(TUESDAY, '12:00', '13:00')])
        b = member('B', 'UTC', [(TUESDAY, '12:00', '13:00')])
        c = member('C', 'UTC', [(3, '12:00', '13:00')])
        d = member('D', 'UTC', [(4, '12:00', '13:00')])
        self.assertEqual(suggest_slots([a, b, c, d], 60, horizon_days=7, count=5), [])
        self.assertEqual(len(suggest_slots([a, b], 60, horizon_days=7, count=5)), 1)
        self.assertEqual(suggest_slots([a, c], 60, horizon_days=7, count=5), [])
        self.assertEqual(suggest_slots([a], 60, horizon_days=7, count=5), [])

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_members_without_windows_are_not_considered(self):
        a = member('A', 'UTC', [(TUESDAY, '12:00', '13:00')])
        b = member('B', 'UTC', [(TUESDAY, '12:00', '13:00')])
        empty = member('Mike', 'America/New_York', [])
        slots = suggest_slots([a, b, empty], 60, horizon_days=7, count=5, all_members=[a, b, empty])
        self.assertEqual(slots[0].considered, 2)
        self.assertEqual(slots[0].missing, [])
        self.assertEqual([row['name'] for row in slots[0].local_starts], ['A', 'B', 'Mike'])


@tag('core')
class FitTest(SimpleTestCase):
    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_weekly_overlap_hours_rounds_down(self):
        viewer = member('V', 'UTC', [(TUESDAY, '12:00', '15:30'), (3, '12:00', '13:00')])
        other = member('O', 'UTC', [(TUESDAY, '12:00', '18:00')])
        self.assertEqual(weekly_overlap_hours(viewer, [other]), 3)
        self.assertEqual(weekly_overlap_hours(viewer, [member('X', 'UTC', [(5, '01:00', '02:00')])]), 0)
        self.assertIsNone(weekly_overlap_hours(member('N', 'UTC', []), [other]))

    @freeze_time('2026-07-05 00:00:00')  # date-rot-ok: frozen Sunday
    def test_requester_fit_counts_attendable_suggested_slots(self):
        a = member('A', 'UTC', [(0, '12:00', '13:00'), (1, '12:00', '13:00')])
        b = member('B', 'UTC', [(0, '12:00', '13:00'), (1, '12:00', '13:00')])
        slots = suggest_slots([a, b], 60, horizon_days=7, count=5)
        requester = member('R', 'UTC', [(1, '11:00', '14:00')])
        self.assertEqual((slots_attendable(requester, slots), len(slots)), (1, 2))
