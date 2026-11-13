"""Explicit event-recap notification delivery (issue #1557).

The email channel submits ONE Relay campaign for the resolved audience
via ``events.services.relay_event_notice.submit_event_notice_campaign``
so Relay owns pacing, retries, and dedup, and reads status back from
Relay.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from events.models import Event, EventRegistration
from events.services.event_recap_notification import (
    EventRecapNotReady,
    absolute_recap_url,
    notify_recap_ready,
)
from notifications.models import EventReminderLog, Notification

User = get_user_model()

SUBMIT_PATCH = (
    'events.services.event_recap_notification.submit_event_notice_campaign'
)
STATUSES_PATCH = (
    'events.services.event_recap_notification.get_event_notice_statuses'
)


@tag('core')
class EventRecapNotificationServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.event = Event.objects.create(
            title='Inference Engineering Book Club',
            slug='inference-engineering-book-club',
            description='A book club session.',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            recap_notes='## What we covered\n\nInference engineering.',
        )
        cls.member = User.objects.create_user(
            email='recap-member@test.com', email_verified=True,
        )
        cls.unsubscribed = User.objects.create_user(
            email='recap-unsubscribed@test.com',
            email_verified=True,
            unsubscribed=True,
        )
        cls.inactive = User.objects.create_user(
            email='recap-inactive@test.com',
            email_verified=True,
            is_active=False,
        )
        cls.unrelated = User.objects.create_user(
            email='recap-unrelated@test.com', email_verified=True,
        )
        for user in (cls.member, cls.unsubscribed, cls.inactive):
            EventRegistration.objects.create(event=cls.event, user=user)

    def test_sends_both_channels_to_active_exact_registrants(self):
        with (
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ) as mock_submit,
            patch(
                STATUSES_PATCH,
                return_value=[
                    {
                        'label': 'Recap-ready notice',
                        'external_key': 'event-recap-ready-1-abc',
                        'recipient_count': 2,
                        'sent_count': 0,
                        'delivered_count': 0,
                        'pending_count': 2,
                    },
                ],
            ),
        ):
            result = notify_recap_ready(self.event)
        recap_url = absolute_recap_url(self.event)

        self.assertEqual(result['eligible'], 2)
        self.assertEqual(result['emailed'], 2)
        self.assertEqual(result['notified'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(result['skipped_inactive'], 0)
        self.assertEqual(
            {item['user_id'] for item in result['results']},
            {self.member.pk, self.unsubscribed.pk},
        )
        self.assertEqual(
            {(item['email_status'], item['in_app_status']) for item in result['results']},
            {('sent', 'sent')},
        )
        self.assertEqual(mock_submit.call_count, 1)
        submit_kwargs = mock_submit.call_args.kwargs
        self.assertEqual(submit_kwargs['template_key'], 'event_recap_ready')
        self.assertEqual(
            {r['email'] for r in submit_kwargs['recipients']},
            {self.member.email, self.unsubscribed.email},
        )
        self.assertEqual(len(result['relay']), 1)
        self.assertEqual(result['relay'][0]['pending_count'], 2)
        self.assertEqual(
            Notification.objects.filter(
                notification_type='event_recap', url=recap_url,
            ).count(),
            2,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=self.event, interval='recap_email',
            ).count(),
            2,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=self.event, interval='recap_in_app',
            ).count(),
            2,
        )
        self.assertFalse(
            EventRegistration.objects.filter(
                event=self.event, user=self.unrelated,
            ).exists(),
        )

        email_logs = EmailLog.objects.filter(
            event=self.event, email_type='event_recap_ready',
        )
        self.assertEqual(email_logs.count(), 2)
        self.assertEqual(
            {log.user_id for log in email_logs},
            {self.member.pk, self.unsubscribed.pk},
        )
        self.assertEqual(
            {log.ses_message_id for log in email_logs},
            {submit_kwargs['external_key']},
        )

    def test_campaign_recipients_carry_recap_and_event_links(self):
        with (
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ) as mock_submit,
            patch(STATUSES_PATCH, return_value=[]),
        ):
            notify_recap_ready(self.event)

        self.assertEqual(mock_submit.call_count, 1)
        recipients = mock_submit.call_args.kwargs['recipients']
        self.assertEqual(len(recipients), 2)
        recap_url = absolute_recap_url(self.event)
        event_url = f'https://aishippinglabs.com{self.event.get_absolute_url()}'
        for recipient in recipients:
            context = recipient['metadata']['relay_campaign_context']
            self.assertEqual(context['recap_url'], recap_url)
            self.assertEqual(context['event_url'], event_url)
            self.assertEqual(context['recording_url'], '')

    def test_complaint_suppresses_email_but_not_in_app(self):
        complaint_user = User.objects.create_user(
            email='recap-complaint@test.com', email_verified=True,
        )
        EventRegistration.objects.create(
            event=self.event, user=complaint_user,
        )
        EmailLog.objects.create(
            user=complaint_user,
            recipient_email=complaint_user.email,
            email_type='event_registration',
            complained_at=timezone.now(),
        )

        with (
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ) as mock_submit,
            patch(STATUSES_PATCH, return_value=[]),
        ):
            result = notify_recap_ready(self.event)

        complaint_result = next(
            item for item in result['results']
            if item['user_id'] == complaint_user.pk
        )
        self.assertEqual(complaint_result['email_status'], 'skipped_complaint')
        self.assertEqual(complaint_result['in_app_status'], 'sent')
        self.assertEqual(result['emailed'], 2)
        self.assertEqual(result['notified'], 3)
        self.assertEqual(mock_submit.call_count, 1)
        self.assertNotIn(
            complaint_user.email,
            {r['email'] for r in mock_submit.call_args.kwargs['recipients']},
        )

    def test_repeat_is_idempotent_per_channel(self):
        with (
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ) as mock_submit,
            patch(STATUSES_PATCH, return_value=[]),
        ):
            first = notify_recap_ready(self.event)
            second = notify_recap_ready(self.event)

        self.assertEqual(mock_submit.call_count, 1)
        self.assertEqual(first['emailed'], 2)
        self.assertEqual(first['notified'], 2)
        self.assertEqual(second['emailed'], 0)
        self.assertEqual(second['notified'], 0)
        self.assertEqual(second['already_emailed'], 2)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['already_sent'], 2)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            2,
        )

    def test_retry_only_delivers_the_failed_email_channel(self):
        with (
            patch(
                SUBMIT_PATCH,
                side_effect=RuntimeError('Relay unavailable'),
            ),
            patch(STATUSES_PATCH, return_value=[]),
        ):
            first = notify_recap_ready(self.event)

        with (
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ) as mock_submit,
            patch(STATUSES_PATCH, return_value=[]),
        ):
            second = notify_recap_ready(self.event)

        first_by_user = {
            item['user_id']: item for item in first['results']
        }
        second_by_user = {
            item['user_id']: item for item in second['results']
        }
        self.assertEqual(first_by_user[self.member.pk]['email_status'], 'failed')
        self.assertEqual(
            first_by_user[self.member.pk]['in_app_status'], 'sent',
        )
        self.assertEqual(
            second_by_user[self.member.pk]['email_status'], 'sent',
        )
        self.assertEqual(
            second_by_user[self.member.pk]['in_app_status'], 'already_sent',
        )
        self.assertEqual(mock_submit.call_count, 1)
        self.assertEqual(second['emailed'], 2)
        self.assertEqual(second['notified'], 0)
        self.assertEqual(second['already_emailed'], 0)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['already_sent'], 0)
        self.assertEqual(second['failed'], 0)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            2,
        )

    def test_rerun_reaches_a_registrant_added_after_the_first_send(self):
        with (
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ) as mock_submit,
            patch(STATUSES_PATCH, return_value=[]),
        ):
            first = notify_recap_ready(self.event)
            later = User.objects.create_user(
                email='recap-later@test.com', email_verified=True,
            )
            EventRegistration.objects.create(event=self.event, user=later)
            second = notify_recap_ready(self.event)

        later_result = next(
            item for item in second['results'] if item['user_id'] == later.pk
        )
        self.assertEqual(mock_submit.call_count, 2)
        self.assertEqual(
            [r['email'] for r in mock_submit.call_args.kwargs['recipients']],
            [later.email],
        )
        self.assertEqual(first['eligible'], 2)
        self.assertEqual(second['eligible'], 3)
        self.assertEqual(second['emailed'], 1)
        self.assertEqual(second['notified'], 1)
        self.assertEqual(second['already_emailed'], 2)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['already_sent'], 2)
        self.assertEqual(later_result['email_status'], 'sent')
        self.assertEqual(later_result['in_app_status'], 'sent')
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            3,
        )

    def test_relay_campaign_failure_does_not_abort_in_app_notifications(self):
        with (
            patch(
                SUBMIT_PATCH,
                side_effect=RuntimeError('Relay unavailable'),
            ),
            patch(STATUSES_PATCH, return_value=[]),
        ):
            result = notify_recap_ready(self.event)

        by_user = {item['user_id']: item for item in result['results']}
        self.assertEqual(result['failed'], 2)
        self.assertEqual(result['emailed'], 0)
        self.assertEqual(result['notified'], 2)
        self.assertEqual(by_user[self.member.pk]['email_status'], 'failed')
        self.assertEqual(by_user[self.member.pk]['in_app_status'], 'sent')
        self.assertEqual(by_user[self.unsubscribed.pk]['email_status'], 'failed')
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            0,
        )

    def test_user_deactivated_between_channels_is_skipped(self):
        original_prepare_email = (
            __import__(
                'events.services.event_recap_notification', fromlist=['_prepare_email'],
            )._prepare_email
        )

        def prepare_email(event, user_id, signed_up):
            state, user = original_prepare_email(event, user_id, signed_up)
            if user_id == self.member.pk:
                User.objects.filter(pk=user_id).update(is_active=False)
            return state, user

        with (
            patch(
                'events.services.event_recap_notification._prepare_email',
                side_effect=prepare_email,
            ),
            patch(
                SUBMIT_PATCH,
                return_value={'campaign': {'external_key': 'k'}},
            ),
            patch(STATUSES_PATCH, return_value=[]),
        ):
            result = notify_recap_ready(self.event)

        member_result = next(
            item for item in result['results'] if item['user_id'] == self.member.pk
        )
        self.assertEqual(member_result['email_status'], 'sent')
        self.assertEqual(member_result['in_app_status'], 'skipped_inactive')
        self.assertEqual(result['skipped_inactive'], 1)

    def test_not_ready_guards_have_stable_reasons(self):
        cases = (
            ('missing_recap', {'recap_notes': '', 'recap_notes_html': ''}),
            (
                'event_draft',
                {
                    'status': 'draft',
                    'published': True,
                    'recap_notes': '## Recap',
                    'recap_notes_html': '<h2>Recap</h2>',
                },
            ),
            (
                'event_cancelled',
                {
                    'status': 'cancelled',
                    'published': True,
                    'recap_notes': '## Recap',
                    'recap_notes_html': '<h2>Recap</h2>',
                },
            ),
            (
                'event_not_ended',
                {
                    'status': 'upcoming',
                    'published': True,
                    'start_datetime': timezone.now() + timedelta(hours=1),
                    'end_datetime': timezone.now() + timedelta(hours=2),
                    'recap_notes': '## Recap',
                    'recap_notes_html': '<h2>Recap</h2>',
                },
            ),
            (
                'event_unpublished',
                {
                    'published': False,
                    'recap_notes': '## Recap',
                    'recap_notes_html': '<h2>Recap</h2>',
                },
            ),
        )
        for reason, updates in cases:
            with self.subTest(reason=reason):
                Event.objects.filter(pk=self.event.pk).update(**updates)
                with self.assertRaises(EventRecapNotReady) as caught:
                    notify_recap_ready(self.event)
                self.assertEqual(caught.exception.reason, reason)
