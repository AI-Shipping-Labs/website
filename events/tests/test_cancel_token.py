"""Unit tests for ``events.services.cancel_token`` (issue #588)."""

import datetime
from datetime import timedelta

import jwt
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from events.models import Event, EventRegistration
from events.services.cancel_token import (
    CANCEL_ACTION,
    JWT_ALGORITHM,
    CancelTokenExpired,
    CancelTokenInvalid,
    decode_cancel_token,
    generate_cancel_token,
)


class CancelTokenRoundTripTest(TestCase):
    """The token encodes the right claims and decodes back to them."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='cancel-token@example.com',
            password='secret1234',
        )
        cls.event = Event.objects.create(
            slug='token-event',
            title='Token Event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
        )
        cls.registration = EventRegistration.objects.create(
            event=cls.event, user=cls.user,
        )

    def test_token_payload_contains_all_required_claims(self):
        token = generate_cancel_token(self.registration)
        payload = decode_cancel_token(token)

        self.assertEqual(payload['registration_id'], self.registration.pk)
        self.assertEqual(payload['event_id'], self.event.pk)
        self.assertEqual(payload['user_id'], self.user.pk)
        self.assertEqual(payload['action'], CANCEL_ACTION)
        self.assertIn('exp', payload)

    def test_decode_round_trips_a_freshly_generated_token(self):
        token = generate_cancel_token(self.registration)
        payload = decode_cancel_token(token)

        self.assertEqual(payload['registration_id'], self.registration.pk)


class CancelTokenExpiryTest(TestCase):
    """Expired tokens raise the typed expired exception."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='expiry@example.com', password='secret1234',
        )
        cls.event = Event.objects.create(
            slug='expiry-event',
            title='Expiry Event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
        )
        cls.registration = EventRegistration.objects.create(
            event=cls.event, user=cls.user,
        )

    def test_expired_token_raises_cancel_token_expired(self):
        payload = {
            'registration_id': self.registration.pk,
            'event_id': self.event.pk,
            'user_id': self.user.pk,
            'action': CANCEL_ACTION,
            'exp': datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=1),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)

        with self.assertRaises(CancelTokenExpired):
            decode_cancel_token(token)


class CancelTokenWrongSecretTest(TestCase):
    """A token signed with a different secret is rejected as invalid."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='wrong-key@example.com', password='secret1234',
        )
        cls.event = Event.objects.create(
            slug='wrong-key-event',
            title='Wrong Key Event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
        )
        cls.registration = EventRegistration.objects.create(
            event=cls.event, user=cls.user,
        )

    def test_wrong_secret_raises_cancel_token_invalid(self):
        payload = {
            'registration_id': self.registration.pk,
            'event_id': self.event.pk,
            'user_id': self.user.pk,
            'action': CANCEL_ACTION,
            'exp': datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=30),
        }
        token = jwt.encode(payload, 'a-completely-different-key', algorithm=JWT_ALGORITHM)

        with self.assertRaises(CancelTokenInvalid):
            decode_cancel_token(token)


class CancelTokenWrongActionTest(TestCase):
    """A token whose action claim is not the cancel action is rejected.

    Defense against cross-token reuse: an unsubscribe token (action
    ``unsubscribe``) submitted to the cancel endpoint must not succeed
    even though it is signed with the same key.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='wrong-action@example.com', password='secret1234',
        )

    def test_wrong_action_raises_cancel_token_invalid(self):
        payload = {
            'registration_id': 1,
            'event_id': 1,
            'user_id': self.user.pk,
            'action': 'unsubscribe',
            'exp': datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=30),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)

        with self.assertRaises(CancelTokenInvalid):
            decode_cancel_token(token)


class CancelTokenTamperedTest(TestCase):
    """A token whose last character is flipped is rejected as invalid."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='tampered@example.com', password='secret1234',
        )
        cls.event = Event.objects.create(
            slug='tampered-event',
            title='Tampered Event',
            start_datetime=timezone.now() + timedelta(days=2),
            status='upcoming',
        )
        cls.registration = EventRegistration.objects.create(
            event=cls.event, user=cls.user,
        )

    def test_tampered_token_raises_cancel_token_invalid(self):
        token = generate_cancel_token(self.registration)
        header, payload, signature = token.split('.')
        first = signature[0]
        replacement = 'A' if first != 'A' else 'B'
        tampered = f'{header}.{payload}.{replacement}{signature[1:]}'

        with self.assertRaises(CancelTokenInvalid):
            decode_cancel_token(tampered)
