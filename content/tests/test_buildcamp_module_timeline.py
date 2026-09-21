"""Cohort-relative dates for Buildcamp's six weeks and three-week capstone."""

import datetime

from django.test import TestCase

from content.models import Cohort, Course, Module
from content.services.course_units import build_module_week_dates


class BuildcampModuleTimelineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Buildcamp', slug='ai-buildcamp', status='published', required_level=0,
        )
        cls.logistics = Module.objects.create(
            course=cls.course, title='Course Logistics', slug='logistics', sort_order=0,
        )
        cls.weeks = [
            Module.objects.create(
                course=cls.course, title=f'Week {number}', slug=f'week-{number}',
                sort_order=number, available_after_days=(number - 1) * 7,
            )
            for number in range(1, 7)
        ]
        cls.capstone = Module.objects.create(
            course=cls.course, title='Capstone Project', slug='capstone',
            sort_order=7, available_after_days=42,
        )
        cls.optional = Module.objects.create(
            course=cls.course, title='Optional', slug='optional', sort_order=10,
            is_bonus=True,
        )
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4', mode='cohort',
            is_active=True, start_date=datetime.date(2026, 9, 21),
            end_date=datetime.date(2026, 11, 22),
        )

    def test_each_module_gets_its_cohort_range(self):
        ranges = build_module_week_dates(
            self.course.get_syllabus(), self.cohort,
            extend_final_to_cohort_end=True,
        )
        self.assertEqual(
            ranges[self.weeks[0].pk],
            (datetime.date(2026, 9, 21), datetime.date(2026, 9, 27)),
        )
        self.assertEqual(
            ranges[self.weeks[5].pk],
            (datetime.date(2026, 10, 26), datetime.date(2026, 11, 1)),
        )
        self.assertEqual(
            ranges[self.capstone.pk],
            (datetime.date(2026, 11, 2), datetime.date(2026, 11, 22)),
        )
        self.assertNotIn(self.logistics.pk, ranges)
        self.assertNotIn(self.optional.pk, ranges)

    def test_public_buildcamp_schedule_uses_the_module_ranges(self):
        response = self.client.get('/courses/ai-buildcamp?cohort=4')
        self.assertEqual(response.context['module_week_ranges'][self.weeks[0].pk], 'Sep 21–27')
        self.assertEqual(response.context['module_week_ranges'][self.capstone.pk], 'Nov 2–22')

    def test_default_last_week_still_spans_seven_days(self):
        ranges = build_module_week_dates(self.course.get_syllabus(), self.cohort)
        self.assertEqual(
            ranges[self.capstone.pk],
            (datetime.date(2026, 11, 2), datetime.date(2026, 11, 8)),
        )
