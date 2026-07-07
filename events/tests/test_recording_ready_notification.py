"""Tests for host/operator recording-ready notifications (#1075)."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)
from events.models import Event, EventHost, Host
from events.services.recording_ready_notification import (
    EMAIL_TYPE,
    notify_recording_ready,
    resolve_recording_ready_recipients,
)


def make_event(**kwargs):
    defaults = {
        'title': 'Recording Review Workshop',
        'slug': f'recording-review-{uuid4().hex}',
        'start_datetime': timezone.now() - timedelta(hours=2),
        'end_datetime': timezone.now() - timedelta(hours=1),
        'timezone': 'Europe/Berlin',
        'status': 'completed',
        'published': False,
        'recording_url': 'https://zoom.us/rec/play/source',
        'recording_s3_url': (
            'https://private-bucket.s3.eu-central-1.amazonaws.com/'
            'recordings/2026/recording-review.mp4'
        ),
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


class RecordingReadyRecipientResolutionTest(TestCase):
    def test_host_email_then_active_hosts_are_deduped_in_order(self):
        event = make_event(host_email='Host@Example.com')
        duplicate = Host.objects.create(
            name='Duplicate Host',
            slug='duplicate-host',
            email='host@example.com',
        )
        second = Host.objects.create(
            name='Second Host',
            slug='second-host',
            email='second@example.com',
        )
        inactive = Host.objects.create(
            name='Inactive Host',
            slug='inactive-host',
            email='inactive@example.com',
            is_active=False,
        )
        EventHost.objects.create(event=event, host=second, position=2)
        EventHost.objects.create(event=event, host=inactive, position=1)
        EventHost.objects.create(event=event, host=duplicate, position=0)

        recipients = resolve_recording_ready_recipients(event)

        self.assertEqual(
            [recipient.email for recipient in recipients],
            ['Host@Example.com', 'second@example.com'],
        )

    @patch(
        'events.services.recording_ready_notification.get_config',
        return_value='staff@example.com',
    )
    def test_falls_back_to_staff_email_when_no_host_recipient(self, _mock_config):
        event = make_event(host_email='')

        recipients = resolve_recording_ready_recipients(event)

        self.assertEqual([recipient.email for recipient in recipients], ['staff@example.com'])
        self.assertEqual(recipients[0].source, 'staff_fallback')

    @patch(
        'events.services.recording_ready_notification.get_config',
        return_value='',
    )
    def test_no_recipient_skips_with_structured_result(self, _mock_config):
        event = make_event(host_email='')

        with self.assertLogs(
            'events.services.recording_ready_notification',
            level='WARNING',
        ) as logs:
            result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['skipped_reason'], 'no_recipient')
        self.assertEqual(result['recipient_count'], 0)
        self.assertTrue(any('no host or staff fallback recipient' in line for line in logs.output))


class RecordingReadyNotificationSendTest(TestCase):
    def test_event_recording_ready_is_transactional(self):
        self.assertEqual(classify_email_type(EMAIL_TYPE), EMAIL_KIND_TRANSACTIONAL)

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_sends_external_host_email_and_writes_event_scoped_log(self, mock_send):
        mock_send.return_value = 'ses-ready-1'
        event = make_event(host_email='external-host@example.com')

        result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['recipient_count'], 1)
        self.assertEqual(result['email_log_ids'], [EmailLog.objects.get().pk])

        log = EmailLog.objects.get()
        self.assertEqual(log.event, event)
        self.assertIsNone(log.user)
        self.assertEqual(log.recipient_email, 'external-host@example.com')
        self.assertEqual(log.email_type, EMAIL_TYPE)
        self.assertEqual(log.ses_message_id, 'ses-ready-1')

        call = mock_send.call_args
        self.assertEqual(call.args[0], 'external-host@example.com')
        self.assertEqual(call.kwargs['email_type'], EMAIL_TYPE)
        html = call.args[2]
        self.assertIn(f'/studio/events/{event.pk}/edit', html)
        self.assertIn('Ready for review/publishing', html)
        self.assertIn('Zoom source/review fallback', html)
        self.assertNotIn(event.recording_s3_url, html)
        # Issue #1076: the host email carries a CTA deep-linking to the
        # pre-filled recording-available campaign draft. The ``&`` is
        # HTML-escaped in the rendered markdown link.
        self.assertIn(
            f'/studio/campaigns/new?event={event.pk}'
            '&amp;template=recording_available',
            html,
        )

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_registered_host_logs_user_not_recipient_email(self, mock_send):
        mock_send.return_value = 'ses-user-host'
        user = get_user_model().objects.create_user(
            email='host-user@example.com',
            password='pw',
        )
        event = make_event(host_email=user.email)

        result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'sent')
        log = EmailLog.objects.get()
        self.assertEqual(log.user, user)
        self.assertEqual(log.recipient_email, '')

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_replay_skips_existing_event_recipient_log(self, mock_send):
        event = make_event(host_email='external-host@example.com')
        existing = EmailLog.objects.create(
            event=event,
            recipient_email='EXTERNAL-HOST@example.com',
            email_type=EMAIL_TYPE,
            ses_message_id='old-ses',
        )

        result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['skipped_reason'], 'already_sent')
        self.assertEqual(result['email_log_ids'], [existing.pk])
        mock_send.assert_not_called()
        self.assertEqual(EmailLog.objects.count(), 1)

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_send_error_is_best_effort_and_does_not_write_log(self, mock_send):
        mock_send.side_effect = RuntimeError('SES unavailable')
        event = make_event(host_email='external-host@example.com')

        result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['recipient_count'], 0)
        self.assertEqual(result['email_log_ids'], [])
        self.assertEqual(EmailLog.objects.count(), 0)

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_published_event_copy_does_not_change_publish_state(self, mock_send):
        mock_send.return_value = 'ses-published'
        event = make_event(
            host_email='external-host@example.com',
            published=True,
        )

        notify_recording_ready(event)

        html = mock_send.call_args.args[2]
        self.assertIn('Uploaded and currently published', html)
        event.refresh_from_db()
        self.assertTrue(event.published)

    def test_empty_s3_url_skips_without_sending(self):
        event = make_event(
            host_email='external-host@example.com',
            recording_s3_url='',
        )

        result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'skipped')
        self.assertEqual(result['skipped_reason'], 'no_recording_s3_url')
        self.assertFalse(EmailLog.objects.exists())


class RecordingAvailableToWatchCopyTest(TestCase):
    """Phase B (#1134): published + workshop video surface => "available to watch"."""

    def _make_workshop_event(self, published, workshop_status='published'):
        from content.models import Workshop

        event = make_event(
            host_email='external-host@example.com',
            published=published,
            kind='workshop',
        )
        Workshop.objects.create(
            slug=f'ws-{uuid4().hex}',
            title='S3 Recording Workshop',
            date=event.start_datetime.date(),
            status=workshop_status,
            event=event,
        )
        event.refresh_from_db()
        return event

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_published_workshop_email_says_available_to_watch_with_video_link(
        self, mock_send,
    ):
        mock_send.return_value = 'ses-watch'
        event = self._make_workshop_event(published=True)
        workshop_slug = event.workshop.slug

        result = notify_recording_ready(event)

        self.assertEqual(result['status'], 'sent')
        subject = mock_send.call_args.args[1]
        html = mock_send.call_args.args[2]

        # Copy leads with "available to watch".
        self.assertIn('available to watch', subject.lower())
        self.assertIn('available to watch', html.lower())

        # The watch link points at the workshop /video page (the surface that
        # renders the player), rendered as a real <a href> — not markdown.
        self.assertIn(
            f'href="https://aishippinglabs.com/workshops/{workshop_slug}/video"',
            html,
        )
        self.assertNotIn('](', html)

        # It must NOT be the announcement-only event-detail page, nor any raw
        # or presigned S3 URL.
        self.assertNotIn(event.get_absolute_url(), html)
        self.assertNotIn('amazonaws.com', html)
        self.assertNotIn(event.recording_s3_url, html)

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_unpublished_workshop_event_keeps_ready_for_review_copy(
        self, mock_send,
    ):
        mock_send.return_value = 'ses-review'
        event = self._make_workshop_event(published=False)

        notify_recording_ready(event)

        subject = mock_send.call_args.args[1]
        html = mock_send.call_args.args[2]
        self.assertIn('ready for review', subject.lower())
        self.assertIn('Ready for review/publishing', html)
        self.assertNotIn('available to watch', html.lower())
        # Falls back to the Studio review link, not a watch link.
        self.assertIn(f'/studio/events/{event.pk}/edit', html)
        self.assertNotIn('/video"', html)

    @patch('events.services.recording_ready_notification.EmailService._send_ses')
    def test_published_but_no_watchable_surface_keeps_review_framing(
        self, mock_send,
    ):
        """Published event whose workshop is a draft has no watchable surface,
        so the email must not claim "available to watch"."""
        mock_send.return_value = 'ses-draft-ws'
        event = self._make_workshop_event(
            published=True, workshop_status='draft',
        )

        notify_recording_ready(event)

        html = mock_send.call_args.args[2]
        self.assertNotIn('available to watch', html.lower())
        self.assertIn(f'/studio/events/{event.pk}/edit', html)
        self.assertNotIn('/video"', html)
