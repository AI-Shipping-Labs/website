"""Relocated staff-token Call profile history-protection API owner (#1479)."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import Token
from community.models import STATUS_CANCELED, BookedCall, CallHost

User = get_user_model()

BOOKING_URL = 'https://calendar.app.google/jordan-profile'


class CallProfileHistoryProtectionApiTest(TestCase):
    """Owns DELETE-protects-history then PATCH-hide for in-use Call profiles.

    Relocated from Playwright
    ``TestCallProfileApiJourney.test_api_delete_protects_history_then_allows_hiding``.
    """

    def test_api_delete_protects_history_then_allows_hiding(self):
        staff = User.objects.create_user(
            email='staff-api-history-1404@test.com',
            password='pw',
            is_staff=True,
        )
        _token, plaintext = Token.create_for_user(
            user=staff,
            name='pw-call-profile-history',
        )
        profile = CallHost.objects.create(
            name='Jordan Lee',
            slug='jordan',
            role_label='AI product coach',
            booking_url=BOOKING_URL,
            is_active=True,
            order=5,
        )
        BookedCall.objects.create(
            host=profile,
            invitee_email='api-history@example.com',
            status=STATUS_CANCELED,
            calendly_event_uri='https://api.calendly.com/scheduled_events/api-history',
        )
        auth = {'HTTP_AUTHORIZATION': f'Token {plaintext}'}

        deleted = self.client.delete('/api/call-profiles/jordan', **auth)
        self.assertEqual(deleted.status_code, 409)
        self.assertEqual(deleted.json(), {
            'error': (
                "This call profile has booked-call history and can't be deleted. "
                'Hide it with PATCH is_active=false instead.'
            ),
            'code': 'call_profile_in_use',
        })

        hidden = self.client.patch(
            '/api/call-profiles/jordan',
            data=json.dumps({'is_active': False}),
            content_type='application/json',
            **auth,
        )
        body = hidden.json()
        self.assertFalse(body['is_active'])
        self.assertTrue(
            CallHost.objects.filter(pk=profile.pk, is_active=False).exists(),
        )
        self.assertTrue(BookedCall.objects.filter(host_id=profile.pk).exists())
