"""Join states on an upcoming course session unit.

Before the join window a dated cohort member sees a muted note saying when
the join link appears, with the email-reminder sentence only when the 24h/20m
reminder emails will actually reach them. Inside the window the unit renders
the event page's own join partial. A past session keeps its recording/recap
layout and shows no join UI.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Cohort, CohortEnrollment, Course, Module, Unit
from email_app.models import EmailLog
from events.models import Event, EventSeries
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

NOTE_TESTID = 'data-testid="unit-session-join-note"'
REMINDER_TESTID = 'data-testid="unit-session-reminder-note"'
JOIN_NOW_TESTID = 'data-testid="event-join-now"'


class SessionUnitJoinStateTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(
            title='Buildcamp', slug='join-state-course', status='published',
            required_level=0,
        )
        week = Module.objects.create(
            course=course, title='Week 3', slug='week-3', sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=week, title='Session 3', slug='session', sort_order=1,
            kind='event', session_position=3,
        )
        series = EventSeries.objects.create(
            name='Buildcamp sessions', slug='join-state-series',
            cadence='none', day_of_week=None, start_time=None,
            visibility='hidden',
        )
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4', mode='cohort',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1), event_series=series,
            external_key='4',
        )
        cls.member = User.objects.create_user(
            email='join-state-member@test.com', password='pw',
        )
        CohortEnrollment.objects.create(user=cls.member, cohort=cohort)
        cls.event = Event.objects.create(
            event_series=series, series_position=3, title='Session 3',
            slug='join-state-session-3', status='upcoming', published=True,
            origin='studio', start_datetime=timezone.now() + datetime.timedelta(days=2),
            zoom_join_url='https://zoom.us/j/123456789',
        )

    def setUp(self):
        self.client.force_login(self.member)

    def _start_in(self, delta):
        self.event.start_datetime = timezone.now() + delta
        self.event.end_datetime = self.event.start_datetime + datetime.timedelta(hours=1)
        self.event.save(update_fields=['start_datetime', 'end_datetime'])

    def _set_include_cohort(self, value):
        IntegrationSetting.objects.create(
            key='EVENT_REMINDERS_INCLUDE_COHORT', value=value, group='site',
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

    def test_before_window_note_promises_the_reminder_and_the_join_window(self):
        response = self.client.get(self.unit.get_absolute_url())

        self.assertContains(response, NOTE_TESTID)
        self.assertContains(
            response,
            f"{REMINDER_TESTID}>You'll get an email reminder before the session.</span>",
            html=False,
        )
        self.assertContains(
            response, 'The join link appears here 5 minutes before it starts.',
        )
        self.assertNotContains(response, JOIN_NOW_TESTID)
        self.assertNotContains(response, 'See how to join')

    def test_before_window_note_omits_reminder_when_cohort_reminders_are_off(self):
        self._set_include_cohort('false')

        response = self.client.get(self.unit.get_absolute_url())

        self.assertContains(response, NOTE_TESTID)
        self.assertNotContains(response, REMINDER_TESTID)

    def test_before_window_note_omits_reminder_when_email_is_suppressed(self):
        EmailLog.objects.create(
            user=self.member, recipient_email=self.member.email,
            email_type='event_registration', complained_at=timezone.now(),
        )

        response = self.client.get(self.unit.get_absolute_url())

        self.assertContains(response, NOTE_TESTID)
        self.assertNotContains(response, REMINDER_TESTID)

    def test_in_window_renders_the_event_page_join_partial(self):
        self._start_in(datetime.timedelta(minutes=2))

        response = self.client.get(self.unit.get_absolute_url())

        self.assertTemplateUsed(response, 'events/_event_join_now.html')
        self.assertContains(response, JOIN_NOW_TESTID)
        self.assertContains(response, f'href="{self.event.get_join_url()}"')
        self.assertNotContains(response, NOTE_TESTID)

    def test_past_session_shows_no_join_ui(self):
        self._start_in(-datetime.timedelta(days=1))

        response = self.client.get(self.unit.get_absolute_url())

        self.assertContains(response, 'data-testid="unit-session-recap-missing"')
        self.assertNotContains(response, NOTE_TESTID)
        self.assertNotContains(response, JOIN_NOW_TESTID)
        self.assertNotContains(response, 'data-testid="unit-session-actions"')
