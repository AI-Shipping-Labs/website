"""Relocated staff-token calendar enqueue API owners (#1482)."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventRegistration

User = get_user_model()

CANCEL_FANOUT = (
    'events.tasks.notify_cancellation.send_cancellation_notice_fanout'
)
RESCHEDULE_FANOUT = (
    'events.tasks.notify_reschedule.send_reschedule_notice_fanout'
)


def _fanout_calls(mock_async, func_path):
    return [
        call for call in mock_async.call_args_list
        if call.args and call.args[0] == func_path
    ]


@tag('core')
class ApiCalendarLifecycleTest(TestCase):
    """Owns PATCH cancel/reschedule calendar enqueue through the event API.

    Relocated from Playwright ``TestApiCalendarLifecycle``.
    """

    def setUp(self):
        self.staff = User.objects.create_user(
            email='api-calendar-1073@test.com',
            password='pw',
            is_staff=True,
        )
        self.token = Token.objects.create(
            user=self.staff, name='calendar-lifecycle',
        )
        start = (timezone.now() + timedelta(days=30)).replace(
            second=0, microsecond=0,
        )
        self.start = start
        self.event = Event.objects.create(
            title='Calendar Lifecycle api-calendar-1073',
            slug='api-calendar-1073',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            timezone='UTC',
            origin='studio',
        )
        attendee = User.objects.create_user(
            email='attendee-api-calendar-1073@test.com',
            email_verified=True,
        )
        EventRegistration.objects.create(event=self.event, user=attendee)

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def _patch(self, payload):
        return self.client.patch(
            f'/api/events/{self.event.slug}',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(),
        )

    @patch('jobs.tasks.async_task')
    def test_api_patch_cancel_enqueues_calendar_cancel(self, mock_async):
        mock_async.return_value = 'task-id'
        response = self._patch({'status': 'cancelled'})

        body = response.json()
        self.assertEqual(body['status'], 'cancelled')
        cancel_calls = _fanout_calls(mock_async, CANCEL_FANOUT)
        self.assertEqual(len(cancel_calls), 1)
        self.assertEqual(cancel_calls[0].args[1], self.event.pk)
        self.assertEqual(
            _fanout_calls(mock_async, RESCHEDULE_FANOUT),
            [],
        )

    @patch('jobs.tasks.async_task')
    def test_api_patch_reschedule_enqueues_calendar_update(self, mock_async):
        mock_async.return_value = 'task-id'
        original_uid = self.event.calendar_uid
        new_start = self.start + timedelta(days=3)
        new_end = new_start + timedelta(hours=2)

        response = self._patch({
            'slug': 'api-reschedule-renamed-1073',
            'start_datetime': new_start.isoformat(),
            'end_datetime': new_end.isoformat(),
        })

        body = response.json()
        self.assertEqual(body['slug'], 'api-reschedule-renamed-1073')
        reschedule_calls = _fanout_calls(mock_async, RESCHEDULE_FANOUT)
        self.assertEqual(len(reschedule_calls), 1)
        self.assertEqual(reschedule_calls[0].args[1], self.event.pk)
        self.assertEqual(
            reschedule_calls[0].args[2],
            self.start.isoformat(),
        )
        saved = Event.objects.get(pk=self.event.pk)
        self.assertEqual(saved.slug, 'api-reschedule-renamed-1073')
        self.assertEqual(saved.calendar_uid, original_uid)
        self.assertEqual(
            _fanout_calls(mock_async, CANCEL_FANOUT),
            [],
        )
