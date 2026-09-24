"""Tests for ``Cohort.event_series`` and the member-only Home session list.

Kept in its own file (rather than extending ``test_cohorts.py``) to avoid
touching a file the parallel #1659 engineer is also likely to edit on
``content.models.cohort`` / its tests.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Cohort, CohortEnrollment, Course
from events.models import Event, EventSeries
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_series(**kwargs):
    kwargs.setdefault('cadence', 'none')
    kwargs.setdefault('day_of_week', None)
    kwargs.setdefault('start_time', None)
    return EventSeries.objects.create(**kwargs)


class CohortEventSeriesFieldTest(TestCase):
    """Model-level FK semantics, mirroring Sprint/Book precedent."""

    def setUp(self):
        self.course = Course.objects.create(
            title='FK Course', slug='fk-course', status='published',
        )

    def test_event_series_is_optional(self):
        cohort = Cohort.objects.create(
            course=self.course, name='No series',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
        )
        self.assertIsNone(cohort.event_series)

    def test_event_series_link_persists(self):
        series = _make_series(name='OH', slug='oh-fk-1660')
        cohort = Cohort.objects.create(
            course=self.course, name='Linked',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            event_series=series,
        )
        cohort.refresh_from_db()
        self.assertEqual(cohort.event_series_id, series.pk)

    def test_deleting_series_unlinks_cohort_but_keeps_it(self):
        series = _make_series(name='Deletable', slug='deletable-fk-1660')
        cohort = Cohort.objects.create(
            course=self.course, name='Survives',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            event_series=series,
        )
        series.delete()
        cohort.refresh_from_db()
        self.assertIsNone(cohort.event_series)
        self.assertTrue(Cohort.objects.filter(pk=cohort.pk).exists())

    def test_one_series_can_back_multiple_cohorts(self):
        series = _make_series(name='Shared', slug='shared-fk-1660')
        cohort_a = Cohort.objects.create(
            course=self.course, name='A',
            start_date=datetime.date(2026, 3, 1),
            end_date=datetime.date(2026, 6, 1),
            event_series=series,
        )
        cohort_b = Cohort.objects.create(
            course=self.course, name='B',
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 9, 1),
            event_series=series,
        )
        self.assertEqual(series.cohorts.count(), 2)
        self.assertIn(cohort_a, series.cohorts.all())
        self.assertIn(cohort_b, series.cohorts.all())


class CourseLiveSessionsHomeTest(TierSetupMixin, TestCase):
    """Live sessions are scoped to the learner's selected Home cohort."""

    def setUp(self):
        self.course = Course.objects.create(
            title='Buildcamp', slug='buildcamp-live-1660', status='published',
        )
        self.series = _make_series(
            name='Buildcamp Office Hours', slug='buildcamp-oh-1660',
            visibility='hidden',
        )
        self.cohort = Cohort.objects.create(
            course=self.course, name='Cohort 1',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=self.series,
        )
        self.upcoming_event = Event.objects.create(
            title='Office Hours — Upcoming',
            slug='office-hours-upcoming-1660',
            start_datetime=datetime.datetime.now(
                tz=datetime.timezone.utc,
            ) + datetime.timedelta(days=3),
            status='upcoming',
            published=True,
            event_series=self.series,
            origin='studio',
        )
        self.past_event = Event.objects.create(
            title='Office Hours — Past',
            slug='office-hours-past-1660',
            start_datetime=datetime.datetime.now(
                tz=datetime.timezone.utc,
            ) - datetime.timedelta(days=3),
            status='completed',
            published=True,
            event_series=self.series,
            origin='studio',
            recap_notes='What we discussed last week.',
        )

    def _course_url(self):
        return f'/courses/{self.course.slug}'

    def test_anonymous_sees_no_live_sessions_block(self):
        response = self.client.get(self._course_url())
        self.assertNotContains(response, 'data-testid="course-live-sessions"')
        self.assertNotContains(response, 'Office Hours — Upcoming')

    def test_non_entitled_authenticated_sees_no_block(self):
        user = User.objects.create_user(
            email='free-1660@test.com', password='testpass',
        )
        self.client.login(email='free-1660@test.com', password='testpass')
        detail = self.client.get(self._course_url())
        home = self.client.get(f'{self._course_url()}/home')
        self.assertNotContains(detail, 'data-testid="course-live-sessions"')
        self.assertNotContains(home, 'data-testid="course-home-live-sessions"')
        self.assertNotContains(home, self.upcoming_event.title)
        self.assertFalse(CohortEnrollment.objects.filter(user=user).exists())

    def test_course_detail_hides_full_list_and_home_shows_join_and_recap_links(self):
        user = User.objects.create_user(
            email='entitled-1660@test.com', password='testpass',
        )
        CohortEnrollment.objects.create(cohort=self.cohort, user=user)
        self.client.login(email='entitled-1660@test.com', password='testpass')
        detail = self.client.get(self._course_url())
        home = self.client.get(f'{self._course_url()}/home')
        self.assertNotContains(detail, 'data-testid="course-live-sessions"')
        self.assertNotContains(detail, 'Office Hours — Upcoming')
        self.assertContains(home, 'data-testid="course-home-live-sessions"')
        self.assertContains(home, self.upcoming_event.title)
        self.assertContains(home, self.past_event.title)
        self.assertContains(
            home, self.past_event.get_recap_url(),
        )

    def test_staff_does_not_get_a_private_session_list_on_course_detail(self):
        User.objects.create_user(
            email='staff-1660@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff-1660@test.com', password='testpass')
        response = self.client.get(self._course_url())
        self.assertNotContains(response, 'data-testid="course-live-sessions"')
        self.assertNotContains(response, 'Office Hours — Upcoming')

    def test_draft_occurrence_is_excluded_from_block(self):
        Event.objects.create(
            title='Office Hours — Draft',
            slug='office-hours-draft-1660',
            start_datetime=datetime.datetime.now(
                tz=datetime.timezone.utc,
            ) + datetime.timedelta(days=5),
            status='draft',
            published=False,
            event_series=self.series,
            origin='studio',
        )
        user = User.objects.create_user(
            email='entitled-draft-1660@test.com', password='testpass',
        )
        CohortEnrollment.objects.create(cohort=self.cohort, user=user)
        self.client.login(
            email='entitled-draft-1660@test.com', password='testpass',
        )
        response = self.client.get(self._course_url())
        self.assertNotContains(response, 'Office Hours — Draft')
