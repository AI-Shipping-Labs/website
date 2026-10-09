"""Hourly pod meeting reminders (issue #1919).

Every test freezes or passes a fixed clock (date-rot-ok: Friday 2026-10-09).
"""

import datetime
from datetime import UTC

from django.test import TestCase, tag
from freezegun import freeze_time

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.models import Notification
from pods.models import MEETING_RESPONSE_CANT, PodMeeting, PodMeetingResponse
from pods.services import meetings as mtg
from pods.services.meeting_reminders import send_meeting_reminders
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday


def reminders(user):
    return list(
        Notification.objects.filter(user=user, title__startswith='Reminder: ').values_list('title', flat=True)
    )


@tag('core')
class MeetingReminderTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.anna = make_user('anna@example.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@example.com', first_name='Raj', last_name='Shah', timezone_name='Asia/Kolkata')
        cls.mike = make_user('mike@example.com', first_name='Mike', last_name='Kay', timezone_name='America/New_York')
        for user in (cls.anna, cls.raj, cls.mike):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals study group', [self.anna, self.raj, self.mike], owner=self.anna)

    def tearDown(self):
        clear_config_cache()

    def test_reminds_each_member_once_skipping_cant_make_it(self):
        soon = make_meeting(self.pod, NOW + datetime.timedelta(hours=10))
        make_meeting(self.pod, NOW + datetime.timedelta(hours=30))
        make_meeting(self.pod, NOW + datetime.timedelta(hours=5), status='proposed')
        make_meeting(self.pod, NOW + datetime.timedelta(hours=6), status='cancelled')
        PodMeetingResponse.objects.create(meeting=soon, user=self.mike, response=MEETING_RESPONSE_CANT)
        result = send_meeting_reminders(now=NOW)
        self.assertEqual(result, {'meetings': 1, 'notifications': 2})
        self.assertEqual(reminders(self.anna), ['Reminder: RAG evals study group meets Fri Oct 09, 20:00 Europe/Berlin'])
        self.assertEqual(reminders(self.raj), ['Reminder: RAG evals study group meets Fri Oct 09, 23:30 Asia/Kolkata'])
        self.assertEqual(reminders(self.mike), [])
        soon.refresh_from_db()
        self.assertEqual(soon.reminder_sent_at, NOW)
        self.assertEqual(
            Notification.objects.get(user=self.anna, title__startswith='Reminder: ').url,
            f'/courses/{self.course.slug}/home/pods/{self.pod.pk}',
        )

    def test_second_run_in_the_same_hour_sends_nothing(self):
        make_meeting(self.pod, NOW + datetime.timedelta(hours=10))
        send_meeting_reminders(now=NOW)
        self.assertEqual(send_meeting_reminders(now=NOW + datetime.timedelta(minutes=30)),
                         {'meetings': 0, 'notifications': 0})
        self.assertEqual(len(reminders(self.anna)), 1)

    @freeze_time(NOW)
    def test_a_moved_meeting_is_reminded_again(self):
        meeting = make_meeting(self.pod, NOW + datetime.timedelta(hours=10), zone='Europe/Berlin')
        send_meeting_reminders(now=NOW)
        mtg.move_meeting(meeting, self.anna, NOW + datetime.timedelta(hours=12))
        meeting.refresh_from_db()
        self.assertIsNone(meeting.reminder_sent_at)
        send_meeting_reminders(now=NOW)
        self.assertEqual(len(reminders(self.raj)), 2)

    def test_reminder_hours_setting_controls_the_window(self):
        make_meeting(self.pod, NOW + datetime.timedelta(hours=5))
        IntegrationSetting.objects.create(key='PODS_MEETING_REMINDER_HOURS', value='2')
        clear_config_cache()
        self.assertEqual(send_meeting_reminders(now=NOW)['meetings'], 0)
        IntegrationSetting.objects.filter(key='PODS_MEETING_REMINDER_HOURS').update(value='999')
        clear_config_cache()
        # Out of range (1-72) falls back to 24 hours.
        self.assertEqual(send_meeting_reminders(now=NOW)['meetings'], 1)

    def test_archived_pods_and_started_meetings_are_skipped(self):
        started = make_meeting(self.pod, NOW - datetime.timedelta(minutes=5))
        archived = make_pod(self.cohort, 'Old pod', [self.anna], status='archived')
        make_meeting(archived, NOW + datetime.timedelta(hours=3))
        self.assertEqual(send_meeting_reminders(now=NOW)['meetings'], 0)
        self.assertIsNone(PodMeeting.objects.get(pk=started.pk).reminder_sent_at)

    def test_reminder_hours_is_editable_in_studio_settings(self):
        staff = make_user('staff@example.com', staff=True)
        self.client.force_login(staff)
        response = self.client.get('/studio/settings/')
        self.assertContains(response, 'PODS_MEETING_REMINDER_HOURS')
        self.assertContains(response, 'pods.md#pods_meeting_reminder_hours')
