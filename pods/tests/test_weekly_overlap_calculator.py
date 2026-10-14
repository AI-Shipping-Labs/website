"""The pod-list fit reuses one grid across pods (perf lane 2026-10-14).

``build_pod_rows`` used to call ``weekly_overlap_hours`` once per pod; each
call built a fresh 7-day grid and re-expanded the same viewer's and the
same members' availability. The shared ``WeeklyOverlapCalculator`` keeps
identical results while expanding each member once per request.
"""

from datetime import UTC, datetime

from django.test import SimpleTestCase, tag
from freezegun import freeze_time

from pods.services.suggestions import (
    GRID_MINUTES,
    MemberAvailability,
    WeeklyOverlapCalculator,
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


@tag('core')
class WeeklyOverlapCalculatorTest(SimpleTestCase):
    viewer = member('You', 'Europe/Berlin', [(TUESDAY, '09:00', '18:00')])
    anna = member('Anna', 'Europe/Berlin', [(TUESDAY, '10:00', '12:00')])
    mike = member('Mike', 'America/New_York', [(TUESDAY, '12:00', '14:00')])

    @freeze_time('2026-07-06 08:00:00')  # date-rot-ok: frozen July clock (both zones on summer time)
    def test_matches_weekly_overlap_hours(self):
        now = datetime(2026, 7, 6, 8, 0, tzinfo=UTC)
        calculator = WeeklyOverlapCalculator(now)
        for others in ([self.anna], [self.mike], [self.anna, self.mike], []):
            with self.subTest(others=len(others)):
                self.assertEqual(
                    calculator.hours(self.viewer, others),
                    weekly_overlap_hours(self.viewer, others, now=now),
                )

    @freeze_time('2026-07-06 08:00:00')
    def test_expands_each_member_once_across_pods(self):
        calculator = WeeklyOverlapCalculator()
        calculator.hours(self.viewer, [self.anna, self.mike])
        self.assertEqual(len(calculator.grid._memo), 3)
        # Two more pods with the same members expand nothing new: the grid
        # and its per-member cells are shared across the request.
        calculator.hours(self.viewer, [self.anna])
        calculator.hours(self.viewer, [self.mike, self.anna])
        self.assertEqual(len(calculator.grid._memo), 3)

    def test_no_viewer_availability_reads_none_without_expansion(self):
        calculator = WeeklyOverlapCalculator()
        empty = MemberAvailability(user=self.viewer.user, timezone_name='UTC', windows=[])
        self.assertIsNone(calculator.hours(empty, [self.anna]))

    @freeze_time('2026-07-06 08:00:00')
    def test_shared_grid_matches_fit_horizon_size(self):
        calculator = WeeklyOverlapCalculator()
        self.assertEqual(
            calculator.grid.size,
            7 * 24 * 60 // GRID_MINUTES,
        )
