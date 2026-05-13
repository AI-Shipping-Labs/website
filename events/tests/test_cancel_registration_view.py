"""View tests for the email-cancel registration flow (issue #588).

Covers the GET confirm page (`/events/<slug>/cancel-registration`) and
the POST action (`/api/events/<slug>/cancel-registration`). The token
is the authorization, so all of the requests below run anonymously.
"""

import datetime
from datetime import timedelta

import jwt
from django.conf import settings
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventRegistration
from events.services.cancel_token import (
    CANCEL_ACTION,
    JWT_ALGORITHM,
    generate_cancel_token,
)


def _make_event(slug='community-lunch', status='upcoming'):
    return Event.objects.create(
        slug=slug,
        title=slug.replace('-', ' ').title(),
        start_datetime=timezone.now() + timedelta(days=3),
        end_datetime=timezone.now() + timedelta(days=3, hours=1),
        status=status,
    )


def _make_registration(email='cancel-view@example.com', event=None):
    user = User.objects.create_user(email=email, password='secret1234')
    if event is None:
        event = _make_event()
    return EventRegistration.objects.create(event=event, user=user)


def _expired_token(registration):
    payload = {
        'registration_id': registration.pk,
        'event_id': registration.event_id,
        'user_id': registration.user_id,
        'action': CANCEL_ACTION,
        'exp': datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=1),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _tampered_token(registration):
    token = generate_cancel_token(registration)
    header, payload, signature = token.split('.')
    first = signature[0]
    replacement = 'A' if first != 'A' else 'B'
    return f'{header}.{payload}.{replacement}{signature[1:]}'


class CancelRegistrationGetMissingTokenTest(TestCase):
    """GET with no token renders the incomplete-link state."""

    def test_no_token_renders_invalid_state(self):
        _make_event(slug='no-token-event')
        client = Client()
        response = client.get('/events/no-token-event/cancel-registration')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'incomplete')
        self.assertNotContains(response, 'Cancel my registration')


class CancelRegistrationGetValidTokenTest(TestCase):
    """GET with a valid token renders the form and does NOT touch the DB."""

    @classmethod
    def setUpTestData(cls):
        cls.registration = _make_registration()

    def test_valid_token_renders_form_without_deleting(self):
        token = generate_cancel_token(self.registration)
        client = Client()
        response = client.get(
            f'/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Community Lunch')
        self.assertContains(response, 'Cancel my registration')
        self.assertContains(
            response,
            f'action="/api/events/community-lunch/cancel-registration?token={token}"',
        )
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


class CancelRegistrationPostValidTokenTest(TestCase):
    """POST with a valid token deletes the row and renders success."""

    def setUp(self):
        self.registration = _make_registration()

    def test_valid_post_deletes_and_renders_success(self):
        token = generate_cancel_token(self.registration)
        client = Client()
        response = client.post(
            f'/api/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'has been cancelled')
        self.assertContains(response, 'Community Lunch')
        self.assertFalse(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


class CancelRegistrationExpiredTokenTest(TestCase):
    """Expired token rejects with the expired message and preserves the row."""

    def setUp(self):
        self.registration = _make_registration(
            email='expired-view@example.com',
        )

    def test_expired_get_returns_expired_message(self):
        token = _expired_token(self.registration)
        client = Client()
        response = client.get(
            f'/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'expired')
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )

    def test_expired_post_does_not_delete(self):
        token = _expired_token(self.registration)
        client = Client()
        response = client.post(
            f'/api/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'expired')
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


class CancelRegistrationTamperedTokenTest(TestCase):
    """Tampered token rejects with the invalid message and preserves the row."""

    def setUp(self):
        self.registration = _make_registration(
            email='tampered-view@example.com',
        )

    def test_tampered_get_returns_invalid_message(self):
        token = _tampered_token(self.registration)
        client = Client()
        response = client.get(
            f'/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid')
        self.assertNotContains(response, 'Community Lunch')
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )

    def test_tampered_post_does_not_delete(self):
        token = _tampered_token(self.registration)
        client = Client()
        response = client.post(
            f'/api/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid')
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


class CancelRegistrationEventIdMismatchTest(TestCase):
    """A token whose event_id does not match the URL slug is invalid."""

    def setUp(self):
        self.registration = _make_registration(
            email='mismatch@example.com', event=_make_event(slug='event-a'),
        )
        self.other_event = _make_event(slug='event-b')

    def test_mismatched_event_slug_is_invalid(self):
        token = generate_cancel_token(self.registration)
        client = Client()
        response = client.post(
            f'/api/events/event-b/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'invalid')
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


class CancelRegistrationAlreadyCancelledTest(TestCase):
    """Token whose registration row no longer exists renders no-op message."""

    def setUp(self):
        self.registration = _make_registration(
            email='gone@example.com',
        )

    def test_missing_registration_row_renders_already_cancelled(self):
        token = generate_cancel_token(self.registration)
        self.registration.delete()

        client = Client()
        response = client.post(
            f'/api/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not registered')


class CancelRegistrationCompletedEventTest(TestCase):
    """Cancelling a completed event preserves the row."""

    def setUp(self):
        self.event = _make_event(slug='last-weeks-talk', status='upcoming')
        self.user = User.objects.create_user(
            email='completed@example.com', password='secret1234',
        )
        self.registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )
        self.token = generate_cancel_token(self.registration)

    def test_completed_event_post_preserves_row(self):
        self.event.status = 'completed'
        self.event.save()

        client = Client()
        response = client.post(
            f'/api/events/last-weeks-talk/cancel-registration?token={self.token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already started or finished')
        self.assertTrue(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )

    def test_completed_event_get_renders_finished_state(self):
        self.event.status = 'completed'
        self.event.save()

        client = Client()
        response = client.get(
            f'/events/last-weeks-talk/cancel-registration?token={self.token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already started or finished')
        self.assertNotContains(response, 'Cancel my registration')


class CancelRegistrationCsrfNotRequiredTest(TestCase):
    """POST without a CSRF token still succeeds — the URL token IS auth."""

    def setUp(self):
        self.registration = _make_registration(
            email='nocsrf@example.com',
        )

    def test_post_without_csrf_token_succeeds(self):
        token = generate_cancel_token(self.registration)
        client = Client(enforce_csrf_checks=True)
        response = client.post(
            f'/api/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'has been cancelled')
        self.assertFalse(
            EventRegistration.objects.filter(pk=self.registration.pk).exists(),
        )


class CancelRegistrationAnonymousTest(TestCase):
    """Anonymous request bearing a valid token can cancel."""

    def setUp(self):
        self.registration = _make_registration(
            email='anon@example.com',
        )

    def test_anonymous_get_does_not_redirect_to_login(self):
        token = generate_cancel_token(self.registration)
        client = Client()
        response = client.get(
            f'/events/community-lunch/cancel-registration?token={token}',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('/accounts/login/', response.get('Location', ''))
        self.assertContains(response, 'Cancel my registration')
