"""Explicit event-recap notification delivery (issue #1557).

The email channel is one Relay campaign submit for the whole audience;
tests stand in for Relay with :mod:`events.tests.recap_relay` (no network,
no SES).
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
from events.tests.recap_relay import start_recap_relay
from notifications.models import EventReminderLog, Notification

User = get_user_model()


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

    def setUp(self):
        super().setUp()
        self.relay = start_recap_relay(self)

    def test_sends_both_channels_to_active_exact_registrants(self):
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
        # ONE Relay campaign for the whole audience.
        self.assertEqual(len(self.relay.campaigns), 1)
        campaign = self.relay.campaigns[0]
        self.assertEqual(campaign['template_key'], 'event_recap_ready')
        self.assertTrue(
            campaign['external_key'].startswith(f'event-recap-ready-{self.event.pk}-'),
        )
        self.assertEqual(
            self.relay.emails,
            sorted([self.member.email, self.unsubscribed.email]),
        )
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
            {log.dedupe_key for log in email_logs},
            {
                f'event-recap-ready:{self.event.pk}:{self.member.pk}',
                f'event-recap-ready:{self.event.pk}:{self.unsubscribed.pk}',
            },
        )
        self.assertEqual(
            {log.ses_message_id for log in email_logs},
            {campaign['external_key']},
        )

    def test_summary_reports_relay_sent_delivered_pending(self):
        result = notify_recap_ready(self.event)

        self.assertEqual(len(result['relay']), 1)
        row = result['relay'][0]
        self.assertEqual(row['external_key'], self.relay.campaigns[0]['external_key'])
        self.assertEqual(row['recipient_count'], 2)
        self.assertEqual(row['sent_count'], 2)
        self.assertEqual(row['delivered_count'], 1)
        self.assertEqual(row['pending_count'], 0)

    def test_campaign_carries_recap_and_event_links_per_recipient(self):
        notify_recap_ready(self.event)

        recap_url = absolute_recap_url(self.event)
        event_url = f'https://aishippinglabs.com{self.event.get_absolute_url()}'
        contexts = self.relay.contexts()
        self.assertEqual(len(contexts), 2)
        for context in contexts.values():
            self.assertEqual(context['recap_url'], recap_url)
            self.assertEqual(context['event_url'], event_url)
            self.assertEqual(context['recording_url'], '')
            self.assertEqual(context['event_title'], self.event.title)

    def test_never_uses_per_user_transactional_send(self):
        with patch('email_app.package_mail.send_package_mail') as per_user:
            notify_recap_ready(self.event)
        per_user.assert_not_called()

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

        result = notify_recap_ready(self.event)

        complaint_result = next(
            item for item in result['results']
            if item['user_id'] == complaint_user.pk
        )
        self.assertEqual(complaint_result['email_status'], 'skipped_complaint')
        self.assertEqual(complaint_result['in_app_status'], 'sent')
        self.assertEqual(result['emailed'], 2)
        self.assertEqual(result['notified'], 3)
        self.assertNotIn(complaint_user.email, self.relay.emails)

    def test_repeat_is_idempotent_per_channel(self):
        first = notify_recap_ready(self.event)

        second = notify_recap_ready(self.event)

        self.assertEqual(first['emailed'], 2)
        self.assertEqual(first['notified'], 2)
        self.assertEqual(second['emailed'], 0)
        self.assertEqual(second['notified'], 0)
        self.assertEqual(second['already_emailed'], 2)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['already_sent'], 2)
        # No second campaign submit, and no extra audit rows.
        self.assertEqual(len(self.relay.campaigns), 1)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            2,
        )

    def test_failed_submit_marks_all_failed_and_retry_resubmits(self):
        self.relay.fail = True
        first = notify_recap_ready(self.event)

        self.assertEqual(first['failed'], 2)
        self.assertEqual(first['emailed'], 0)
        self.assertEqual(first['notified'], 2)
        self.assertEqual(
            {item['email_status'] for item in first['results']}, {'failed'},
        )
        self.assertFalse(
            EventReminderLog.objects.filter(
                event=self.event, interval='recap_email',
            ).exists(),
        )

        self.relay.fail = False
        second = notify_recap_ready(self.event)

        self.assertEqual(second['emailed'], 2)
        self.assertEqual(second['notified'], 0)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['failed'], 0)
        self.assertEqual(len(self.relay.campaigns), 1)

    def test_rerun_reaches_a_registrant_added_after_the_first_send(self):
        first = notify_recap_ready(self.event)
        later = User.objects.create_user(
            email='recap-later@test.com', email_verified=True,
        )
        EventRegistration.objects.create(event=self.event, user=later)

        second = notify_recap_ready(self.event)

        later_result = next(
            item for item in second['results'] if item['user_id'] == later.pk
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
        self.assertEqual(len(self.relay.campaigns), 2)
        keys = {c['external_key'] for c in self.relay.campaigns}
        self.assertEqual(len(keys), 2)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            3,
        )
        self.assertEqual(len(second['relay']), 2)

    def test_user_deactivated_between_channels_is_skipped(self):
        from events.services import event_recap_notification as service

        original_submit = service._submit_email_campaign

        def submit_then_deactivate(event, users, recap_url):
            states = original_submit(event, users, recap_url)
            User.objects.filter(pk=self.member.pk).update(is_active=False)
            return states

        with patch.object(
            service, '_submit_email_campaign', side_effect=submit_then_deactivate,
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
