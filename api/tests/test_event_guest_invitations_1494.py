import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import Client, TestCase, TransactionTestCase, tag
from django.utils import timezone
from icalendar import Calendar

from accounts.models import EmailAlias, Token
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    GuestInviteDelivery,
    HostInviteDelivery,
    SeriesOccurrenceOptOut,
    SeriesRegistration,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


class EventGuestInvitationApiTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='guest-api-staff@test.com', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='guest-api')
        cls.host = User.objects.create_user(
            email='alexey@datatalks.club', tier=cls.premium_tier,
            email_verified=True,
        )
        cls.guest = User.objects.create_user(
            email='alexey.s.grigoriev@gmail.com', tier=cls.free_tier,
            email_verified=True,
        )
        cls.start = timezone.now() + timedelta(days=7)
        cls.event = Event.objects.create(
            title='Remote agentic workloads',
            slug='remote-agentic-workloads',
            description='Set up a remote environment.',
            start_datetime=cls.start,
            end_datetime=cls.start + timedelta(hours=1),
            status='upcoming',
            published=True,
            required_level=20,
            host_email=cls.host.email,
        )

    def auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def post(self, event=None, **payload):
        event = event or self.event
        return self.client.post(
            f'/api/events/id/{event.pk}/guest-invitations',
            data=json.dumps(payload),
            content_type='application/json',
            **self.auth(),
        )

    def test_routes_require_staff_token_before_reading_or_writing(self):
        response = self.client.post(
            f'/api/events/id/{self.event.pk}/guest-invitations',
            data=json.dumps({'email': self.guest.email}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(GuestInviteDelivery.objects.count(), 0)

    @patch('events.services.registration_email.send_registration_confirmation')
    def test_id_get_post_and_readback_override_tier_event_only(self, send):
        send.return_value = None
        series = EventSeries.objects.create(name='Agent series', slug='agent-series')
        self.event.event_series = series
        self.event.save(update_fields=['event_series'])
        sibling = Event.objects.create(
            title='Sibling', slug='sibling', description='Sibling',
            start_datetime=self.start + timedelta(days=1), status='upcoming',
            published=True, required_level=20, event_series=series,
        )

        identity = self.client.get(
            f'/api/events/id/{self.event.pk}', **self.auth(),
        )
        self.assertEqual(identity.json()['id'], self.event.pk)
        self.assertEqual(identity.json()['slug'], self.event.slug)

        response = self.post(email=self.guest.email)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        registration = EventRegistration.objects.get(
            event=self.event, user=self.guest,
        )
        self.assertEqual(body['registration_id'], registration.pk)
        self.assertEqual(body['registration_status'], 'registered')
        self.assertEqual(body['email_status'], 'sent')
        self.assertFalse(EventRegistration.objects.filter(event=sibling).exists())
        self.assertFalse(SeriesRegistration.objects.exists())
        self.assertFalse(SeriesOccurrenceOptOut.objects.exists())

        readback = self.client.get(
            f'/api/events/id/{self.event.pk}/guest-invitations/{registration.pk}',
            **self.auth(),
        )
        self.assertEqual(readback.json()['guest_email'], self.guest.email)
        self.assertEqual(readback.json()['email_status'], 'sent')

    @patch('events.services.registration_email._send_raw_email')
    def test_guest_delivery_uses_attendee_calendar_and_cancel_join_links(self, send):
        send.return_value = 'guest-message-id'
        response = self.post(email=self.guest.email)

        self.assertEqual(response.status_code, 201)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs['to_email'], self.guest.email)
        self.assertIn(self.event.get_join_url(), kwargs['html_body'])
        self.assertIn('cancel-registration?token=', kwargs['html_body'])
        self.assertNotIn('Manage event', kwargs['html_body'])
        calendar = Calendar.from_ical(kwargs['ics_content'])
        component = calendar.walk('VEVENT')[0]
        self.assertEqual(str(calendar.get('method')), 'REQUEST')
        self.assertTrue(str(component.get('url')).endswith(self.event.get_join_url()))
        self.assertIn(self.guest.email, str(component.get('attendee')))
        self.assertEqual(HostInviteDelivery.objects.count(), 0)

    @patch('events.services.registration_email.send_registration_confirmation')
    def test_repeat_success_is_idempotent(self, send):
        send.return_value = None
        first = self.post(email=self.guest.email)
        second = self.post(email=self.guest.email)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.json()['registration_status'], 'already_registered')
        self.assertEqual(second.json()['email_status'], 'already_sent')
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(GuestInviteDelivery.objects.count(), 1)
        self.assertEqual(send.call_count, 1)

    @patch('events.services.guest_invitation._send_verification_once')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_new_mailbox_gets_free_unverified_account_and_one_claim_email(
        self, send, verify,
    ):
        send.return_value = None
        first = self.post(email='New.Guest@Example.COM')
        second = self.post(email='new.guest@example.com')

        user = User.objects.get(email='new.guest@example.com')
        self.assertFalse(user.email_verified)
        self.assertEqual(user.tier, self.free_tier)
        self.assertIsNotNone(user.verification_expires_at)
        self.assertEqual(first.json()['guest_email'], 'new.guest@example.com')
        self.assertEqual(second.json()['email_status'], 'already_sent')
        verify.assert_called_once_with(user)

    def test_alias_and_operational_host_conflicts_have_zero_side_effects(self):
        canonical = User.objects.create_user(email='canonical@test.com')
        EmailAlias.objects.create(
            user=canonical, email='alias@test.com', source='manual',
        )
        before_users = User.objects.count()
        cases = [
            ('alias@test.com', 'guest_email_is_alias'),
            (self.host.email, 'guest_is_operational_host'),
        ]
        for email, code in cases:
            with self.subTest(email=email):
                response = self.post(email=email)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['code'], code)
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(GuestInviteDelivery.objects.count(), 0)
        self.assertEqual(HostInviteDelivery.objects.count(), 0)

    def test_ineligible_event_matrix_and_invalid_email_have_zero_side_effects(self):
        cases = [
            ({'published': False}, 'event_not_published'),
            ({'status': 'draft'}, 'event_not_upcoming'),
            ({'status': 'cancelled'}, 'event_not_upcoming'),
            ({'status': 'completed'}, 'event_not_upcoming'),
            ({'external_host': 'Luma'}, 'external_event'),
            ({'end_datetime': timezone.now() - timedelta(minutes=1)}, 'event_not_upcoming'),
        ]
        for index, (overrides, code) in enumerate(cases):
            event_fields = {
                'title': f'Invalid {index}', 'slug': f'invalid-{index}',
                'description': 'Invalid',
                'start_datetime': self.start,
                'end_datetime': self.start + timedelta(hours=1),
                'status': 'upcoming', 'published': True,
            }
            event_fields.update(overrides)
            event = Event.objects.create(
                **event_fields,
            )
            with self.subTest(code=code):
                response = self.post(event, email='unused-new@test.com')
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()['code'], code)
        bad = self.post(email='not-an-email')
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(User.objects.filter(email='unused-new@test.com').count(), 0)
        self.assertEqual(EventRegistration.objects.count(), 0)

    @patch('events.services.guest_invitation._send_verification_once')
    @patch('events.services.registration_email.send_registration_confirmation')
    def test_dry_run_creates_nothing(self, send, verify):
        before_users = User.objects.count()
        response = self.post(
            email='preview-only@test.com', dry_run=True,
        )
        self.assertEqual(response.json()['registration_status'], 'would_register')
        self.assertEqual(response.json()['email_status'], 'would_send')
        self.assertEqual(User.objects.count(), before_users)
        self.assertEqual(EventRegistration.objects.count(), 0)
        self.assertEqual(GuestInviteDelivery.objects.count(), 0)
        send.assert_not_called()
        verify.assert_not_called()

    @patch('events.services.registration_email.send_registration_confirmation')
    def test_retryable_failure_reuses_registration_then_sends_once(self, send):
        send.side_effect = [TimeoutError('provider detail'), None]
        failed = self.post(email=self.guest.email)
        registration_id = failed.json()['registration_id']
        retried = self.post(email=self.guest.email)

        self.assertEqual(failed.json()['email_status'], 'failed_retryable')
        self.assertEqual(retried.json()['registration_id'], registration_id)
        self.assertEqual(retried.json()['email_status'], 'sent')
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(send.call_count, 2)

    @patch('events.services.registration_email.send_registration_confirmation')
    def test_guest_registration_keeps_attendee_reschedule_and_cancel_lifecycle(
        self, initial_send,
    ):
        from events.tasks.notify_cancellation import send_cancellation_notice_one
        from events.tasks.notify_reschedule import send_reschedule_notice_one

        initial_send.return_value = None
        self.post(email=self.guest.email)
        old_start = self.event.start_datetime
        self.event.start_datetime += timedelta(days=1)
        self.event.end_datetime += timedelta(days=1)
        self.event.ics_sequence += 1
        self.event.save(update_fields=[
            'start_datetime', 'end_datetime', 'ics_sequence',
        ])

        with patch(
            'events.tasks.notify_reschedule._send_raw_email',
            return_value='guest-reschedule',
        ) as reschedule_send:
            rescheduled = send_reschedule_notice_one(
                self.event.pk, self.guest.pk, old_start.isoformat(),
            )

        self.event.status = 'cancelled'
        self.event.ics_sequence += 1
        self.event.save(update_fields=['status', 'ics_sequence'])
        with patch(
            'events.tasks.notify_cancellation._send_raw_email',
            return_value='guest-cancellation',
        ) as cancellation_send:
            cancelled = send_cancellation_notice_one(
                self.event.pk, self.guest.pk,
            )

        self.assertEqual(rescheduled['status'], 'sent')
        self.assertEqual(cancelled['status'], 'sent')
        self.assertEqual(
            reschedule_send.call_args.kwargs['to_email'], self.guest.email,
        )
        self.assertEqual(
            cancellation_send.call_args.kwargs['to_email'], self.guest.email,
        )
        self.assertTrue(EventRegistration.objects.filter(
            event=self.event, user=self.guest,
        ).exists())


@tag('core', 'postgresql')
class EventGuestInvitationConcurrencyTest(TransactionTestCase):
    """Production row locks serialize two identical invitation requests."""

    def test_concurrent_requests_create_and_send_exactly_once(self):
        if connection.vendor != 'postgresql':
            self.skipTest(
                'guest invitation concurrency requires PostgreSQL row locking',
            )

        staff = User.objects.create_user(
            email='concurrent-guest-staff@test.com', is_staff=True,
        )
        token = Token.objects.create(user=staff, name='concurrent-guest')
        guest = User.objects.create_user(email='concurrent-guest@test.com')
        start = timezone.now() + timedelta(days=7)
        event = Event.objects.create(
            title='Concurrent guest invitation',
            slug='concurrent-guest-invitation',
            description='One delivery under contention.',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            published=True,
        )
        barrier = threading.Barrier(2)
        send_count = 0
        send_lock = threading.Lock()

        def record_send(_registration):
            nonlocal send_count
            with send_lock:
                send_count += 1

        def invite():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                response = Client().post(
                    f'/api/events/id/{event.pk}/guest-invitations',
                    data=json.dumps({'email': guest.email}),
                    content_type='application/json',
                    HTTP_AUTHORIZATION=f'Token {token.key}',
                )
                return response.status_code, response.json()
            finally:
                connection.close()

        with (
            patch(
                'events.services.registration_email.send_registration_confirmation',
                side_effect=record_send,
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            results = list(pool.map(lambda _: invite(), range(2)))

        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        self.assertEqual(
            sorted(body['registration_status'] for _, body in results),
            ['already_registered', 'registered'],
        )
        self.assertEqual(
            sorted(body['email_status'] for _, body in results),
            ['already_sent', 'sent'],
        )
        self.assertEqual(EventRegistration.objects.count(), 1)
        self.assertEqual(GuestInviteDelivery.objects.count(), 1)
        self.assertEqual(send_count, 1)
