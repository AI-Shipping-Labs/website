"""API tests for bulk plan-ready emails (issue #1055)."""

import datetime
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import Token
from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import (
    PLAN_READY_EMAIL_STATUS_SENT,
    Plan,
    PlanReadyEmailLog,
    Sprint,
)

User = get_user_model()


@tag('core')
class PlanReadyEmailsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.other = User.objects.create_user(
            email='other@test.com', password='pw',
        )
        cls.non_staff = User.objects.create_user(
            email='nonstaff@test.com', password='pw',
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name='staff')
        cls.non_staff_token = Token(
            key='non-staff-plan-ready-token',
            user=cls.non_staff,
            name='legacy-member-token',
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def setUp(self):
        PlanReadyEmailLog.objects.all().delete()
        Plan.objects.all().delete()

    def _url(self, slug='may-2026'):
        return f'/api/sprints/{slug}/plans/send-ready-emails'

    def _auth(self, token=None):
        token = token or self.staff_token
        return {'HTTP_AUTHORIZATION': f'Token {token.key}'}

    def _post(self, payload=None, *, token=None, slug='may-2026', raw=None):
        if raw is not None:
            body = raw
        else:
            body = json.dumps({} if payload is None else payload)
        return self.client.post(
            self._url(slug),
            data=body,
            content_type='application/json',
            **self._auth(token),
        )

    def test_dry_run_returns_preview_without_side_effects(self):
        eligible = Plan.objects.create(member=self.member, sprint=self.sprint)
        already = Plan.objects.create(member=self.other, sprint=self.sprint)
        PlanReadyEmailLog.objects.create(
            plan=already,
            sprint=self.sprint,
            member=already.member,
            status=PLAN_READY_EMAIL_STATUS_SENT,
            sent_at=timezone.now(),
        )

        response = self._post({'dry_run': True})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['dry_run'])
        self.assertEqual(body['total_plans'], 2)
        self.assertEqual(body['eligible_count'], 1)
        self.assertEqual(body['already_sent_count'], 1)
        self.assertEqual(body['sent_count'], 0)
        self.assertEqual(
            [row['plan_id'] for row in body['eligible']],
            [eligible.pk],
        )
        eligible.refresh_from_db()
        self.assertIsNone(eligible.shared_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)
        self.assertEqual(
            PlanReadyEmailLog.objects.filter(plan=eligible).count(),
            0,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_send_and_second_send_are_idempotent(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        first = Plan.objects.create(member=self.member, sprint=self.sprint)
        second = Plan.objects.create(member=self.other, sprint=self.sprint)

        response = self._post({})
        repeat = self._post({})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeat.status_code, 200)
        body = response.json()
        self.assertEqual(body['sent_count'], 2)
        self.assertEqual(body['failed_count'], 0)
        repeat_body = repeat.json()
        self.assertEqual(repeat_body['sent_count'], 0)
        self.assertEqual(repeat_body['skipped_already_sent_count'], 2)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 2)
        self.assertEqual(EmailLog.objects.filter(email_type='plan_shared').count(), 2)
        self.assertEqual(mock_ses.call_count, 2)
        for plan in (first, second):
            plan.refresh_from_db()
            self.assertIsNotNone(plan.shared_at)

    def test_non_staff_bearer_gets_403(self):
        Plan.objects.create(member=self.member, sprint=self.sprint)

        response = self._post({}, token=self.non_staff_token)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'forbidden_staff_only')
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_unknown_sprint_returns_404(self):
        response = self._post({}, slug='missing')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_sprint')

    def test_non_object_json_body_returns_validation_error(self):
        response = self._post(raw='[]')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['code'], 'invalid_type')

    def test_invalid_dry_run_type_returns_validation_error(self):
        response = self._post({'dry_run': 'yes'})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['code'], 'validation_error')

    def test_openapi_documents_endpoint(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        document = build_spec(urlpatterns)
        path = '/api/sprints/{slug}/plans/send-ready-emails'

        self.assertIn(path, document['paths'])
        operation = document['paths'][path]['post']
        self.assertEqual(operation['summary'], 'Send plan-ready emails (staff-only)')
        self.assertIn('requestBody', operation)
        for status in ('200', '400', '403', '404', '422'):
            self.assertIn(status, operation['responses'])
