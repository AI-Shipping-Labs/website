"""Studio pod meetings (issue #1919): meetings table, schedule form, row
actions, list columns and the call link setting.

Every test freezes the clock (date-rot-ok: fixed Friday 2026-10-09).
"""

import datetime
from datetime import UTC

from django.test import TestCase, tag
from freezegun import freeze_time

from notifications.models import Notification
from pods.models import PodMeeting, PodMeetingResponse
from pods.tests.fixtures import enroll, make_cohort, make_course, make_meeting, make_pod, make_user
from pods.tests.test_meeting_views import texts

NOW = datetime.datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # date-rot-ok: frozen Friday


@tag('core')
@freeze_time(NOW)
class StudioPodMeetingsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = make_course()
        cls.cohort = make_cohort(cls.course)
        cls.staff = make_user('admin@test.com', staff=True)
        cls.member = make_user('member@test.com')
        cls.anna = make_user('anna@test.com', first_name='Anna', last_name='Klein', timezone_name='Europe/Berlin')
        cls.raj = make_user('raj@test.com', first_name='Raj', last_name='Shah', timezone_name='Asia/Kolkata')
        for user in (cls.anna, cls.raj):
            enroll(user, cls.cohort)

    def setUp(self):
        self.pod = make_pod(self.cohort, 'RAG evals study group', [self.anna, self.raj], owner=self.anna)
        self.client.force_login(self.staff)

    def detail(self):
        return self.client.get(f'/studio/pods/{self.pod.pk}/')

    def test_empty_meetings_table_uses_the_studio_empty_state(self):
        response = self.detail()
        section = response.content.decode().split('data-testid="studio-pod-meetings"')[1]
        self.assertIn('No meetings yet.', section.split('data-testid="studio-pod-schedule-form"')[0])

    def test_schedule_weekly_creates_scheduled_meetings_and_notifies_members(self):
        response = self.client.post(f'/studio/pods/{self.pod.pk}/meetings/schedule', {
            'date': '2026-10-15', 'time': '17:00', 'timezone': 'Europe/Berlin', 'repeat_weekly': '1',
        }, follow=True)
        self.assertContains(response, 'Scheduled 4 meetings.')
        self.assertEqual(
            texts(response, 'studio-pod-meeting-start'),
            ['2026-10-15 15:00 UTC Europe/Berlin', '2026-10-22 15:00 UTC Europe/Berlin',
             '2026-10-29 16:00 UTC Europe/Berlin', '2026-11-05 16:00 UTC Europe/Berlin'],
        )
        self.assertEqual(set(texts(response, 'studio-pod-meeting-status')), {'Scheduled'})
        self.assertEqual(
            {m.created_via for m in PodMeeting.objects.filter(pod=self.pod)}, {'studio'},
        )
        self.assertTrue(Notification.objects.filter(
            user=self.anna, title='RAG evals study group meets Thu Oct 15, 17:00 Europe/Berlin',
        ).exists())

    def test_schedule_errors_come_back_on_the_form(self):
        response = self.client.post(f'/studio/pods/{self.pod.pk}/meetings/schedule', {
            'date': '2026-10-09', 'time': '08:15', 'timezone': 'UTC',
        }, follow=True)
        self.assertEqual(
            texts(response, 'studio-pod-schedule-error'),
            ['Pick a time at least 1 hour from now and within the next 6 months.'],
        )
        self.assertFalse(PodMeeting.objects.exists())

    def test_row_actions_mark_held_and_cancel(self):
        past = make_meeting(self.pod, NOW - datetime.timedelta(hours=3))
        future = make_meeting(self.pod, NOW + datetime.timedelta(days=3))
        PodMeetingResponse.objects.create(meeting=future, user=self.raj, response='cant_make_it')
        response = self.detail()
        self.assertEqual(texts(response, 'studio-pod-meeting-number'), ['1', '2'])
        self.assertEqual(texts(response, 'studio-pod-meeting-going'), ['Anna K. Raj S.', 'Anna K.'])
        self.assertEqual(texts(response, 'studio-pod-meeting-cant'), ['—', 'Raj S.'])
        self.client.post(f'/studio/pods/{self.pod.pk}/meetings/{past.pk}/status', {'status': 'held'})
        response = self.client.post(
            f'/studio/pods/{self.pod.pk}/meetings/{future.pk}/status', {'status': 'cancelled'}, follow=True,
        )
        self.assertEqual(texts(response, 'studio-pod-meeting-status'), ['Held', 'Cancelled'])
        self.assertEqual(texts(response, 'studio-pod-meeting-number'), ['1', '—'])
        self.assertEqual(
            dict(PodMeeting.objects.values_list('pk', 'status')), {past.pk: 'held', future.pk: 'cancelled'},
        )

    def test_non_staff_cannot_schedule_or_change_meetings(self):
        meeting = make_meeting(self.pod, NOW + datetime.timedelta(days=3))
        self.client.force_login(self.member)
        response = self.client.post(f'/studio/pods/{self.pod.pk}/meetings/schedule', {
            'date': '2026-10-15', 'time': '17:00', 'timezone': 'UTC',
        })
        self.assertEqual(response.status_code, 403)
        response = self.client.post(f'/studio/pods/{self.pod.pk}/meetings/{meeting.pk}/status', {'status': 'cancelled'})
        self.assertEqual(response.status_code, 403)
        meeting.refresh_from_db()
        self.assertEqual((PodMeeting.objects.count(), meeting.status), (1, 'scheduled'))

    def test_list_shows_meetings_and_next_meeting_columns(self):
        make_meeting(self.pod, NOW - datetime.timedelta(days=2), status='held')
        make_meeting(self.pod, datetime.datetime(2026, 10, 14, 16, 0, tzinfo=UTC))
        empty = make_pod(self.cohort, 'Quiet pod', [self.anna], meeting_count=1)
        response = self.client.get('/studio/pods/')
        rows = dict(zip(texts(response, 'studio-pod-name'), texts(response, 'studio-pod-meetings'), strict=True))
        self.assertEqual(rows, {'RAG evals study group': 'Meetings 1/4 held', empty.name: 'Meetings 0/1 held'})
        nexts = dict(zip(texts(response, 'studio-pod-name'), texts(response, 'studio-pod-next-meeting'), strict=True))
        self.assertEqual(nexts, {'RAG evals study group': 'Next 2026-10-14 16:00', empty.name: 'No meeting planned'})

    def test_settings_form_saves_and_validates_the_call_link(self):
        form = {
            'name': self.pod.name, 'purpose': self.pod.purpose, 'max_members': '4', 'meeting_count': '4',
            'meeting_minutes': '60', 'status': 'open', 'owner': str(self.anna.pk),
        }
        response = self.client.post(f'/studio/pods/{self.pod.pk}/', {**form, 'meeting_url': 'http://meet.google.com/x'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            texts(response, 'studio-pod-error'),
            ['Paste an https link to your call, for example https://meet.google.com/abc-defg-hij.'],
        )
        self.client.post(f'/studio/pods/{self.pod.pk}/', {**form, 'meeting_url': 'https://meet.google.com/abc-defg-hij'})
        self.pod.refresh_from_db()
        self.assertEqual(self.pod.meeting_url, 'https://meet.google.com/abc-defg-hij')
        make_meeting(self.pod, NOW + datetime.timedelta(days=1))
        make_meeting(self.pod, NOW + datetime.timedelta(days=2))
        response = self.client.post(f'/studio/pods/{self.pod.pk}/', {**form, 'meeting_count': '1'})
        self.assertEqual(
            texts(response, 'studio-pod-error'),
            ['This pod already has 2 meetings planned or held. Cancel a meeting before lowering the number.'],
        )

    def test_proposals_offer_no_mark_held_and_cancel_ends_the_whole_proposal(self):
        import uuid

        series = uuid.uuid4()
        first = make_meeting(self.pod, NOW + datetime.timedelta(days=2), status='proposed', series_id=series)
        make_meeting(self.pod, NOW + datetime.timedelta(days=9), status='proposed', series_id=series)
        response = self.detail()
        self.assertNotContains(response, 'data-testid="studio-pod-meeting-held"')
        response = self.client.post(f'/studio/pods/{self.pod.pk}/meetings/{first.pk}/status', {'status': 'held'},
                                    follow=True)
        self.assertEqual(set(PodMeeting.objects.values_list('status', flat=True)), {'proposed'})
        response = self.client.post(
            f'/studio/pods/{self.pod.pk}/meetings/{first.pk}/status', {'status': 'cancelled'}, follow=True,
        )
        self.assertEqual(texts(response, 'studio-pod-meeting-status'), ['Cancelled', 'Cancelled'])
