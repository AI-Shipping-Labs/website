"""Staff API action for announcing an event recap (issue #1557)."""

from datetime import timedelta
from unittest.mock import ANY, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from notifications.models import Notification

User = get_user_model()


@tag('core')
class EventRecapNotificationApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user(
            email='recap-api-staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='recap-api-member@test.com', email_verified=True,
        )
        cls.non_staff = User.objects.create_user(
            email='recap-api-non-staff@test.com', email_verified=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='recap-api')
        cls.non_staff_token = Token(
            key='recap-api-non-staff-token',
            user=cls.non_staff,
            name='recap-api-non-staff',
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.event = Event.objects.create(
            title='API Recap Event',
            slug='api-recap-event',
            description='An API event.',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            recap_notes='## Recap\n\nThe recording is summarized here.',
        )
        EventRegistration.objects.create(event=cls.event, user=cls.member)

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    @patch(
        'events.services.event_recap_notification.EmailService._send_ses',
        return_value='ses-api-recap',
    )
    def test_post_returns_delivery_summary_and_canonical_absolute_url(self, mock_send):
        response = self.client.post(
            f'/api/events/{self.event.slug}/notify-recap-ready',
            **self._auth(),
        )

        body = response.json()
        self.assertEqual(body['event']['id'], self.event.pk)
        self.assertEqual(body['event']['slug'], self.event.slug)
        self.assertTrue(body['recap_url'].startswith('https://aishippinglabs.com/'))
        self.assertEqual(body['emailed'], 1)
        self.assertEqual(body['notified'], 1)
        self.assertEqual(body['results'][0]['user_id'], self.member.pk)
        self.assertEqual(body['results'][0]['email_status'], 'sent')
        self.assertEqual(
            body['results'][0]['email_log_id'],
            EmailLog.objects.get(
                event=self.event, email_type='event_recap_ready',
            ).pk,
        )
        self.assertEqual(
            body['results'][0]['notification_id'],
            Notification.objects.get(
                user=self.member, notification_type='event_recap',
            ).pk,
        )
        mock_send.assert_called_once_with(
            self.member.email,
            'Recap ready: API Recap Event',
            ANY,
            email_type='event_recap_ready',
            unsubscribe_url=None,
            cc=None,
            bcc=None,
        )

    def test_missing_recap_returns_stable_422_reason(self):
        Event.objects.filter(pk=self.event.pk).update(
            recap_notes='', recap_notes_html='',
        )

        response = self.client.post(
            f'/api/events/{self.event.slug}/notify-recap-ready',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'recap_not_ready')
        self.assertEqual(
            response.json()['details']['reason'],
            'missing_recap',
        )

    def test_recap_patch_does_not_announce_automatically(self):
        Event.objects.filter(pk=self.event.pk).update(
            recap_notes='', recap_notes_html='',
        )

        response = self.client.patch(
            f'/api/events/{self.event.slug}',
            data='{"recap_notes": "## Newly published recap"}',
            content_type='application/json',
            **self._auth(),
        )

        body = response.json()
        self.assertEqual(body['recap_notes'], '## Newly published recap')
        self.assertFalse(
            EmailLog.objects.filter(
                event=self.event, email_type='event_recap_ready',
            ).exists(),
        )
        self.assertFalse(
            Notification.objects.filter(
                user=self.member, notification_type='event_recap',
            ).exists(),
        )

    def test_unknown_event_returns_404(self):
        response = self.client.post(
            '/api/events/does-not-exist/notify-recap-ready',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_event')

    def test_anonymous_and_non_staff_callers_cannot_trigger_delivery(self):
        for headers in (
            {},
            {'HTTP_AUTHORIZATION': f'Token {self.non_staff_token.key}'},
        ):
            with self.subTest(headers=headers):
                response = self.client.post(
                    f'/api/events/{self.event.slug}/notify-recap-ready',
                    **headers,
                )

                self.assertEqual(response.status_code, 401)
                self.assertEqual(
                    response.json()['code'],
                    'authentication_required'
                    if not headers else 'invalid_token',
                )
                self.assertFalse(
                    EmailLog.objects.filter(
                        event=self.event, email_type='event_recap_ready',
                    ).exists(),
                )
                self.assertFalse(
                    Notification.objects.filter(
                        user=self.member, notification_type='event_recap',
                    ).exists(),
                )
