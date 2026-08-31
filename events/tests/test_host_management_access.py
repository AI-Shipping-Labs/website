"""Retired host-management routes and retained host lifecycle tests (#1550)."""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.utils import timezone
from icalendar import Calendar

from email_app.models import EmailLog
from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration, HostInviteDelivery
from events.services.cancel_token import generate_cancel_token
from events.services.host_registration import maybe_register_host_as_attendee
from events.tasks.notify_cancellation import send_cancellation_notice_one
from events.tasks.notify_reschedule import send_reschedule_notice_one
from integrations.config import site_base_url

User = get_user_model()


def _event(host):
    start = timezone.now() + timedelta(days=7)
    return Event.objects.create(
        title='Retired Host Controls Event',
        slug='retired-host-controls-event',
        description='Private event description marker.',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status='upcoming',
        platform='zoom',
        location='Private location marker',
        zoom_join_url='https://meeting.test/join/private',
        host_email=host.email,
    )


def _vevent(raw):
    calendar = Calendar.from_ical(raw)
    return [item for item in calendar.walk() if item.name == 'VEVENT'][0]


@tag('core')
class HostManagementActionTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            email='host-actions@test.com', password='pw',
        )
        self.member = User.objects.create_user(
            email='member-actions@test.com', password='pw',
        )
        self.staff = User.objects.create_user(
            email='staff-actions@test.com', password='pw', is_staff=True,
        )
        self.event = _event(self.host)
        self.registration = EventRegistration.objects.create(
            event=self.event, user=self.host,
        )

    def test_removed_routes_return_non_disclosing_404_for_every_role(self):
        routes = (
            ('get', f'/events/{self.event.pk}/host/manage?token=stale-value'),
            ('post', f'/events/{self.event.pk}/host/update'),
            ('post', f'/events/{self.event.pk}/host/create-zoom'),
            ('post', f'/events/{self.event.pk}/host/notify'),
        )
        for user in (None, self.member, self.host, self.staff):
            for method, path in routes:
                with self.subTest(
                    role='anonymous' if user is None else user.email,
                    method=method,
                    path=path,
                ):
                    client = Client()
                    if user is not None:
                        client.force_login(user)
                    response = getattr(client, method)(
                        path,
                        {'token': 'stale-value', 'title': 'Forged title'},
                    )
                    self.assertEqual(response.status_code, 404)
                    self.assertNotContains(
                        response, self.event.title, status_code=404,
                    )
                    self.assertNotContains(
                        response, 'Private location marker', status_code=404,
                    )
                    self.assertNotContains(
                        response, self.host.email, status_code=404,
                    )
                    self.assertNotContains(
                        response, 'stale-value', status_code=404,
                    )

    @patch('notifications.services.NotificationService.notify')
    @patch('integrations.services.zoom.create_meeting')
    @patch('events.services.calendar_lifecycle.enqueue_schedule_update')
    def test_forged_update_has_no_side_effects(
        self, mock_enqueue, mock_create_meeting, mock_notify,
    ):
        original = {
            field: getattr(self.event, field)
            for field in (
                'title', 'start_datetime', 'end_datetime', 'location',
                'status', 'zoom_join_url',
            )
        }
        client = Client()
        client.force_login(self.host)

        response = client.post(
            f'/events/{self.event.pk}/host/update',
            {
                'title': 'Forged title',
                'start_datetime': (timezone.now() + timedelta(days=30)).isoformat(),
                'end_datetime': (timezone.now() + timedelta(days=31)).isoformat(),
                'location': 'Forged location',
                'status': 'cancelled',
                'zoom_join_url': 'https://meeting.test/join/forged',
            },
        )

        self.assertEqual(response.status_code, 404)
        self.event.refresh_from_db()
        self.assertEqual(
            {field: getattr(self.event, field) for field in original},
            original,
        )
        mock_enqueue.assert_not_called()
        mock_create_meeting.assert_not_called()
        mock_notify.assert_not_called()

    def test_current_host_cannot_silently_cancel_lifecycle_registration(self):
        cancel_token = generate_cancel_token(self.registration)
        response = self.client.post(
            f'/api/events/{self.event.slug}/cancel-registration?token={cancel_token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'The current host registration cannot be cancelled. '
            'Ask an operator to reassign the event host first.',
        )
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


@tag('core')
class HostDeliveryRecoveryTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(email='delivery-host@test.com')
        self.event = _event(self.host)

    @patch('events.services.registration_email._send_raw_email')
    def test_failure_is_visible_retryable_and_bounded(self, mock_send):
        mock_send.side_effect = RuntimeError('provider unavailable')
        for _ in range(4):
            maybe_register_host_as_attendee(self.event)

        delivery = HostInviteDelivery.objects.get(
            event=self.event,
            user=self.host,
            access_version=self.event.host_access_version,
        )
        self.assertEqual(delivery.status, HostInviteDelivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, HostInviteDelivery.MAX_ATTEMPTS)
        self.assertEqual(delivery.last_error, HostInviteDelivery.ERROR_PROVIDER)
        self.assertEqual(mock_send.call_count, HostInviteDelivery.MAX_ATTEMPTS)

    @patch('events.services.registration_email._send_raw_email')
    def test_provider_exception_detail_is_never_persisted_or_rendered(
        self, mock_send,
    ):
        private_detail = 'provider-private-diagnostic-detail'
        mock_send.side_effect = RuntimeError(private_detail)
        with self.assertLogs(
            'events.services.host_registration', level='ERROR',
        ) as logs:
            maybe_register_host_as_attendee(self.event)
        self.assertNotIn(private_detail, '\n'.join(logs.output))
        self.assertIn(HostInviteDelivery.ERROR_PROVIDER, '\n'.join(logs.output))

        delivery = HostInviteDelivery.objects.get(
            event=self.event,
            user=self.host,
            access_version=self.event.host_access_version,
        )
        self.assertEqual(delivery.last_error, HostInviteDelivery.ERROR_PROVIDER)
        self.assertNotIn(private_detail, delivery.last_error)

        staff = User.objects.create_user(
            email='delivery-staff@test.com', password='pw', is_staff=True,
        )
        self.client.login(email=staff.email, password='pw')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, private_detail)
        self.assertContains(response, 'Delivery failed; review application logs.')

        delivery.last_error = private_detail
        delivery.save(update_fields=['last_error'])
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertNotContains(response, private_detail)
        self.assertContains(response, 'Delivery failed; review application logs.')

    @patch('events.services.registration_email._send_raw_email')
    def test_retry_succeeds_once_without_duplicate_registration(self, mock_send):
        mock_send.side_effect = [RuntimeError('temporary'), 'ses-recovered']
        maybe_register_host_as_attendee(self.event)
        maybe_register_host_as_attendee(self.event)
        maybe_register_host_as_attendee(self.event)

        delivery = HostInviteDelivery.objects.get(
            event=self.event,
            user=self.host,
            access_version=self.event.host_access_version,
        )
        self.assertEqual(delivery.status, HostInviteDelivery.STATUS_SENT)
        self.assertEqual(delivery.attempt_count, 2)
        self.assertEqual(mock_send.call_count, 2)
        self.assertNotIn('cancel-registration', mock_send.call_args.kwargs['html_body'])
        self.assertNotIn('/host/', mock_send.call_args.kwargs['html_body'])
        self.assertNotIn('/studio/', mock_send.call_args.kwargs['html_body'])
        self.assertEqual(
            EventRegistration.objects.filter(
                event=self.event, user=self.host,
            ).count(),
            1,
        )
        log = EmailLog.objects.get(
            dedupe_key=(
                f'event-host-registration:{self.event.pk}:{self.host.pk}:'
                f'{self.event.host_access_version}'
            ),
        )
        self.assertEqual(log.event, self.event)
        self.assertEqual(delivery.email_log, log)

    @patch('events.services.registration_email._send_raw_email')
    def test_reassignment_back_to_former_host_sends_fresh_invitation(
        self, mock_send,
    ):
        other = User.objects.create_user(email='replacement-host@test.com')
        mock_send.side_effect = ['ses-first', 'ses-other', 'ses-returned']

        maybe_register_host_as_attendee(self.event)
        first_version = self.event.host_access_version
        self.event.host_email = other.email
        self.event.save(update_fields=['host_email'])
        maybe_register_host_as_attendee(self.event)
        self.event.host_email = self.host.email
        self.event.save(update_fields=['host_email'])
        maybe_register_host_as_attendee(self.event)

        self.assertNotEqual(self.event.host_access_version, first_version)
        self.assertEqual(mock_send.call_count, 3)
        self.assertEqual(
            HostInviteDelivery.objects.filter(
                event=self.event,
                user=self.host,
                status=HostInviteDelivery.STATUS_SENT,
            ).count(),
            2,
        )


@tag('core')
class HostLifecycleCopyRenderingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='copy-host@test.com', first_name='Ada',
        )

    def _render(self, template_name, context):
        _subject, body_html = EmailService()._render_template(
            template_name, self.user, context,
        )
        return body_html

    def assert_calendar_copy_is_qualified(self, body_html):
        lowered = body_html.lower()
        for rejected_claim in (
            'attached .ics',
            'attached <code>.ics</code>',
            'calendar cancellation is attached',
            'overwrite the original entry automatically',
        ):
            self.assertNotIn(rejected_claim, lowered)

    def test_host_and_attendee_registration_share_non_cancel_copy(self):
        base_context = {
            'event_title': 'Host Lifecycle Workshop',
            'event_datetime': 'Saturday, March 21, 2026, 18:00 UTC',
            'timezone_help': 'Times are shown in UTC.',
            'join_url': 'https://site.test/events/1/workshop/join',
            'cancel_url': 'https://site.test/events/workshop/cancel-registration',
            'google_calendar_url': 'https://calendar.test/google',
            'outlook_calendar_url': 'https://calendar.test/outlook',
            'office365_calendar_url': 'https://calendar.test/office',
        }
        host_html = self._render(
            'event_registration',
            {**base_context, 'is_host_registration': True},
        )
        attendee_html = self._render(
            'event_registration',
            {**base_context, 'is_host_registration': False},
        )

        for shared_copy in (
            base_context['join_url'],
            base_context['timezone_help'],
            'Add to your calendar',
            'includes a calendar invitation for this event',
        ):
            self.assertIn(shared_copy, host_html)
            self.assertIn(shared_copy, attendee_html)
        self.assertIn(
            "You're the designated host for this event, so this registration "
            "can't be cancelled from here. Ask an operator if the host needs "
            'to change.',
            host_html,
        )
        self.assertNotIn(base_context['cancel_url'], host_html)
        self.assertIn(base_context['cancel_url'], attendee_html)
        for removed_copy in ('Host management links', '/host/', '/studio/'):
            self.assertNotIn(removed_copy, host_html)
            self.assertNotIn(removed_copy, attendee_html)
        self.assert_calendar_copy_is_qualified(host_html)
        self.assert_calendar_copy_is_qualified(attendee_html)

    def test_host_and_attendee_reschedule_share_non_cancel_copy(self):
        base_context = {
            'event_title': 'Host Lifecycle Workshop',
            'old_event_datetime': 'Saturday, March 21, 2026, 18:00 UTC',
            'new_event_datetime': 'Saturday, March 28, 2026, 18:00 UTC',
            'timezone_help': 'Times are shown in UTC.',
            'join_url': 'https://site.test/events/1/workshop/join',
            'cancel_url': 'https://site.test/events/workshop/cancel-registration',
        }
        host_html = self._render(
            'event_rescheduled',
            {**base_context, 'is_host_registration': True},
        )
        attendee_html = self._render(
            'event_rescheduled',
            {**base_context, 'is_host_registration': False},
        )

        for shared_copy in (
            base_context['join_url'],
            base_context['timezone_help'],
            base_context['old_event_datetime'],
            base_context['new_event_datetime'],
            'includes an updated calendar invitation',
        ):
            self.assertIn(shared_copy, host_html)
            self.assertIn(shared_copy, attendee_html)
        self.assertIn(
            "You're the designated host for this event, so this registration "
            "can't be cancelled from here. Ask an operator if the host needs "
            'to change.',
            host_html,
        )
        self.assertNotIn(base_context['cancel_url'], host_html)
        self.assertIn(base_context['cancel_url'], attendee_html)
        for removed_copy in ('Host management links', '/host/', '/studio/'):
            self.assertNotIn(removed_copy, host_html)
            self.assertNotIn(removed_copy, attendee_html)
        self.assert_calendar_copy_is_qualified(host_html)
        self.assert_calendar_copy_is_qualified(attendee_html)

    def test_cancellation_renders_prompt_aware_supported_client_language(self):
        html = self._render(
            'event_cancelled',
            {
                'event_title': 'Host Lifecycle Workshop',
                'event_datetime': 'Saturday, March 28, 2026, 18:00 UTC',
            },
        )
        self.assertIn('includes a calendar cancellation update', html)
        self.assertIn('Supported calendar apps can use it', html)
        self.assertIn('if prompted', html.lower())
        self.assert_calendar_copy_is_qualified(html)


@tag('core')
class HostCalendarLifecycleTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(email='calendar-host@test.com')
        self.event = _event(self.host)
        self.registration = EventRegistration.objects.create(
            event=self.event, user=self.host,
        )

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-update')
    def test_reschedule_uses_attendee_audience_and_deduplicates(self, mock_send):
        self.event.ics_sequence = 2
        self.event.save(update_fields=['ics_sequence'])
        first = send_reschedule_notice_one(
            self.event.pk,
            self.host.pk,
            (self.event.start_datetime - timedelta(days=1)).isoformat(),
        )
        second = send_reschedule_notice_one(
            self.event.pk,
            self.host.pk,
            (self.event.start_datetime - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(second['status'], 'deduplicated')
        self.assertEqual(mock_send.call_count, 1)
        vevent = _vevent(mock_send.call_args.kwargs['ics_content'])
        join_url = f'{site_base_url()}{self.event.get_join_url()}'
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertNotIn(self.event.zoom_join_url, mock_send.call_args.kwargs['html_body'])
        self.assertEqual(
            EmailLog.objects.get(email_type='event_rescheduled').event,
            self.event,
        )

    @patch('events.tasks.notify_reschedule._send_raw_email')
    def test_new_host_initial_invite_at_current_sequence_wins_race(self, mock_send):
        self.event.ics_sequence = 4
        self.event.save(update_fields=['ics_sequence'])
        HostInviteDelivery.objects.create(
            event=self.event,
            user=self.host,
            access_version=self.event.host_access_version,
            status=HostInviteDelivery.STATUS_SENT,
            attempt_count=1,
            sent_at=timezone.now(),
            sent_ics_sequence=4,
        )
        result = send_reschedule_notice_one(
            self.event.pk,
            self.host.pk,
            (self.event.start_datetime - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(result['status'], 'deduplicated')
        self.assertEqual(
            result['reason'], 'host_initial_invite_has_current_sequence',
        )
        mock_send.assert_not_called()

    @patch(
        'events.tasks.notify_cancellation._send_raw_email',
        return_value='ses-cancel',
    )
    def test_cancel_uses_attendee_audience_and_deduplicates(self, mock_send):
        self.event.status = 'cancelled'
        self.event.ics_sequence = 3
        self.event.save(update_fields=['status', 'ics_sequence'])
        first = send_cancellation_notice_one(self.event.pk, self.host.pk)
        second = send_cancellation_notice_one(self.event.pk, self.host.pk)
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(second['status'], 'deduplicated')
        self.assertEqual(mock_send.call_count, 1)
        vevent = _vevent(mock_send.call_args.kwargs['ics_content'])
        join_url = f'{site_base_url()}{self.event.get_join_url()}'
        self.assertEqual(str(vevent.get('url')), join_url)
        self.assertEqual(str(vevent.get('location')), join_url)
        self.assertEqual(str(vevent.get('status')), 'CANCELLED')
        self.assertEqual(int(vevent.get('sequence')), 3)
