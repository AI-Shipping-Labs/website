from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from content.models import Workshop
from email_app.models import EmailLog
from email_app.services.email_classification import (
    EMAIL_KIND_TRANSACTIONAL,
    classify_email_type,
)
from events.models import Event, EventHost, EventRegistration, Host
from events.services.workshop_ready_notification import (
    EMAIL_TYPE,
    INTERVAL_WORKSHOP_READY,
    WorkshopReadyNotReady,
    notify_workshop_ready,
)
from notifications.models import EventReminderLog, Notification

User = get_user_model()


def make_event(slug='workshop-ready-event', *, workshop_status='published'):
    start = datetime(2026, 6, 8, 16, 0, tzinfo=UTC)
    event = Event.objects.create(
        title='Workshop Ready Event',
        slug=slug,
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status='completed',
    )
    if workshop_status is not None:
        Workshop.objects.create(
            title='Workshop Ready Notes',
            slug=f'{slug}-workshop',
            description='Detailed **workshop** write-up for members.',
            date=date(2026, 6, 8),
            status=workshop_status,
            event=event,
        )
    return event


class WorkshopReadyServiceTest(TestCase):
    def setUp(self):
        self.event = make_event()
        self.user_a = User.objects.create_user(email='a@example.com')
        self.user_b = User.objects.create_user(email='b@example.com')
        self.host_user = User.objects.create_user(email='host@example.com')
        self.unrelated = User.objects.create_user(email='other@example.com')
        EventRegistration.objects.create(event=self.event, user=self.user_a)
        EventRegistration.objects.create(event=self.event, user=self.user_b)
        self.event.host_email = self.host_user.email
        self.event.save(update_fields=['host_email'])

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_sends_to_registrants_and_host_only(self, mock_send):
        mock_send.side_effect = ['ses-a', 'ses-b', 'ses-host']

        result = notify_workshop_ready(self.event)

        self.assertEqual(result['emailed'], 3)
        self.assertEqual(result['notified'], 3)
        self.assertEqual(result['already_sent'], 0)
        self.assertEqual(
            EmailLog.objects.filter(email_type=EMAIL_TYPE, event=self.event).count(),
            3,
        )
        self.assertEqual(
            Notification.objects.filter(
                notification_type='announcement',
                title='Workshop ready: Workshop Ready Notes',
            ).count(),
            3,
        )
        self.assertFalse(
            Notification.objects.filter(user=self.unrelated).exists(),
        )
        html = mock_send.call_args_list[0].args[2]
        self.assertIn('Workshop Ready Event', html)
        self.assertIn('Workshop Ready Notes', html)
        self.assertIn('/workshops/workshop-ready-event-workshop', html)

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_host_already_registered_is_deduped(self, mock_send):
        EventRegistration.objects.create(event=self.event, user=self.host_user)
        host = Host.objects.create(
            name='Host Person',
            slug='host-person',
            email=self.host_user.email.upper(),
            is_active=True,
        )
        EventHost.objects.create(event=self.event, host=host)

        result = notify_workshop_ready(self.event)

        self.assertEqual(result['emailed'], 3)
        self.assertEqual(
            EmailLog.objects.filter(user=self.host_user, email_type=EMAIL_TYPE).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(user=self.host_user).count(),
            1,
        )
        self.assertEqual(mock_send.call_count, 3)

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_rerun_skips_existing_and_reaches_new_registrant(self, mock_send):
        notify_workshop_ready(self.event)
        new_user = User.objects.create_user(email='new@example.com')
        EventRegistration.objects.create(event=self.event, user=new_user)

        result = notify_workshop_ready(self.event)

        self.assertEqual(result['emailed'], 1)
        self.assertEqual(result['notified'], 1)
        self.assertEqual(result['already_sent'], 3)
        self.assertEqual(
            EmailLog.objects.filter(email_type=EMAIL_TYPE, event=self.event).count(),
            4,
        )
        self.assertEqual(
            Notification.objects.filter(user=self.user_a).count(),
            1,
        )

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_unsubscribed_registrant_still_receives_transactional_email(self, mock_send):
        unsubscribed = User.objects.create_user(
            email='unsub@example.com',
            unsubscribed=True,
        )
        EventRegistration.objects.create(event=self.event, user=unsubscribed)

        notify_workshop_ready(self.event)

        self.assertTrue(
            EmailLog.objects.filter(user=unsubscribed, email_type=EMAIL_TYPE).exists(),
        )
        self.assertEqual(classify_email_type(EMAIL_TYPE), EMAIL_KIND_TRANSACTIONAL)

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_non_user_host_receives_email_only(self, mock_send):
        host = Host.objects.create(
            name='External Host',
            slug='external-host',
            email='external-host@example.com',
            is_active=True,
        )
        EventHost.objects.create(event=self.event, host=host)

        result = notify_workshop_ready(self.event)

        self.assertEqual(result['emailed'], 4)
        self.assertEqual(result['notified'], 3)
        self.assertTrue(
            EmailLog.objects.filter(
                email_type=EMAIL_TYPE,
                recipient_email='external-host@example.com',
                user__isnull=True,
            ).exists(),
        )
        self.assertFalse(Notification.objects.filter(user__isnull=True).exists())
        self.assertTrue(
            any(item['email_only'] for item in result['results']),
        )

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_inactive_host_user_is_skipped_not_email_only(self, mock_send):
        inactive = User.objects.create_user(
            email='inactive-host@example.com',
            is_active=False,
        )
        self.event.host_email = inactive.email
        self.event.save(update_fields=['host_email'])

        notify_workshop_ready(self.event)

        self.assertFalse(
            EmailLog.objects.filter(recipient_email=inactive.email).exists(),
        )
        self.assertFalse(
            EmailLog.objects.filter(user=inactive).exists(),
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_recipient_failure_is_reported_and_rest_continue(self, mock_send):
        def send(to_email, *args, **kwargs):
            if to_email == 'b@example.com':
                raise RuntimeError('SES failed')
            return f'ses-{to_email}'

        mock_send.side_effect = send

        result = notify_workshop_ready(self.event)

        self.assertEqual(result['emailed'], 2)
        self.assertEqual(result['failed'], 1)
        self.assertTrue(
            EmailLog.objects.filter(user=self.user_a, email_type=EMAIL_TYPE).exists(),
        )
        self.assertFalse(
            EventReminderLog.objects.filter(
                user=self.user_b,
                interval=INTERVAL_WORKSHOP_READY,
            ).exists(),
        )

    def test_blocks_without_published_linked_workshop(self):
        no_workshop = make_event('no-workshop', workshop_status=None)
        draft_workshop = make_event('draft-workshop', workshop_status='draft')

        for event in (no_workshop, draft_workshop):
            with self.subTest(event=event.slug):
                with self.assertRaises(WorkshopReadyNotReady):
                    notify_workshop_ready(event)

        self.assertFalse(EmailLog.objects.filter(email_type=EMAIL_TYPE).exists())
        self.assertFalse(Notification.objects.exists())
        self.assertFalse(
            EventReminderLog.objects.filter(interval=INTERVAL_WORKSHOP_READY).exists(),
        )


class WorkshopReadyStudioAndApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@example.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(email='member@example.com')
        cls.token = Token.objects.create(user=cls.staff, name='workshop-ready')
        cls.event = make_event('studio-api-workshop-ready')
        EventRegistration.objects.create(event=cls.event, user=cls.member)

    def setUp(self):
        self.client.login(email='staff@example.com', password='pw')

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_studio_endpoint_sends_and_reports_counts(self, mock_send):
        response = self.client.post(
            f'/studio/events/{self.event.pk}/notify-workshop-ready',
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 emailed')
        self.assertContains(response, '1 in-app notifications')
        self.assertContains(response, 'Audience: event registrants plus host')
        self.assertTrue(
            EmailLog.objects.filter(user=self.member, email_type=EMAIL_TYPE).exists(),
        )

    def test_studio_edit_shows_disabled_state_without_published_workshop(self):
        event = make_event('studio-no-workshop', workshop_status=None)

        response = self.client.get(f'/studio/events/{event.pk}/edit')

        self.assertContains(response, 'data-testid="notify-workshop-ready-button-disabled"')
        self.assertContains(response, 'A linked published workshop is required')

    @patch('email_app.services.email_service.EmailService._send_ses', return_value='ses')
    def test_api_endpoint_success_and_error_cases(self, mock_send):
        auth = {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

        response = self.client.post(
            '/api/events/studio-api-workshop-ready/notify-workshop-ready',
            **auth,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['emailed'], 1)
        self.assertEqual(response.json()['notified'], 1)

        response = self.client.post(
            '/api/events/missing-event/notify-workshop-ready',
            **auth,
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_event')

        event = make_event('api-draft-workshop', workshop_status='draft')
        response = self.client.post(
            f'/api/events/{event.slug}/notify-workshop-ready',
            **auth,
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'workshop_not_ready')

        response = self.client.post(
            '/api/events/studio-api-workshop-ready/notify-workshop-ready',
        )
        self.assertIn(response.status_code, (401, 403))

    def test_openapi_documents_endpoint(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        document = build_spec(urlpatterns)
        path = '/api/events/{slug}/notify-workshop-ready'

        self.assertIn(path, document['paths'])
        operation = document['paths'][path]['post']
        self.assertEqual(
            operation['summary'],
            'Send workshop-ready broadcast',
        )
        for status in ('200', '401', '404', '422'):
            self.assertIn(status, operation['responses'])
