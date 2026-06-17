"""Tests for the staff host profile API (#1031)."""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventHost, Host

User = get_user_model()


@tag('core')
class HostProfilesApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-hosts-api@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member-hosts-api@test.com',
            password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='hosts-api')
        cls.non_staff_token = Token(
            key='non-staff-hosts-api-token',
            user=cls.member,
            name='legacy-member-token',
        )
        Token.objects.bulk_create([cls.non_staff_token])

        cls.alexey = Host.objects.get(slug='alexey-grigorev')
        cls.valeriia = Host.objects.get(slug='valeriia-kuka')
        start = timezone.now() + timedelta(days=7)
        cls.event = Event.objects.create(
            title='Hosted API Profile Event',
            slug='hosted-api-profile-event',
            description='Event with API-managed host title.',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            origin='studio',
            published=True,
        )
        EventHost.objects.create(event=cls.event, host=cls.alexey, position=0)

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _patch(self, slug, payload, *, token=None):
        return self.client.patch(
            f'/api/hosts/{slug}',
            data=json.dumps(payload),
            content_type='application/json',
            **self._auth(token),
        )

    def test_list_returns_profiles_ordered_by_name(self):
        response = self.client.get('/api/hosts', **self._auth())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        names = [host['name'] for host in body['hosts']]
        self.assertEqual(names, sorted(names))
        alexey = next(
            host for host in body['hosts'] if host['slug'] == 'alexey-grigorev'
        )
        self.assertEqual(
            set(alexey),
            {
                'id',
                'name',
                'slug',
                'title',
                'bio',
                'bio_html',
                'photo_url',
                'email',
                'is_active',
                'created_at',
                'updated_at',
            },
        )
        self.assertEqual(alexey['title'], 'Chief Agent Officer at AI Shipping Labs')

    def test_detail_returns_profile_or_unknown_host(self):
        response = self.client.get('/api/hosts/alexey-grigorev', **self._auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Alexey Grigorev')
        self.assertEqual(
            response.json()['title'],
            'Chief Agent Officer at AI Shipping Labs',
        )

        missing = self.client.get('/api/hosts/nope', **self._auth())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['code'], 'unknown_host')

    def test_patch_updates_title_and_bio_without_editing_event_row(self):
        before_event_updated_at = Event.objects.get(pk=self.event.pk).updated_at

        response = self._patch(
            'alexey-grigorev',
            {
                'title': 'Chief Agent Officer at AI Shipping Labs',
                'bio': '**Updated** host bio',
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['title'], 'Chief Agent Officer at AI Shipping Labs')
        self.assertIn('<strong>Updated</strong>', body['bio_html'])

        event = Event.objects.get(pk=self.event.pk)
        self.assertEqual(event.updated_at, before_event_updated_at)

        event_response = self.client.get(
            '/api/events/hosted-api-profile-event',
            **self._auth(),
        )
        self.assertEqual(
            event_response.json()['hosts'][0]['title'],
            'Chief Agent Officer at AI Shipping Labs',
        )

    def test_patch_allows_blank_title_and_updates_slug(self):
        response = self._patch(
            'alexey-grigorev',
            {
                'slug': 'alexey-api-profile',
                'title': '',
                'email': '',
                'is_active': False,
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['slug'], 'alexey-api-profile')
        self.assertEqual(body['title'], '')
        self.assertEqual(body['email'], '')
        self.assertFalse(body['is_active'])

    def test_patch_duplicate_slug_or_invalid_email_returns_422_without_mutating(self):
        before = Host.objects.values_list(
            'slug',
            'title',
            'email',
            'bio',
        ).get(pk=self.alexey.pk)

        duplicate = self._patch(
            'alexey-grigorev',
            {
                'slug': 'valeriia-kuka',
                'title': 'Should not persist',
            },
        )
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(duplicate.json()['code'], 'validation_error')
        self.assertIn('slug', duplicate.json()['details'])

        invalid_email = self._patch(
            'alexey-grigorev',
            {
                'email': 'not-an-email',
                'title': 'Should not persist either',
            },
        )
        self.assertEqual(invalid_email.status_code, 422)
        self.assertEqual(invalid_email.json()['code'], 'validation_error')
        self.assertIn('email', invalid_email.json()['details'])

        after = Host.objects.values_list(
            'slug',
            'title',
            'email',
            'bio',
        ).get(pk=self.alexey.pk)
        self.assertEqual(after, before)

    def test_patch_rejects_non_object_body(self):
        response = self.client.patch(
            '/api/hosts/alexey-grigorev',
            data=json.dumps([1, 2, 3]),
            content_type='application/json',
            **self._auth(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_type')

    def test_auth_matches_staff_events_api(self):
        cases = [
            ({}, {'error': 'Authentication token required'}),
            (
                {'HTTP_AUTHORIZATION': self.staff_token.key},
                {'error': 'Authentication token required'},
            ),
            (
                {'HTTP_AUTHORIZATION': 'Token does-not-exist'},
                {'error': 'Invalid token'},
            ),
            (
                self._auth(self.non_staff_token),
                {'error': 'Invalid token'},
            ),
        ]

        for headers, expected_body in cases:
            with self.subTest(headers=headers):
                before = Host.objects.get(pk=self.alexey.pk).title
                get_response = self.client.get('/api/hosts', **headers)
                patch_response = self.client.patch(
                    '/api/hosts/alexey-grigorev',
                    data=json.dumps({'title': 'Unauthorized'}),
                    content_type='application/json',
                    **headers,
                )

                self.assertEqual(get_response.status_code, 401)
                self.assertEqual(get_response.json(), expected_body)
                self.assertEqual(patch_response.status_code, 401)
                self.assertEqual(patch_response.json(), expected_body)
                self.assertEqual(Host.objects.get(pk=self.alexey.pk).title, before)
