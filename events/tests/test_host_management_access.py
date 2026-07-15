"""Object-scoped host management and delivery lifecycle tests (#861)."""

from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.utils import timezone
from icalendar import Calendar

from email_app.models import EmailLog
from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration, HostInviteDelivery
from events.services.cancel_token import generate_cancel_token
from events.services.host_access import (
    HostAccessError,
    HostAccessExpired,
    build_host_access_url,
    generate_host_access_token,
    validate_host_access_token,
)
from events.services.host_registration import maybe_register_host_as_attendee
from events.tasks.notify_cancellation import send_cancellation_notice_one
from events.tasks.notify_reschedule import send_reschedule_notice_one

User = get_user_model()


def _event(host):
    start = timezone.now() + timedelta(days=7)
    return Event.objects.create(
        title='Scoped Host Event',
        slug='scoped-host-event',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status='upcoming',
        platform='zoom',
        host_email=host.email,
    )


def _token_from_url(url):
    return parse_qs(urlparse(url).query)['token'][0]


def _vevent(raw):
    calendar = Calendar.from_ical(raw)
    return [item for item in calendar.walk() if item.name == 'VEVENT'][0]


@tag('core')
class HostAccessTokenTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(email='host861@test.com', password='pw')
        self.other = User.objects.create_user(email='other861@test.com', password='pw')
        self.staff = User.objects.create_user(
            email='staff861@test.com', password='pw', is_staff=True,
        )
        self.event = _event(self.host)
        self.token = generate_host_access_token(self.event, self.host)
        self.url = f'/events/{self.event.pk}/host/manage?token={self.token}'

    def test_designated_normal_host_can_open_scoped_landing(self):
        self.client.login(email=self.host.email, password='pw')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Host controls')

    def test_anonymous_is_sent_to_login_without_granting_access(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_forwarded_token_denies_other_user_and_non_host_staff(self):
        for user in (self.other, self.staff):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 403)
                self.assertTemplateUsed(
                    response, 'events/host_management_denied.html',
                )
                self.assertContains(
                    response, 'Sign in with the designated host account',
                    status_code=403,
                )
                self.assertContains(response, 'Switch account', status_code=403)
                self.assertContains(response, 'Back to Events', status_code=403)
                self.assertNotContains(response, self.token, status_code=403)
                self.client.logout()

    def test_expired_token_shows_safe_recovery_without_reflecting_token(self):
        self.client.force_login(self.host)
        future = timezone.now() + timedelta(days=40)
        with patch('events.services.host_access.timezone.now', return_value=future):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'events/host_management_denied.html')
        self.assertContains(response, 'This host link has expired', status_code=403)
        self.assertContains(response, 'Contact the event operator', status_code=403)
        self.assertContains(response, 'Back to Events', status_code=403)
        self.assertNotContains(response, self.token, status_code=403)

    def test_reassigned_host_shows_stale_link_recovery(self):
        self.client.force_login(self.host)
        self.event.host_email = self.other.email
        self.event.save(update_fields=['host_email'])

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, 'events/host_management_denied.html')
        self.assertContains(
            response, 'This host link is no longer current', status_code=403,
        )
        self.assertContains(response, 'reassigned', status_code=403)
        self.assertContains(response, 'Contact the event operator', status_code=403)
        self.assertContains(response, 'Back to Events', status_code=403)
        self.assertNotContains(response, self.token, status_code=403)

    def test_token_is_event_scoped_and_tamper_evident(self):
        second = Event.objects.create(
            title='Other', slug='other-host-event',
            start_datetime=timezone.now() + timedelta(days=8),
            status='upcoming', host_email=self.host.email,
        )
        with self.assertRaises(HostAccessError):
            validate_host_access_token(second, self.token)
        with self.assertRaises(HostAccessError):
            validate_host_access_token(self.event, f'{self.token}x')

    def test_token_expires_after_bounded_event_aware_lifetime(self):
        future = timezone.now() + timedelta(days=40)
        with patch('events.services.host_access.timezone.now', return_value=future):
            with self.assertRaises(HostAccessExpired):
                validate_host_access_token(self.event, self.token)

    def test_host_change_rotates_version_and_revokes_old_token(self):
        old_version = self.event.host_access_version
        self.event.host_email = self.other.email
        self.event.save(update_fields=['host_email'])
        self.assertNotEqual(self.event.host_access_version, old_version)
        with self.assertRaises(HostAccessError):
            validate_host_access_token(self.event, self.token)
        new_token = generate_host_access_token(self.event, self.other)
        self.assertEqual(
            validate_host_access_token(self.event, new_token), self.other,
        )

    def test_email_link_targets_safe_get_landing_not_post_action(self):
        url = build_host_access_url(self.event, self.host, anchor='zoom')
        self.assertIn(f'/events/{self.event.pk}/host/manage?', url)
        self.assertNotIn('/create-zoom', url)
        self.assertEqual(
            validate_host_access_token(self.event, _token_from_url(url)),
            self.host,
        )


@tag('core')
class HostManagementActionTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(email='actions861@test.com', password='pw')
        self.event = _event(self.host)
        self.token = generate_host_access_token(self.event, self.host)
        self.client.login(email=self.host.email, password='pw')

    @patch('events.views.host_management.create_meeting')
    def test_zoom_creation_is_explicit_post(self, mock_create):
        mock_create.return_value = {
            'meeting_id': '861', 'join_url': 'https://zoom.us/j/861',
        }
        get_response = self.client.get(
            f'/events/{self.event.pk}/host/create-zoom?token={self.token}',
        )
        self.assertEqual(get_response.status_code, 405)
        mock_create.assert_not_called()

        response = self.client.post(
            f'/events/{self.event.pk}/host/create-zoom',
            {'token': self.token},
        )
        self.assertEqual(response.status_code, 302)
        mock_create.assert_called_once_with(self.event)
        self.event.refresh_from_db()
        self.assertEqual(self.event.zoom_meeting_id, '861')

    def test_post_action_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(email=self.host.email, password='pw')
        response = csrf_client.post(
            f'/events/{self.event.pk}/host/update',
            {'token': self.token},
        )
        self.assertEqual(response.status_code, 403)

    def test_limited_edit_does_not_expose_host_assignment(self):
        response = self.client.get(
            f'/events/{self.event.pk}/host/manage?token={self.token}',
        )
        form = response.context['form']
        self.assertNotIn('host_email', form.fields)
        self.assertNotIn('published', form.fields)

    def test_current_host_cannot_silently_cancel_lifecycle_registration(self):
        registration = EventRegistration.objects.create(
            event=self.event, user=self.host,
        )
        cancel_token = generate_cancel_token(registration)
        response = self.client.post(
            f'/api/events/{self.event.slug}/cancel-registration?token={cancel_token}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cannot be cancelled')
        self.assertTrue(
            EventRegistration.objects.filter(pk=registration.pk).exists(),
        )


@tag('core')
class HostDeliveryRecoveryTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(email='delivery861@test.com')
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
        self.assertEqual(
            delivery.last_error,
            HostInviteDelivery.ERROR_PROVIDER,
        )
        self.assertEqual(mock_send.call_count, HostInviteDelivery.MAX_ATTEMPTS)

    @patch('events.services.registration_email._send_raw_email')
    def test_provider_exception_detail_is_never_persisted_or_rendered(
        self, mock_send,
    ):
        private_detail = (
            'secret=host-provider-key '
            'https://provider.test/request?token=private-payload'
        )
        mock_send.side_effect = RuntimeError(private_detail)
        with self.assertLogs(
            'events.services.host_registration',
            level='ERROR',
        ) as logs:
            maybe_register_host_as_attendee(self.event)
        self.assertNotIn(private_detail, '\n'.join(logs.output))
        self.assertIn(
            HostInviteDelivery.ERROR_PROVIDER,
            '\n'.join(logs.output),
        )

        delivery = HostInviteDelivery.objects.get(
            event=self.event,
            user=self.host,
            access_version=self.event.host_access_version,
        )
        self.assertEqual(
            delivery.last_error,
            HostInviteDelivery.ERROR_PROVIDER,
        )
        self.assertNotIn(private_detail, delivery.last_error)

        staff = User.objects.create_user(
            email='delivery-staff861@test.com',
            password='pw',
            is_staff=True,
        )
        self.client.login(email=staff.email, password='pw')
        response = self.client.get(f'/studio/events/{self.event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, private_detail)
        self.assertContains(response, 'Delivery failed; review application logs.')

        # Legacy rows may already contain raw details. Studio must collapse
        # every unknown value to the same safe operator-facing diagnostic.
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
        self.assertNotIn(
            'cancel-registration', mock_send.call_args.kwargs['html_body'],
        )
        self.assertEqual(
            EventRegistration.objects.filter(event=self.event, user=self.host).count(),
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
    def test_reassignment_back_to_former_host_sends_fresh_rotated_link(
        self, mock_send,
    ):
        other = User.objects.create_user(email='replacement861@test.com')
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
                event=self.event, user=self.host,
                status=HostInviteDelivery.STATUS_SENT,
            ).count(),
            2,
        )


@tag('core')
class HostLifecycleCopyRenderingTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            email='copy-host861@test.com',
            first_name='Ada',
        )

    def _render(self, template_name, context):
        _subject, body_html = EmailService()._render_template(
            template_name,
            self.host,
            context,
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

    def test_host_registration_renders_prompt_aware_calendar_invitation(self):
        html = self._render(
            'event_registration',
            {
                'event_title': 'Host Lifecycle Workshop',
                'event_datetime': 'Saturday, March 21, 2026, 18:00 UTC',
                'timezone_help': '',
                'join_url': 'https://aishippinglabs.com/events/1/workshop/join',
                'cancel_url': 'https://aishippinglabs.com/cancel',
                'google_calendar_url': 'https://calendar.google.com/',
                'outlook_calendar_url': 'https://outlook.live.com/',
                'office365_calendar_url': 'https://outlook.office.com/',
                'is_host_registration': True,
                'edit_url': 'https://aishippinglabs.com/host/edit',
                'manage_url': 'https://aishippinglabs.com/host/manage',
                'create_zoom_url': 'https://aishippinglabs.com/host/zoom',
                'studio_url': 'https://aishippinglabs.com/host/studio',
                'zoom_join_url': '',
            },
        )
        self.assertIn('includes a calendar invitation for this event', html)
        self.assertIn('if prompted', html)
        self.assertIn('Host management links', html)
        self.assertIn('Open host controls', html)
        self.assertIn('https://aishippinglabs.com/host/studio', html)
        self.assertNotIn('Open the event in Studio', html)
        self.assert_calendar_copy_is_qualified(html)

    def test_host_reschedule_renders_supported_client_update_language(self):
        html = self._render(
            'event_rescheduled',
            {
                'event_title': 'Host Lifecycle Workshop',
                'old_event_datetime': 'Saturday, March 21, 2026, 18:00 UTC',
                'new_event_datetime': 'Saturday, March 28, 2026, 18:00 UTC',
                'timezone_help': '',
                'join_url': 'https://aishippinglabs.com/events/1/workshop/join',
                'cancel_url': 'https://aishippinglabs.com/cancel',
                'is_host_registration': True,
                'edit_url': 'https://aishippinglabs.com/host/edit',
                'manage_url': 'https://aishippinglabs.com/host/manage',
                'create_zoom_url': 'https://aishippinglabs.com/host/zoom',
                'studio_url': 'https://aishippinglabs.com/host/studio',
                'zoom_join_url': '',
            },
        )
        self.assertIn('includes an updated calendar invitation', html)
        self.assertIn('supported calendar apps can apply', html)
        self.assertIn('if prompted', html.lower())
        self.assertIn('Host management links', html)
        self.assertIn('Open host controls', html)
        self.assertIn('https://aishippinglabs.com/host/studio', html)
        self.assertNotIn('Open the event in Studio', html)
        self.assert_calendar_copy_is_qualified(html)

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
        self.host = User.objects.create_user(email='calendar-host861@test.com')
        self.event = _event(self.host)
        self.registration = EventRegistration.objects.create(
            event=self.event, user=self.host,
        )

    @patch('events.tasks.notify_reschedule._send_raw_email', return_value='ses-update')
    def test_reschedule_preserves_host_audience_and_deduplicates(self, mock_send):
        self.event.ics_sequence = 2
        self.event.save(update_fields=['ics_sequence'])
        first = send_reschedule_notice_one(
            self.event.pk, self.host.pk,
            (self.event.start_datetime - timedelta(days=1)).isoformat(),
        )
        second = send_reschedule_notice_one(
            self.event.pk, self.host.pk,
            (self.event.start_datetime - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(second['status'], 'deduplicated')
        self.assertEqual(mock_send.call_count, 1)
        vevent = _vevent(mock_send.call_args.kwargs['ics_content'])
        detail_url = f'https://aishippinglabs.com{self.event.get_absolute_url()}'
        self.assertEqual(str(vevent.get('url')), detail_url)
        self.assertEqual(str(vevent.get('location')), detail_url)
        log = EmailLog.objects.get(email_type='event_rescheduled')
        self.assertEqual(log.event, self.event)

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
            self.event.pk, self.host.pk,
            (self.event.start_datetime - timedelta(days=1)).isoformat(),
        )
        self.assertEqual(result['status'], 'deduplicated')
        self.assertEqual(
            result['reason'], 'host_initial_invite_has_current_sequence',
        )
        mock_send.assert_not_called()

    @patch('events.tasks.notify_cancellation._send_raw_email', return_value='ses-cancel')
    def test_cancel_preserves_host_audience_and_deduplicates(self, mock_send):
        self.event.status = 'cancelled'
        self.event.ics_sequence = 3
        self.event.save(update_fields=['status', 'ics_sequence'])
        first = send_cancellation_notice_one(self.event.pk, self.host.pk)
        second = send_cancellation_notice_one(self.event.pk, self.host.pk)
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(second['status'], 'deduplicated')
        self.assertEqual(mock_send.call_count, 1)
        vevent = _vevent(mock_send.call_args.kwargs['ics_content'])
        detail_url = f'https://aishippinglabs.com{self.event.get_absolute_url()}'
        self.assertEqual(str(vevent.get('url')), detail_url)
        self.assertEqual(str(vevent.get('location')), detail_url)
        self.assertEqual(str(vevent.get('status')), 'CANCELLED')
