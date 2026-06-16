"""Tests for auto-registering event hosts as attendees (#1002)."""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import EmailAlias, Token
from accounts.services.email_resolution import normalize_email
from email_app.models import EmailLog
from events.models import Event, EventRegistration
from events.services.host_registration import maybe_register_host_as_attendee
from tests.fixtures import StaffUserMixin

User = get_user_model()

HOST_INVITE_SEND_PATH = 'events.services.host_invite._send_raw_email'


def _future_start():
    return timezone.now() + timedelta(days=7)


def _make_event(**kwargs):
    start = kwargs.pop('start_datetime', _future_start())
    defaults = {
        'title': 'Host Auto Registration',
        'slug': 'host-auto-registration',
        'start_datetime': start,
        'end_datetime': start + timedelta(hours=1),
        'status': 'upcoming',
        'host_email': 'host@test.com',
    }
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


@override_settings(EVENTS_HOST_INVITE_EMAIL='')
@tag('core')
class HostAutoRegistrationServiceTest(TestCase):
    def setUp(self):
        self.host = User.objects.create_user(
            email='host@test.com',
            password='pw',
        )

    def test_registers_resolved_host_user(self):
        event = _make_event()

        registration = maybe_register_host_as_attendee(event)

        self.assertIsNotNone(registration)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).exists(),
        )

    def test_alias_resolves_to_canonical_user(self):
        alias_user = User.objects.create_user(
            email='canonical@test.com',
            password='pw',
        )
        EmailAlias.objects.create(
            user=alias_user,
            email=normalize_email('old@test.com'),
        )
        event = _make_event(host_email='old@test.com')

        maybe_register_host_as_attendee(event)

        self.assertTrue(
            EventRegistration.objects.filter(
                event=event,
                user=alias_user,
            ).exists(),
        )
        self.assertFalse(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).exists(),
        )

    def test_non_user_host_email_skips_and_logs_warning(self):
        event = _make_event(host_email='nobody@test.com')

        with self.assertLogs(
            'events.services.host_registration',
            level='WARNING',
        ) as logs:
            result = maybe_register_host_as_attendee(event)

        self.assertIsNone(result)
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assertTrue(
            any('did not resolve to a platform user' in line
                for line in logs.output),
            logs.output,
        )

    def test_idempotent_on_resave(self):
        event = _make_event()

        first = maybe_register_host_as_attendee(event)
        second = maybe_register_host_as_attendee(event)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).count(),
            1,
        )

    def test_self_registered_host_is_not_duplicated(self):
        event = _make_event()
        existing = EventRegistration.objects.create(
            event=event,
            user=self.host,
        )

        registration = maybe_register_host_as_attendee(event)

        self.assertEqual(registration.pk, existing.pk)
        self.assertEqual(EventRegistration.objects.filter(event=event).count(), 1)

    def test_draft_event_skips(self):
        event = _make_event(status='draft')

        maybe_register_host_as_attendee(event)

        self.assertFalse(EventRegistration.objects.filter(event=event).exists())

    def test_past_event_skips(self):
        start = timezone.now() - timedelta(days=2)
        event = _make_event(
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='completed',
        )

        maybe_register_host_as_attendee(event)

        self.assertFalse(EventRegistration.objects.filter(event=event).exists())

    def test_cancelled_event_skips(self):
        event = _make_event(status='cancelled')

        maybe_register_host_as_attendee(event)

        self.assertFalse(EventRegistration.objects.filter(event=event).exists())

    def test_host_change_registers_new_host_and_keeps_old_host(self):
        event = _make_event()
        maybe_register_host_as_attendee(event)
        new_host = User.objects.create_user(
            email='new-host@test.com',
            password='pw',
        )

        event.host_email = 'new-host@test.com'
        event.save(update_fields=['host_email'])
        maybe_register_host_as_attendee(event)

        self.assertEqual(
            set(
                EventRegistration.objects.filter(event=event).values_list(
                    'user__email',
                    flat=True,
                )
            ),
            {self.host.email, new_host.email},
        )

    def test_best_effort_exception_is_logged_and_swallowed(self):
        event = _make_event()

        with patch(
            'events.services.host_registration.resolve_user_by_email',
            side_effect=RuntimeError('resolver down'),
        ), self.assertLogs(
            'events.services.host_registration',
            level='ERROR',
        ) as logs:
            result = maybe_register_host_as_attendee(event)

        self.assertIsNone(result)
        self.assertFalse(EventRegistration.objects.filter(event=event).exists())
        self.assertTrue(
            any('Failed to auto-register host' in line for line in logs.output),
            logs.output,
        )

    def test_no_registration_confirmation_email_or_second_ics(self):
        event = _make_event()

        with patch(
            'events.services.registration_email.send_registration_confirmation',
        ) as mock_confirmation, patch(
            'events.services.registration_email.generate_ics',
        ) as mock_registration_ics:
            maybe_register_host_as_attendee(event)

        mock_confirmation.assert_not_called()
        mock_registration_ics.assert_not_called()
        self.assertEqual(
            EmailLog.objects.filter(email_type='event_registration').count(),
            0,
        )


@override_settings(EVENTS_HOST_INVITE_EMAIL='')
@tag('core')
class HostAutoRegistrationStudioTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)
        self.host = User.objects.create_user(
            email='studio-host@test.com',
            password='pw',
        )

    def _create_payload(self, **overrides):
        start = _future_start()
        payload = {
            'title': 'Studio Host Auto Registration',
            'slug': '',
            'event_date': start.strftime('%d/%m/%Y'),
            'event_time': start.strftime('%H:%M'),
            'duration_hours': '1',
            'timezone': 'UTC',
            'status': 'upcoming',
            'host_email': self.host.email,
        }
        payload.update(overrides)
        return payload

    def test_studio_create_auto_registers_host(self):
        with patch(HOST_INVITE_SEND_PATH, return_value='ses-host'):
            response = self.client.post(
                '/studio/events/new',
                self._create_payload(),
            )

        event = Event.objects.get(title='Studio Host Auto Registration')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).exists(),
        )

    def test_studio_edit_auto_registers_host(self):
        event = _make_event(
            title='Studio Edit Host Auto Registration',
            slug='studio-edit-host-auto-registration',
            host_email='',
        )

        with patch(HOST_INVITE_SEND_PATH, return_value='ses-host'):
            response = self.client.post(
                f'/studio/events/{event.pk}/edit',
                self._create_payload(
                    title=event.title,
                    slug=event.slug,
                    host_email=self.host.email,
                ),
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).exists(),
        )


@override_settings(EVENTS_HOST_INVITE_EMAIL='')
@tag('core')
class HostAutoRegistrationApiTest(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='api-staff@test.com',
            password='pw',
            is_staff=True,
        )
        self.token = Token.objects.create(user=self.staff, name='events')
        self.host = User.objects.create_user(
            email='api-host@test.com',
            password='pw',
        )

    def _auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    def _create_payload(self, **overrides):
        start = _future_start()
        payload = {
            'title': 'API Host Auto Registration',
            'platform': 'zoom',
            'start_datetime': start.isoformat(),
            'end_datetime': (start + timedelta(hours=1)).isoformat(),
            'status': 'upcoming',
            'published': True,
            'host_email': self.host.email,
        }
        payload.update(overrides)
        return payload

    def test_api_create_auto_registers_host(self):
        with patch(HOST_INVITE_SEND_PATH, return_value='ses-host'), patch(
            'events.services.registration_email.send_registration_confirmation',
        ) as mock_confirmation:
            response = self.client.post(
                '/api/events',
                data=json.dumps(self._create_payload()),
                content_type='application/json',
                **self._auth(),
            )

        self.assertEqual(response.status_code, 201)
        event = Event.objects.get(slug=response.json()['slug'])
        self.assertTrue(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).exists(),
        )
        mock_confirmation.assert_not_called()

    def test_api_update_auto_registers_host(self):
        start = _future_start()
        event = Event.objects.create(
            title='API Update Host Auto Registration',
            slug='api-update-host-auto-registration',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='draft',
            published=False,
            origin='studio',
            host_email='',
        )

        with patch(HOST_INVITE_SEND_PATH, return_value='ses-host'):
            response = self.client.patch(
                f'/api/events/{event.slug}',
                data=json.dumps({
                    'status': 'upcoming',
                    'published': True,
                    'host_email': self.host.email,
                }),
                content_type='application/json',
                **self._auth(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            EventRegistration.objects.filter(
                event=event,
                user=self.host,
            ).exists(),
        )
