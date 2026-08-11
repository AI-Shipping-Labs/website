"""Staff-token Call profile CRUD API tests (#1404)."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import Token
from community.models import STATUS_CANCELED, BookedCall, CallHost

User = get_user_model()


@tag('core')
class CallProfilesApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='call-profile-api-staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='call-profile-api-member@test.com',
            password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='call-profiles')
        cls.member_token = Token(
            key='non-staff-call-profile-api-token',
            user=cls.member,
            name='legacy-member-call-profiles',
        )
        Token.objects.bulk_create([cls.member_token])

    def setUp(self):
        CallHost.objects.exclude(slug__in=['alexey', 'valeria']).delete()

    def _auth(self, token=None):
        return {
            'HTTP_AUTHORIZATION': f'Token {(token or self.staff_token).key}',
        }

    def _post(self, payload, **headers):
        return self.client.post(
            '/api/call-profiles',
            data=json.dumps(payload),
            content_type='application/json',
            **(headers or self._auth()),
        )

    def _patch(self, slug, payload, **headers):
        return self.client.patch(
            f'/api/call-profiles/{slug}',
            data=json.dumps(payload),
            content_type='application/json',
            **(headers or self._auth()),
        )

    def test_list_is_ordered_and_exposes_only_contract_fields(self):
        CallHost.objects.create(
            name='AAA Profile',
            slug='aaa-profile',
            is_active=False,
            order=0,
            capacity=77,
            current_load=66,
        )
        response = self.client.get('/api/call-profiles', **self._auth())
        self.assertEqual(response.status_code, 200)
        profiles = response.json()['call_profiles']
        ordering = [(profile['order'], profile['name']) for profile in profiles]
        self.assertEqual(ordering, sorted(ordering))
        self.assertEqual(
            set(profiles[0]),
            {
                'id', 'name', 'slug', 'role_label', 'photo_url',
                'booking_url', 'is_active', 'order', 'created_at', 'updated_at',
            },
        )
        forbidden = {'capacity', 'current_load', 'is_available', 'open_spots'}
        self.assertFalse(set(profiles[0]) & forbidden)

    def test_create_hidden_profile_and_generate_slug(self):
        response = self._post({
            'name': 'API Profile',
            'role_label': 'Coach',
            'booking_url': '',
            'is_active': False,
            'order': 4,
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['slug'], 'api-profile')
        self.assertFalse(response.json()['is_active'])
        self.assertTrue(CallHost.objects.filter(slug='api-profile').exists())

    def test_create_rejects_unknown_invalid_or_non_object_payload_without_mutation(self):
        before = CallHost.objects.count()
        invalid = self._post({
            'name': 'Invalid',
            'slug': 'invalid',
            'booking_url': 'ftp://example.com/book',
            'photo_url': 'javascript:alert(1)',
            'is_active': True,
            'order': -1,
            'capacity': 10,
        })
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()['code'], 'validation_error')
        for field in ('booking_url', 'photo_url', 'order', 'capacity'):
            self.assertIn(field, invalid.json()['details'])
        self.assertEqual(CallHost.objects.count(), before)

        non_object = self.client.post(
            '/api/call-profiles',
            data=json.dumps([]),
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(non_object.status_code, 400)
        self.assertEqual(non_object.json()['code'], 'invalid_type')

        malformed = self.client.post(
            '/api/call-profiles',
            data='{',
            content_type='application/json',
            **self._auth(),
        )
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.json(), {'error': 'Invalid JSON'})

    def test_post_and_patch_reject_non_string_text_fields_without_mutation(self):
        text_fields = ('name', 'slug', 'role_label', 'photo_url', 'booking_url')
        for field in text_fields:
            with self.subTest(method='POST', field=field):
                before = CallHost.objects.count()
                payload = {
                    'name': 'Typed API profile',
                    'slug': f'typed-api-{field}',
                    'is_active': False,
                    field: ['not', 'text'],
                }
                response = self._post(payload)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()['details'][field],
                    'Must be a string.',
                )
                self.assertEqual(CallHost.objects.count(), before)

        profile = CallHost.objects.create(
            name='Typed patch profile',
            slug='typed-patch-profile',
            role_label='Original role',
            booking_url='https://example.com/original',
            is_active=True,
        )
        original = {
            field: getattr(profile, field)
            for field in text_fields
        }
        for field in text_fields:
            with self.subTest(method='PATCH', field=field):
                response = self._patch(
                    'typed-patch-profile',
                    {field: {'not': 'text'}},
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json()['details'][field],
                    'Must be a string.',
                )
                profile.refresh_from_db()
                self.assertEqual(
                    {key: getattr(profile, key) for key in text_fields},
                    original,
                )

    def test_active_profile_requires_link(self):
        response = self._post({
            'name': 'Linkless',
            'slug': 'linkless',
            'booking_url': '',
            'is_active': True,
        })
        self.assertEqual(response.status_code, 422)
        self.assertIn('booking_url', response.json()['details'])
        self.assertFalse(CallHost.objects.filter(slug='linkless').exists())

    def test_retrieve_patch_publish_hide_and_clear_link(self):
        profile = CallHost.objects.create(
            name='API Draft', slug='api-draft', is_active=False, order=5,
        )
        get_response = self.client.get('/api/call-profiles/api-draft', **self._auth())
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(get_response.json()['id'], profile.pk)

        publish = self._patch('api-draft', {
            'booking_url': 'https://example.com/book',
            'is_active': True,
        })
        self.assertEqual(publish.status_code, 200)
        self.assertTrue(publish.json()['is_active'])

        invalid_clear = self._patch('api-draft', {
            'booking_url': '',
            'name': 'Must not persist',
        })
        self.assertEqual(invalid_clear.status_code, 422)
        profile.refresh_from_db()
        self.assertEqual(profile.name, 'API Draft')
        self.assertEqual(profile.booking_url, 'https://example.com/book')
        self.assertTrue(profile.is_active)

        hidden = self._patch('api-draft', {
            'booking_url': '',
            'is_active': False,
        })
        self.assertEqual(hidden.status_code, 200)
        self.assertEqual(hidden.json()['booking_url'], '')
        self.assertFalse(hidden.json()['is_active'])

    def test_delete_unused_profile_returns_204(self):
        CallHost.objects.create(name='Unused API', slug='unused-api', is_active=False)
        response = self.client.delete('/api/call-profiles/unused-api', **self._auth())
        self.assertEqual(response.status_code, 204)
        self.assertFalse(CallHost.objects.filter(slug='unused-api').exists())
        missing = self.client.get('/api/call-profiles/unused-api', **self._auth())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()['code'], 'call_profile_not_found')

    def test_delete_in_use_profile_returns_409_and_preserves_history(self):
        profile = CallHost.objects.create(
            name='History API',
            slug='history-api',
            booking_url='https://example.com/history',
            is_active=True,
        )
        booked_call = BookedCall.objects.create(
            host=profile,
            invitee_email='history-api@example.com',
            status=STATUS_CANCELED,
            calendly_event_uri='https://api.calendly.com/scheduled_events/api-history',
        )
        response = self.client.delete('/api/call-profiles/history-api', **self._auth())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            'error': (
                "This call profile has booked-call history and can't be deleted. "
                'Hide it with PATCH is_active=false instead.'
            ),
            'code': 'call_profile_in_use',
        })
        self.assertTrue(CallHost.objects.filter(pk=profile.pk).exists())
        self.assertTrue(BookedCall.objects.filter(pk=booked_call.pk).exists())

    def test_missing_non_staff_and_session_auth_all_return_401_without_mutation(self):
        self.client.force_login(self.staff)
        profile = CallHost.objects.create(
            name='Protected API', slug='protected-api', is_active=False,
        )
        auth_cases = [
            {},
            {'HTTP_AUTHORIZATION': f'Token {self.member_token.key}'},
            {'HTTP_AUTHORIZATION': 'Token invalid'},
        ]
        for headers in auth_cases:
            with self.subTest(headers=headers):
                self.assertEqual(
                    self.client.get('/api/call-profiles', **headers).status_code,
                    401,
                )
                self.assertEqual(
                    self.client.patch(
                        '/api/call-profiles/protected-api',
                        data=json.dumps({'name': 'Unauthorized'}),
                        content_type='application/json',
                        **headers,
                    ).status_code,
                    401,
                )
                self.assertEqual(
                    self.client.delete('/api/call-profiles/protected-api', **headers).status_code,
                    401,
                )
        profile.refresh_from_db()
        self.assertEqual(profile.name, 'Protected API')

    def test_no_call_hosts_api_alias_exists(self):
        response = self.client.get('/api/call-hosts', **self._auth())
        self.assertEqual(response.status_code, 404)
