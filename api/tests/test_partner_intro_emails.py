"""API tests for sprint partner intro emails (#1124)."""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from accounts.models import Token
from email_app.models import EmailLog
from plans.models import (
    Plan,
    Sprint,
    SprintEnrollment,
    SprintPartnerIntroEmailLog,
)
from plans.services import assign_accountability_partners

User = get_user_model()


@tag('core')
class PartnerIntroEmailsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.non_staff = User.objects.create_user(
            email='nonstaff@test.com', password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')
        cls.non_staff_token = Token(
            key='non-staff-partner-intro-token',
            user=cls.non_staff,
            name='member',
        )
        Token.objects.bulk_create([cls.non_staff_token])

    def setUp(self):
        self.sprint = Sprint.objects.create(
            name='May Sprint',
            slug='may-sprint',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )

    def _url(self, slug='may-sprint'):
        return f'/api/sprints/{slug}/partner-intro-emails'

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _post(self, payload=None, *, token=None, slug='may-sprint', raw=None):
        body = raw if raw is not None else json.dumps({} if payload is None else payload)
        return self.client.post(
            self._url(slug),
            data=body,
            content_type='application/json',
            **self._auth(token),
        )

    def _ready_pair(self):
        alice = User.objects.create_user(email='alice@test.com', password='pw')
        bob = User.objects.create_user(
            email='bob@test.com',
            password='pw',
            slack_user_id='UBOB',
        )
        for user in (alice, bob):
            SprintEnrollment.objects.create(
                sprint=self.sprint,
                user=user,
                enrolled_by=self.staff,
            )
            Plan.objects.create(sprint=self.sprint, member=user)
        assign_accountability_partners(
            sprint=self.sprint,
            member=alice,
            partner=bob,
            assigned_by=self.staff,
        )
        return alice, bob

    def test_dry_run_returns_preview_without_side_effects(self):
        self._ready_pair()

        response = self._post({'dry_run': True})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['dry_run'])
        self.assertTrue(body['send_ready'])
        self.assertEqual(body['total_enrolled'], 2)
        self.assertEqual(body['eligible_count'], 2)
        self.assertEqual(body['sent_count'], 0)
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_send_and_second_send_are_idempotent(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        self._ready_pair()

        first = self._post({'dry_run': False})
        second = self._post({'dry_run': False})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['sent_count'], 2)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()['sent_count'], 0)
        self.assertEqual(second.json()['skipped_already_sent_count'], 2)
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 2)
        self.assertEqual(
            EmailLog.objects.filter(email_type='sprint_partner_intro').count(),
            2,
        )
        self.assertEqual(mock_ses.call_count, 2)

    def test_non_staff_token_cannot_preview_or_send(self):
        self._ready_pair()

        response = self._post({'dry_run': False}, token=self.non_staff_token)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'forbidden_staff_only')
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_blocked_send_returns_validation_error_without_side_effects(self):
        self.sprint.status = 'draft'
        self.sprint.save(update_fields=['status'])
        self._ready_pair()

        response = self._post({'dry_run': False})

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body['code'], 'validation_error')
        self.assertFalse(body['details']['summary']['send_ready'])
        self.assertEqual(SprintPartnerIntroEmailLog.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_invalid_body_and_dry_run_type_return_json_errors(self):
        bad_body = self._post(raw='[]')
        bad_dry_run = self._post({'dry_run': 'yes'})

        self.assertEqual(bad_body.status_code, 400)
        self.assertEqual(bad_body.json()['code'], 'invalid_type')
        self.assertEqual(bad_dry_run.status_code, 422)
        self.assertEqual(bad_dry_run.json()['code'], 'validation_error')

    def test_openapi_documents_endpoint(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        document = build_spec(urlpatterns)
        path = '/api/sprints/{slug}/partner-intro-emails'

        self.assertIn(path, document['paths'])
        operation = document['paths'][path]['post']
        self.assertEqual(
            operation['summary'],
            'Send partner intro emails (staff-only)',
        )
        self.assertIn('requestBody', operation)
        for status in ('200', '400', '403', '404', '422'):
            self.assertIn(status, operation['responses'])
