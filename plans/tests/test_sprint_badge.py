"""Tests for the date-derived sprint lifecycle badge (issue #979).

The badge is computed from ``start_date`` / ``end_date`` via
``Sprint.sprint_badge(now=...)``. ``now`` is injected as a fixed ``date``
so every state is deterministic and the suite never depends on real time.
"""

import datetime

from django.test import TestCase, override_settings

from integrations.config import clear_config_cache
from plans.models import Sprint


class SprintBadgeStateTest(TestCase):
    """All five states + both boundary days over an injected ``now``.

    Fixture: start 2026-06-01, 6 weeks -> end_date 2026-07-13, window W=7.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
            duration_weeks=6, status='active',
        )

    def test_end_date_matches_fixture(self):
        # Sanity-check the derived end so the date assertions below are
        # anchored to the documented 2026-07-13 boundary.
        self.assertEqual(self.sprint.end_date, datetime.date(2026, 7, 13))

    def test_upcoming_more_than_window_before_start(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 5, 1))
        self.assertEqual(badge.state, 'upcoming')
        self.assertEqual(badge.label, 'Upcoming')

    def test_starting_soon_within_window_before_start(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 5, 28))
        self.assertEqual(badge.state, 'starting_soon')
        self.assertEqual(badge.label, 'Starting soon')

    def test_active_on_start_day_boundary(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 6, 1))
        self.assertEqual(badge.state, 'active')
        self.assertEqual(badge.label, 'Active')

    def test_active_mid_window(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 6, 20))
        self.assertEqual(badge.state, 'active')

    def test_ending_soon_within_window_of_end(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 7, 10))
        self.assertEqual(badge.state, 'ending_soon')
        self.assertEqual(badge.label, 'Ending soon')

    def test_ending_soon_on_end_day_boundary(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 7, 13))
        self.assertEqual(badge.state, 'ending_soon')

    def test_ended_day_after_end(self):
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 7, 14))
        self.assertEqual(badge.state, 'ended')
        self.assertEqual(badge.label, 'Ended')

    def test_each_state_carries_a_distinct_css_class(self):
        # The pill colour must vary by state (colour table in #979); the
        # neutral upcoming pill must not be reused for the populated states.
        states = {
            datetime.date(2026, 5, 1): 'upcoming',
            datetime.date(2026, 5, 28): 'starting_soon',
            datetime.date(2026, 6, 1): 'active',
            datetime.date(2026, 7, 10): 'ending_soon',
            datetime.date(2026, 7, 14): 'ended',
        }
        css = {
            self.sprint.sprint_badge(now=d).css_class for d in states
        }
        self.assertEqual(len(css), len(states))
        # Active is emerald, ending soon is amber -- the two "live" states a
        # member most needs to tell apart.
        self.assertIn(
            'emerald',
            self.sprint.sprint_badge(now=datetime.date(2026, 6, 1)).css_class,
        )
        self.assertIn(
            'amber',
            self.sprint.sprint_badge(now=datetime.date(2026, 7, 10)).css_class,
        )


class SprintBadgeShortSprintTest(TestCase):
    """A sprint shorter than W never reads Active (overlap rule, #979).

    ``end_date`` is derived from whole ``duration_weeks``, so the shortest
    expressible sprint is 1 week (7 days). To exercise a sprint *shorter*
    than the window we widen ``W`` to 14 via config: a 7-day sprint is then
    strictly shorter than W (``e - W < s``), so its start/end windows
    overlap and it must skip ``active`` entirely.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Short', slug='short',
            start_date=datetime.date(2026, 6, 1),
            duration_weeks=1,  # end_date 2026-06-08, length 7 days < W=14
            status='active',
        )

    def tearDown(self):
        clear_config_cache()

    @override_settings(SPRINT_BADGE_WINDOW_DAYS='14')
    def test_short_sprint_never_active(self):
        clear_config_cache()
        observed = set()
        for offset in range(-21, 22):
            day = self.sprint.start_date + datetime.timedelta(days=offset)
            observed.add(self.sprint.sprint_badge(now=day).state)
        self.assertNotIn('active', observed)

    @override_settings(SPRINT_BADGE_WINDOW_DAYS='14')
    def test_short_sprint_goes_starting_to_ending_to_ended(self):
        clear_config_cache()
        # Before start (within widened window): Starting soon.
        self.assertEqual(
            self.sprint.sprint_badge(now=datetime.date(2026, 5, 28)).state,
            'starting_soon',
        )
        # On the start day and across the s..e range: Ending soon (overlap).
        self.assertEqual(
            self.sprint.sprint_badge(now=datetime.date(2026, 6, 1)).state,
            'ending_soon',
        )
        self.assertEqual(
            self.sprint.sprint_badge(now=datetime.date(2026, 6, 8)).state,
            'ending_soon',
        )
        # Day after end_date: Ended.
        self.assertEqual(
            self.sprint.sprint_badge(now=datetime.date(2026, 6, 9)).state,
            'ended',
        )


@override_settings()
class SprintBadgeConfigTest(TestCase):
    """The window length is read from SPRINT_BADGE_WINDOW_DAYS (#979)."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='Config', slug='config',
            start_date=datetime.date(2026, 6, 1),
            duration_weeks=6, status='active',
        )

    def tearDown(self):
        clear_config_cache()

    def test_default_window_is_seven(self):
        # 10 days before start is upcoming under the default W=7.
        clear_config_cache()
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 5, 22))
        self.assertEqual(badge.state, 'upcoming')

    @override_settings(SPRINT_BADGE_WINDOW_DAYS='14')
    def test_override_widens_starting_soon_window(self):
        clear_config_cache()
        # 10 days before start is now within the 14-day window -> starting_soon.
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 5, 22))
        self.assertEqual(badge.state, 'starting_soon')

    @override_settings(SPRINT_BADGE_WINDOW_DAYS='')
    def test_blank_override_falls_back_to_default(self):
        clear_config_cache()
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 5, 22))
        self.assertEqual(badge.state, 'upcoming')

    @override_settings(SPRINT_BADGE_WINDOW_DAYS='not-a-number')
    def test_non_numeric_override_falls_back_to_default(self):
        clear_config_cache()
        # Must not raise; falls back to 7 -> still upcoming 10 days out.
        badge = self.sprint.sprint_badge(now=datetime.date(2026, 5, 22))
        self.assertEqual(badge.state, 'upcoming')


class SprintBadgeDisplayOnlyTest(TestCase):
    """The badge never reads or mutates the stored status field (#979)."""

    def test_status_active_but_ended_badge_leaves_status_unchanged(self):
        sprint = Sprint.objects.create(
            name='Stale', slug='stale',
            start_date=datetime.date(2026, 1, 1),
            duration_weeks=6,  # end_date 2026-02-12, well in the past
            status='active',
        )
        # A sprint whose dates are over but whose stored status is still
        # 'active' reads 'ended' on the badge...
        badge = sprint.sprint_badge(now=datetime.date(2026, 6, 1))
        self.assertEqual(badge.state, 'ended')
        # ...and computing the badge does not change the stored status.
        sprint.refresh_from_db()
        self.assertEqual(sprint.status, 'active')
