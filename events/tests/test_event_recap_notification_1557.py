"""Explicit event-recap notification delivery (issue #1557).

Since A1.2 slice 3 the email channel records a durable ``EmailDelivery``
and the provider send happens from the delivery worker, so tests drain
pending deliveries with :func:`email_app.testing.deliver_pending_mail`
before asserting ``EmailLog`` rows.
"""

from datetime import timedelta
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from email_app.testing import deliver_pending_mail
from events.models import Event, EventRegistration
from events.services.event_recap_notification import (
    EventRecapNotReady,
    absolute_recap_url,
    notify_recap_ready,
)
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
        deliveries = EmailDelivery.objects.order_by('idempotency_key')
        self.assertEqual(deliveries.count(), 2)
        self.assertEqual(
            {d.recipient_email for d in deliveries},
            {self.member.email, self.unsubscribed.email},
        )
        for delivery in deliveries:
            self.assertEqual(delivery.purpose, 'event_recap_ready')
            self.assertEqual(
                delivery.idempotency_key,
                f'event-recap-ready:{self.event.pk}:{delivery.recipient_user_id}',
            )
            # Issue #1613: no URL is stored; the worker mints the links
            # from the related event at delivery time.
            self.assertEqual(delivery.context_data, {})
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

        # The worker writes the audit rows after provider acceptance,
        # keeping the event FK from the delivery's related relation.
        deliver_pending_mail()
        email_logs = EmailLog.objects.filter(
            event=self.event, email_type='event_recap_ready',
        )
        self.assertEqual(email_logs.count(), 2)
        self.assertEqual(
            {log.user_id for log in email_logs},
            {self.member.pk, self.unsubscribed.pk},
        )
        self.assertEqual(
            {log.dedupe_key for log in email_logs},
            {d.idempotency_key for d in deliveries},
        )

    def test_worker_mints_recap_links_from_related_event(self):
        """Issue #1613: the stored context is empty; the rendered email
        still carries the recap and event links after the drain."""

        notify_recap_ready(self.event)
        deliveries = list(EmailDelivery.objects.all())
        self.assertEqual(len(deliveries), 2)
        self.assertTrue(all(d.context_data == {} for d in deliveries))

        from email_app.testing import StubSESClient

        stub = StubSESClient()
        with patch(
            'community_base.mail.backends.ses_local.configured_client',
            return_value=stub,
        ):
            deliver_pending_mail()

        self.assertEqual(len(stub.calls), 2)
        recap_url = absolute_recap_url(self.event)
        event_url = f'https://aishippinglabs.com{self.event.get_absolute_url()}'
        for call in stub.calls:
            html = call['Content']['Simple']['Body']['Html']['Data']
            self.assertIn(f'href="{recap_url}"', html)
            self.assertIn(event_url, html)

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
        self.assertEqual(EmailDelivery.objects.count(), 2)

    def test_repeat_is_idempotent_per_channel(self):
        first = notify_recap_ready(self.event)
        deliver_pending_mail()

        second = notify_recap_ready(self.event)

        self.assertEqual(first['emailed'], 2)
        self.assertEqual(first['notified'], 2)
        self.assertEqual(second['emailed'], 0)
        self.assertEqual(second['notified'], 0)
        self.assertEqual(second['already_emailed'], 2)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['already_sent'], 2)
        # No second delivery, and the drain after the repeat adds no rows.
        self.assertEqual(EmailDelivery.objects.count(), 2)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            2,
        )

    def test_retry_only_delivers_the_failed_email_channel(self):
        from email_app.package_mail import package_send as real_package_send

        def fail_member(**kwargs):
            if kwargs.get('to') == self.member.email:
                raise MailError('provider unavailable')
            return real_package_send(**kwargs)

        with patch(
            'email_app.package_mail.package_send',
            side_effect=fail_member,
        ):
            first = notify_recap_ready(self.event)
            deliver_pending_mail()
        second = notify_recap_ready(self.event)
        deliver_pending_mail()

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
        self.assertEqual(second['emailed'], 1)
        self.assertEqual(second['notified'], 0)
        self.assertEqual(second['already_emailed'], 1)
        self.assertEqual(second['already_notified'], 2)
        self.assertEqual(second['already_sent'], 1)
        self.assertEqual(second['failed'], 0)
        self.assertEqual(EmailDelivery.objects.count(), 2)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            2,
        )

    def test_rerun_reaches_a_registrant_added_after_the_first_send(self):
        first = notify_recap_ready(self.event)
        deliver_pending_mail()
        later = User.objects.create_user(
            email='recap-later@test.com', email_verified=True,
        )
        EventRegistration.objects.create(event=self.event, user=later)

        second = notify_recap_ready(self.event)
        deliver_pending_mail()

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
        self.assertEqual(EmailDelivery.objects.count(), 3)
        self.assertEqual(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).count(),
            3,
        )

    def test_one_failed_email_does_not_abort_other_channels_or_recipients(self):
        from email_app.package_mail import package_send as real_package_send

        def fail_member(**kwargs):
            if kwargs.get('to') == self.member.email:
                raise MailError('provider unavailable')
            return real_package_send(**kwargs)

        with patch(
            'email_app.package_mail.package_send',
            side_effect=fail_member,
        ):
            result = notify_recap_ready(self.event)

        by_user = {item['user_id']: item for item in result['results']}
        self.assertEqual(result['failed'], 1)
        self.assertEqual(result['emailed'], 1)
        self.assertEqual(result['notified'], 2)
        self.assertEqual(by_user[self.member.pk]['email_status'], 'failed')
        self.assertEqual(by_user[self.member.pk]['in_app_status'], 'sent')
        self.assertEqual(by_user[self.unsubscribed.pk]['email_status'], 'sent')
        self.assertEqual(EmailDelivery.objects.count(), 1)

    def test_user_deactivated_between_channels_is_skipped(self):
        original_deliver_email = (
            __import__(
                'events.services.event_recap_notification', fromlist=['_deliver_email'],
            )._deliver_email
        )

        def deliver_email(event, user_id, recap_url):
            state = original_deliver_email(event, user_id, recap_url)
            if user_id == self.member.pk:
                User.objects.filter(pk=user_id).update(is_active=False)
            return state

        with patch(
            'events.services.event_recap_notification._deliver_email',
            side_effect=deliver_email,
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
