"""A self-paced enrollment must never shadow a dated cohort's sessions.

Course access auto-creates a self-paced ``CohortEnrollment``, so a learner
later added to a dated cohort holds both. The session unit page and the
course Home session schedule must resolve the dated cohort's series event,
and a staff viewer previewing a cohort with ``?cohort=`` must see it too.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from content.services.course_commitments import build_course_commitments
from events.models import Event, EventSeries

User = get_user_model()


class SessionCohortPreferenceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.course = Course.objects.create(
            title='Buildcamp', slug='session-pref', status='published',
            required_level=0,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='RAG', slug='rag', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module, title='Session 2', slug='session',
            sort_order=1, kind='event', session_position=2,
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        cls.series = EventSeries.objects.create(
            name='Office hours cohort 4', slug='session-pref-oh-4',
            cadence='none', day_of_week=None, start_time=None,
            visibility='hidden',
        )
        # A cohort that starts tomorrow is not a "past" fallback cohort, so
        # only an enrollment- or selection-based lookup can reach its series.
        cls.cohort4 = Cohort.objects.create(
            course=cls.course, name='Cohort 4', mode='cohort',
            external_key='cohort-4',
            start_date=today + datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=60),
            event_series=cls.series,
        )
        cls.event = Event.objects.create(
            title='Office hours session 2', slug='session-pref-oh-4-s2',
            event_series=cls.series, series_position=2,
            start_datetime=timezone.now() + datetime.timedelta(days=2),
            status='upcoming', published=True, origin='studio',
            description='Session two agenda.',
        )
        cls.learner = User.objects.create_user(
            email='both-enrollments@example.com', password='pw',
            email_verified=True,
        )
        # Self-paced enrollment first, so an unordered ``.first()`` picks it.
        CohortEnrollment.objects.create(user=cls.learner, cohort=cls.self_paced)
        CohortEnrollment.objects.create(user=cls.learner, cohort=cls.cohort4)
        cls.staff = User.objects.create_user(
            email='staff-no-enrollment@example.com', password='pw',
            email_verified=True, is_staff=True,
        )

    def test_unit_page_shows_dated_cohort_session_over_self_paced(self):
        self.client.force_login(self.learner)
        response = self.client.get(self.unit.get_absolute_url())
        self.assertEqual(response.context['unit_session_entry']['event'], self.event)
        self.assertNotContains(response, 'data-testid="unit-session-not-scheduled"')

    def test_course_home_schedules_dated_cohort_session_over_self_paced(self):
        self.client.force_login(self.learner)
        response = self.client.get(f'/courses/{self.course.slug}/home')
        self.assertEqual(response.context['unscheduled_session_rows'], [])
        self.assertEqual(
            [row['event'] for row in response.context['live_session_schedule']],
            [self.event],
        )

    def test_self_paced_schedule_rows_prefer_learners_dated_enrollment(self):
        # The course Home schedule builder falls back to session resolution
        # when the displayed cohort is self-paced; it must still reach the
        # learner's dated Cohort 4 series instead of "Not scheduled".
        commitments = build_course_commitments(self.course, self.learner, self.self_paced)
        self.assertEqual(
            [row['event'] for row in commitments['live_session_schedule']],
            [self.event],
        )

    def test_staff_without_enrollment_previewing_cohort_sees_session(self):
        self.client.force_login(self.staff)
        response = self.client.get(
            self.unit.get_absolute_url() + '?cohort=cohort-4',
        )
        self.assertEqual(response.context['unit_session_entry']['event'], self.event)
