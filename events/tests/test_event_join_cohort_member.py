"""Dated cohort members may use the join link without registering.

The reminder emails send cohort members to their course session unit, whose
join partial links to ``event_join``. The join gate therefore accepts the
same audience: registrants plus members of dated cohorts linked to the
event's series. Everyone else is still sent to the event page.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course
from events.models import Event, EventJoinClick, EventSeries

User = get_user_model()


class CohortMemberJoinTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(
            title='Buildcamp', slug='join-cohort-course', status='published',
        )
        series = EventSeries.objects.create(
            name='Buildcamp sessions', slug='join-cohort-series',
            cadence='none', day_of_week=None, start_time=None,
            visibility='hidden',
        )
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1), event_series=series,
        )
        cls.member = User.objects.create_user(
            email='join-cohort-member@test.com', password='pw',
        )
        CohortEnrollment.objects.create(user=cls.member, cohort=cohort)
        cls.outsider = User.objects.create_user(
            email='join-cohort-outsider@test.com', password='pw',
        )
        start = timezone.now() + datetime.timedelta(minutes=2)
        cls.event = Event.objects.create(
            event_series=series, series_position=1, title='Session 1',
            slug='join-cohort-session-1', status='upcoming', published=True,
            origin='studio', start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=1),
            zoom_join_url='https://zoom.us/j/987654321',
        )

    def test_cohort_member_without_registration_is_sent_to_zoom(self):
        self.client.force_login(self.member)

        response = self.client.get(self.event.get_join_url())

        self.assertRedirects(
            response, 'https://zoom.us/j/987654321',
            fetch_redirect_response=False,
        )
        self.assertTrue(
            EventJoinClick.objects.filter(
                event=self.event, user=self.member,
            ).exists()
        )

    def test_non_member_without_registration_goes_to_event_page(self):
        self.client.force_login(self.outsider)

        response = self.client.get(self.event.get_join_url())

        self.assertRedirects(
            response, self.event.get_absolute_url(),
            fetch_redirect_response=False,
        )
