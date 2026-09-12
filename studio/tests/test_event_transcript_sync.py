"""Studio transcript & recap panel + sync action (issue #1597)."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import Event

User = get_user_model()


def _past_start_end():
    now = timezone.now()
    return now - timedelta(hours=3), now - timedelta(hours=1)


class StudioTranscriptPanelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='pass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='admin@test.com', password='pass')
        start, end = _past_start_end()
        self.event = Event.objects.create(
            title='Transcript Studio Event',
            slug='transcript-studio-event',
            start_datetime=start,
            end_datetime=end,
            status='completed',
            platform='zoom',
            zoom_meeting_id='55511122233',
        )

    def test_panel_shows_none_status_and_sync_action(self):
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'Transcript &amp; recap')
        self.assertContains(response, 'No transcript captured yet.')
        self.assertContains(response, 'data-testid="transcript-status"')
        self.assertContains(response, 'No recap draft yet.')
        self.assertContains(response, 'data-testid="sync-transcript-button"')
        self.assertNotContains(response, 'data-testid="studio-transcript-preview"')

    def test_panel_shows_stored_status_and_recap_presence(self):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_text='A stored transcript body.',
            recap_notes='## Drafted recap',
        )
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(response, 'Transcript stored (')
        self.assertContains(response, 'Recap draft present in recap notes.')
        self.assertContains(response, 'data-testid="studio-transcript-preview"')
        self.assertContains(response, 'data-testid="studio-transcript-body"')
        self.assertContains(response, 'A stored transcript body.')
        # The drafted markdown is editable in the existing recap notes box.
        self.assertContains(response, '## Drafted recap')

    def test_panel_shows_unavailable_status(self):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_unavailable_at=timezone.now(),
        )
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(
            response,
            'Transcript unavailable — Zoom has no transcript for this '
            'meeting.',
        )
        self.assertNotContains(response, 'data-testid="studio-transcript-preview"')

    def test_panel_shows_waiting_status(self):
        Event.objects.filter(pk=self.event.pk).update(
            transcript_url='https://zoom.us/rec/download/wait.vtt',
        )
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertContains(
            response,
            'Transcript URL captured — download pending or retrying.',
        )
        self.assertNotContains(response, 'data-testid="studio-transcript-preview"')


class StudioSyncTranscriptActionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='admin@test.com', password='pass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-transcript@test.com', password='pass',
        )
        start, end = _past_start_end()
        cls.event = Event.objects.create(
            title='Sync Action Event',
            slug='sync-action-event',
            start_datetime=start,
            end_datetime=end,
            status='completed',
            platform='zoom',
            zoom_meeting_id='55599988877',
        )

    def test_sync_action_queues_task_and_redirects(self):
        client = Client()
        client.login(email='admin@test.com', password='pass')
        with patch(
            'studio.views.events.refresh_transcript_from_zoom',
            return_value={'refreshed': True},
        ):
            with patch('jobs.tasks.helpers.q_async_task') as mock_q_async:
                mock_q_async.return_value = 'studio-task'
                response = client.post(
                    reverse(
                        'studio_event_sync_transcript',
                        kwargs={'event_id': self.event.pk},
                    ),
                )
        self.assertRedirects(
            response,
            reverse('studio_event_edit', kwargs={'event_id': self.event.pk}),
        )
        self.assertEqual(mock_q_async.call_args[0][0],
                         'jobs.tasks.recording_transcript.transcribe_recording')

    def test_sync_action_reports_zoom_refresh_failure_but_still_queues(self):
        from integrations.services.zoom import ZoomAPIError

        client = Client()
        client.login(email='admin@test.com', password='pass')
        with patch(
            'studio.views.events.refresh_transcript_from_zoom',
            side_effect=ZoomAPIError('listing failed'),
        ):
            with patch('jobs.tasks.helpers.q_async_task') as mock_q_async:
                mock_q_async.return_value = 'studio-task-2'
                response = client.post(
                    reverse(
                        'studio_event_sync_transcript',
                        kwargs={'event_id': self.event.pk},
                    ),
                    follow=True,
                )
        messages = [str(m) for m in response.context['messages']]
        self.assertTrue(
            any('Could not re-check Zoom recordings' in m for m in messages),
        )
        self.assertTrue(
            any('Transcript & recap sync queued' in m for m in messages),
        )
        self.assertEqual(mock_q_async.call_count, 1)

    def test_sync_action_is_staff_only(self):
        client = Client()
        client.login(email='member-transcript@test.com', password='pass')
        with patch('jobs.tasks.helpers.q_async_task') as mock_q_async:
            response = client.post(
                reverse(
                    'studio_event_sync_transcript',
                    kwargs={'event_id': self.event.pk},
                ),
            )
        self.assertEqual(response.status_code, 403)
        mock_q_async.assert_not_called()
