"""Tests for the post-event follow-up background task (one Relay campaign submit)."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)
from events.models import Event, EventRegistration
from events.tasks.send_post_event_followup import (
    enqueue_post_event_followup,
    send_post_event_followup_fanout,
)
from notifications.models import EventReminderLog

User = get_user_model()


class EnqueuePostEventFollowupTest(TestCase):
    """The enqueue helper defers via async_task with the dotted path."""

    @patch('jobs.tasks.helpers.q_async_task')
    def test_enqueue_calls_async_task_with_dotted_path(self, mock_q):
        mock_q.return_value = 'task-id'
        enqueue_post_event_followup(42)

        self.assertEqual(mock_q.call_count, 1)
        args = mock_q.call_args.args
        self.assertEqual(
            args[0],
            'events.tasks.send_post_event_followup.send_post_event_followup_fanout',
        )
        self.assertEqual(args[1], 42)


class PostEventFollowupCampaignSubmitTest(TestCase):
    """send_post_event_followup_fanout submits ONE Relay campaign for all registrants."""

    @classmethod
    def setUpTestData(cls):
        cls.event = Event.objects.create(
            title='Recap one-shot',
            slug='recap-one-shot',
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status='completed',
            recording_url='https://youtube.com/watch?v=recap-one',
            post_event_summary='Thanks for joining the recap test session.',
        )
        cls.user = User.objects.create_user(email='one@test.com')
        cls.unsub_user = User.objects.create_user(
            email='unsub@test.com', unsubscribed=True,
        )

    def setUp(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        EventRegistration.objects.create(event=self.event, user=self.unsub_user)

    @patch('events.tasks.send_post_event_followup.submit_event_notice_campaign', return_value={'created': True})
    def test_submits_one_campaign_writes_logs_and_dedups_rerun(self, mock_submit):
        result = send_post_event_followup_fanout(self.event.pk)

        self.assertEqual(result['status'], 'submitted')
        self.assertEqual(result['count'], 2)
        self.assertEqual(mock_submit.call_count, 1)
        kwargs = mock_submit.call_args.kwargs
        self.assertEqual(kwargs['external_key'], f'event-followup-{self.event.pk}')
        self.assertEqual(kwargs['template_key'], 'post_event_followup')
        emails = {r['email'] for r in kwargs['recipients']}
        self.assertEqual(emails, {'one@test.com', 'unsub@test.com'})
        self.assertEqual(
            EmailLog.objects.filter(event=self.event, email_type='post_event_followup').count(),
            2,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(event=self.event, interval='followup').count(),
            2,
        )

        second = send_post_event_followup_fanout(self.event.pk)
        self.assertEqual(second['status'], 'skipped')
        self.assertEqual(second['reason'], 'no_recipients')
        self.assertEqual(mock_submit.call_count, 1)

    @patch('events.tasks.send_post_event_followup.submit_event_notice_campaign', return_value={'created': True})
    def test_render_includes_summary_recording_and_recap_or_placeholder(self, mock_submit):
        send_post_event_followup_fanout(self.event.pk)
        context = mock_submit.call_args.kwargs['context']
        self.assertEqual(context['event_summary'], 'Thanks for joining the recap test session.')
        self.assertEqual(context['recording_url'], 'https://youtube.com/watch?v=recap-one')
        self.assertTrue(context['notes_placeholder'])

    @patch('events.tasks.send_post_event_followup.submit_event_notice_campaign', return_value={'created': True})
    def test_published_recap_replaces_notes_placeholder(self, mock_submit):
        Event.objects.filter(pk=self.event.pk).update(
            recap_notes='## What we covered\n\nBatching.',
            recap_notes_html='<h2>What we covered</h2><p>Batching.</p>',
        )
        send_post_event_followup_fanout(self.event.pk)
        self.event.refresh_from_db()
        context = mock_submit.call_args.kwargs['context']
        self.assertEqual(
            context['recap_url'],
            f'https://aishippinglabs.com{self.event.get_recap_url()}',
        )
        self.assertNotIn('notes_placeholder', context)

    @patch('events.tasks.send_post_event_followup.submit_event_notice_campaign', return_value={'created': True})
    def test_blank_summary_uses_generic_fallback(self, mock_submit):
        Event.objects.filter(pk=self.event.pk).update(post_event_summary='')
        send_post_event_followup_fanout(self.event.pk)
        context = mock_submit.call_args.kwargs['context']
        self.assertEqual(
            context['event_summary'],
            'Thanks for joining us at Recap one-shot. The recording is now available below.',
        )

    @patch('events.tasks.send_post_event_followup.submit_event_notice_campaign')
    def test_event_without_recording_url_skips(self, mock_submit):
        Event.objects.filter(pk=self.event.pk).update(recording_url='', recording_s3_url='')
        result = send_post_event_followup_fanout(self.event.pk)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'no_recording_url')
        mock_submit.assert_not_called()

    @patch('events.tasks.send_post_event_followup.submit_event_notice_campaign')
    def test_missing_event_returns_skipped(self, mock_submit):
        result = send_post_event_followup_fanout(999_999)
        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['reason'], 'missing_event')
        mock_submit.assert_not_called()


class TransactionalClassificationTest(TestCase):
    """classify_email_type reports post_event_followup as transactional."""

    def test_post_event_followup_is_transactional(self):
        self.assertEqual(
            classify_email_type('post_event_followup'),
            EMAIL_KIND_TRANSACTIONAL,
        )
