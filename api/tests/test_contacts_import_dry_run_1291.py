"""API dry-run parity for contact imports (#1291)."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import TierOverride, Token

User = get_user_model()


class ContactsImportDryRunApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='contacts')

    def _post(self, payload, *, token=True):
        headers = {}
        if token:
            headers['HTTP_AUTHORIZATION'] = f'Token {self.token.key}'
        return self.client.post(
            '/api/contacts/import',
            data=json.dumps(payload),
            content_type='application/json',
            **headers,
        )

    def test_true_dry_run_returns_classification_without_any_writes_or_provider(self):
        existing = User.objects.create_user(
            email='existing@example.com', password=None, tags=['keep'],
        )
        before = User.objects.count()
        with mock.patch(
            'studio.services.contacts_import.backfill_user_from_stripe',
        ) as provider:
            response = self._post({
                'contacts': [
                    {'email': 'existing@example.com', 'tags': ['new']},
                    {'email': 'new@example.com', 'stripe_customer_id': 'cus_new'},
                    {'email': 'NEW@example.com'},
                    {'email': 'not-an-email'},
                ],
                'default_tag': 'batch',
                'default_tier': 'main',
                'dry_run': True,
            })

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['dry_run'], True)
        self.assertEqual(
            (body['created'], body['updated'], body['skipped'], body['malformed']),
            (1, 1, 1, 1),
        )
        self.assertEqual(
            [warning['reason'] for warning in body['warnings']],
            ['duplicate within file', 'malformed email'],
        )
        provider.assert_not_called()
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())
        self.assertEqual(TierOverride.objects.count(), 0)
        existing.refresh_from_db()
        self.assertEqual(existing.tags, ['keep'])

    def test_omitted_and_false_preserve_legacy_apply_response(self):
        omitted = self._post({'contacts': [{'email': 'one@example.com'}]})
        explicit_false = self._post({
            'contacts': [{'email': 'two@example.com'}],
            'dry_run': False,
        })
        self.assertEqual(omitted.status_code, 200)
        self.assertEqual(explicit_false.status_code, 200)
        self.assertNotIn('dry_run', omitted.json())
        self.assertNotIn('dry_run', explicit_false.json())
        self.assertTrue(User.objects.filter(email='one@example.com').exists())
        self.assertTrue(User.objects.filter(email='two@example.com').exists())

    def test_non_boolean_dry_run_has_stable_validation_error(self):
        for value in ('true', 1, None, [], {}):
            with self.subTest(value=value):
                response = self._post({
                    'contacts': [{'email': 'never@example.com'}],
                    'dry_run': value,
                })
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {
                    'error': 'dry_run must be a boolean',
                    'code': 'invalid_dry_run',
                })
        self.assertFalse(User.objects.filter(email='never@example.com').exists())

    def test_token_gate_precedes_dry_run_validation(self):
        response = self._post(
            {'contacts': 'not-a-list', 'dry_run': 'true'}, token=False,
        )
        self.assertEqual(response.status_code, 401)

    def test_generated_openapi_documents_boolean_default_and_no_write_semantics(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        operation = build_spec(urlpatterns)['paths']['/api/contacts/import']['post']
        dry_run = operation['requestBody']['content']['application/json'][
            'schema'
        ]['properties']['dry_run']
        self.assertEqual(dry_run['type'], 'boolean')
        self.assertIs(dry_run['default'], False)
        self.assertIn('without database writes', operation['description'])
        example = operation['responses']['200']['content']['application/json']['example']
        self.assertIs(example['dry_run'], True)
