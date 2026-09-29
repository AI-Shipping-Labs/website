"""Staff API action for announcing an event recap (issue #1557)."""

import datetime
from datetime import timedelta

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from content.models import Cohort, CohortEnrollment, Course
from email_app.models import EmailLog
from email_app.testing import deliver_pending_mail
from events.models import Event, EventRegistration, EventSeries
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

    def test_post_returns_delivery_summary_and_canonical_absolute_url(self):
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
        # A1.2 slice 3: the email channel records a durable delivery; the
        # summary id is the delivery id until the worker writes the
        # EmailLog audit row (which keeps the event FK).
        delivery = EmailDelivery.objects.get(
            purpose='event_recap_ready', recipient_user=self.member,
        )
        self.assertEqual(
            body['results'][0]['email_log_id'], str(delivery.pk),
        )
        # Issue #1613: the durable context stores no URL; the worker
        # mints the recap link from the related event.
        self.assertEqual(delivery.context_data, {})
        self.assertEqual(
            body['results'][0]['notification_id'],
            Notification.objects.get(
                user=self.member, notification_type='event_recap',
            ).pk,
        )
        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.get(
                event=self.event, email_type='event_recap_ready',
            ).dedupe_key,
            delivery.idempotency_key,
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


@tag('core')
class EventNotifyRecapByIdApiTest(TestCase):
    """``POST /api/events/<id>/notify-recap[?dry_run=true]``."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.staff = User.objects.create_user(
            email='notify-recap-staff@test.com', password='pw', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='notify-recap')
        cls.non_staff = User.objects.create_user(email='notify-recap-member-token@test.com')
        cls.non_staff_token = Token(
            key='notify-recap-non-staff-token', user=cls.non_staff, name='non-staff',
        )
        Token.objects.bulk_create([cls.non_staff_token])
        series = EventSeries.objects.create(name='Office Hours', slug='oh-notify-recap')
        cls.event = Event.objects.create(
            title='Notify Recap Event',
            slug='notify-recap-event',
            start_datetime=now - timedelta(hours=3),
            end_datetime=now - timedelta(hours=1),
            status='completed',
            published=True,
            event_series=series,
            recap_notes='## Recap\n\nSummary.',
        )
        cls.registrant = User.objects.create_user(
            email='notify-recap-registrant@test.com', email_verified=True,
        )
        cls.cohort_member = User.objects.create_user(
            email='notify-recap-cohort@test.com', email_verified=True,
        )
        EventRegistration.objects.create(event=cls.event, user=cls.registrant)
        course = Course.objects.create(
            title='Camp', slug='camp-notify-recap', status='published',
        )
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date=datetime.date(2026, 9, 1),
            end_date=datetime.date(2026, 12, 1),
            event_series=series,
        )
        CohortEnrollment.objects.create(cohort=cohort, user=cls.cohort_member)

    def _post(self, query='', token=None):
        key = (token or self.token).key
        return self.client.post(
            f'/api/events/{self.event.pk}/notify-recap{query}',
            HTTP_AUTHORIZATION=f'Token {key}',
        )

    def test_dry_run_lists_audience_with_reasons_and_sends_nothing(self):
        response = self._post('?dry_run=true')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['dry_run'])
        self.assertEqual(body['would_email'], 2)
        reasons = {
            item['email']: [reason['source'] for reason in item['reasons']]
            for item in body['results']
        }
        self.assertEqual(reasons, {
            'notify-recap-registrant@test.com': ['registered'],
            'notify-recap-cohort@test.com': ['cohort'],
        })
        self.assertFalse(EmailDelivery.objects.exists())

    def test_send_reaches_registrant_and_cohort_member_once(self):
        first = self._post().json()
        second = self._post('?dry_run=false').json()

        self.assertFalse(first['dry_run'])
        self.assertEqual(first['emailed'], 2)
        self.assertEqual(first['by_reason'], {'cohort': 1, 'registered': 1})
        self.assertEqual(second['emailed'], 0)
        self.assertEqual(second['already_emailed'], 2)
        self.assertEqual(
            set(EmailDelivery.objects.values_list('recipient_email', flat=True)),
            {'notify-recap-registrant@test.com', 'notify-recap-cohort@test.com'},
        )

    def test_invalid_dry_run_unknown_event_and_not_ready(self):
        invalid = self._post('?dry_run=maybe')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()['code'], 'invalid_dry_run')

        missing = self.client.post(
            '/api/events/999999/notify-recap',
            HTTP_AUTHORIZATION=f'Token {self.token.key}',
        )
        self.assertEqual(missing.status_code, 404)

        Event.objects.filter(pk=self.event.pk).update(recap_notes='', recap_notes_html='')
        not_ready = self._post()
        self.assertEqual(not_ready.status_code, 422)
        self.assertEqual(not_ready.json()['details']['reason'], 'missing_recap')
        preview = self._post('?dry_run=1').json()
        self.assertFalse(preview['ready'])
        self.assertEqual(preview['eligible'], 2)
        self.assertFalse(EmailDelivery.objects.exists())

    def test_non_staff_token_cannot_preview_or_send(self):
        for query in ('?dry_run=true', ''):
            with self.subTest(query=query):
                response = self._post(query, token=self.non_staff_token)
                self.assertEqual(response.status_code, 401)
        self.assertFalse(EmailDelivery.objects.exists())
