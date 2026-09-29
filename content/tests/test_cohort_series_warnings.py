"""Cohort event-series health warnings.

A dated cohort whose sessions cannot resolve an event renders every
session as "Not scheduled" (the Cohort 4 production incident).
``cohort_series_warnings`` is the single owner of that diagnosis; these
tests pin its three cases and the event-series picker ordering.
"""

import datetime

from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, Course, Module, Unit
from content.services.course_cohorts import (
    active_cohorts_with_series_warnings,
    cohort_event_series_options,
    cohort_series_warnings,
)
from events.models import Event, EventSeries


def _event(series, position, status='upcoming'):
    return Event.objects.create(
        title=f'{series.slug} {position}', slug=f'{series.slug}-{position}-{status}',
        event_series=series, series_position=position,
        start_datetime=timezone.now() + datetime.timedelta(days=position),
        status=status, published=True, origin='studio',
    )


class CohortSeriesWarningsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.course = Course.objects.create(
            title='Buildcamp', slug='warn-buildcamp', status='published',
        )
        module = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        for position in (1, 2, 3):
            Unit.objects.create(
                module=module, title=f'Session {position}',
                slug=f'session-{position}', sort_order=position,
                kind='event', session_position=position,
            )
        cls.full_series = EventSeries.objects.create(
            name='OH full', slug='warn-oh-full', cadence='none',
            day_of_week=None, start_time=None,
        )
        for position in (1, 2, 3):
            _event(cls.full_series, position)
        cls.empty_series = EventSeries.objects.create(
            name='[DELETE ME in Studio] duplicate', slug='warn-oh-empty',
            cadence='none', day_of_week=None, start_time=None,
        )
        # A draft event is not published, so the series still counts as empty.
        _event(cls.empty_series, 1, status='draft')
        cls.gap_series = EventSeries.objects.create(
            name='OH gap', slug='warn-oh-gap', cadence='none',
            day_of_week=None, start_time=None,
        )
        _event(cls.gap_series, 1)
        _event(cls.gap_series, 3, status='cancelled')

        def dated(name, series):
            return Cohort.objects.create(
                course=cls.course, name=name, mode='cohort',
                start_date=today - datetime.timedelta(days=1),
                end_date=today + datetime.timedelta(days=30),
                event_series=series,
            )

        cls.healthy = dated('Healthy', cls.full_series)
        cls.unlinked = dated('Unlinked', None)
        cls.empty = dated('Empty', cls.empty_series)
        cls.gap = dated('Gap', cls.gap_series)
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )

    def test_healthy_and_self_paced_cohorts_have_no_warnings(self):
        warnings = cohort_series_warnings([self.healthy, self.self_paced])
        self.assertEqual(warnings, {self.healthy.pk: [], self.self_paced.pk: []})

    def test_cohort_without_series_is_an_error(self):
        [warning] = cohort_series_warnings([self.unlinked])[self.unlinked.pk]
        self.assertEqual(
            (warning['code'], warning['level']), ('no_event_series', 'error'),
        )

    def test_ended_cohort_without_series_is_only_an_info_note(self):
        today = timezone.localdate()
        ended = Cohort.objects.create(
            course=self.course, name='Cohort 1', mode='cohort',
            start_date=today - datetime.timedelta(days=90),
            end_date=today - datetime.timedelta(days=1),
        )
        [warning] = cohort_series_warnings([ended])[ended.pk]
        self.assertEqual(
            (warning['code'], warning['level'], warning['message']),
            ('ended_without_event_series', 'info', 'Ended cohort, no live sessions linked.'),
        )

    def test_series_with_no_published_events_is_a_warning(self):
        [warning] = cohort_series_warnings([self.empty])[self.empty.pk]
        self.assertEqual(
            (warning['code'], warning['level']), ('empty_event_series', 'warning'),
        )

    def test_series_missing_session_positions_is_flagged(self):
        [warning] = cohort_series_warnings([self.gap])[self.gap.pk]
        self.assertEqual(warning['code'], 'missing_session_positions')
        # Position 3 exists only as a cancelled event, so it is uncovered too.
        self.assertEqual(warning['missing_positions'], [2, 3])
        self.assertIn('session position 2, 3', warning['message'])

    def test_health_check_skips_ended_and_inactive_cohorts(self):
        today = timezone.localdate()
        Cohort.objects.create(
            course=self.course, name='Old', mode='cohort',
            start_date=today - datetime.timedelta(days=90),
            end_date=today - datetime.timedelta(days=60),
        )
        Cohort.objects.create(
            course=self.course, name='Off', mode='cohort', is_active=False,
            start_date=today, end_date=today + datetime.timedelta(days=30),
        )
        flagged = {cohort.name for cohort, _ in active_cohorts_with_series_warnings()}
        self.assertEqual(flagged, {'Unlinked', 'Empty', 'Gap'})

    def test_picker_lists_counts_and_sorts_retired_series_last(self):
        EventSeries.objects.create(
            name='AAA inactive', slug='warn-inactive', cadence='none',
            day_of_week=None, start_time=None, is_active=False,
        )
        options = cohort_event_series_options()
        self.assertEqual(
            [(s.slug, s.num_events, s.is_retired) for s in options],
            [
                ('warn-oh-full', 3, False),
                ('warn-oh-gap', 2, False),
                ('warn-inactive', 0, True),
                ('warn-oh-empty', 1, True),
            ],
        )
