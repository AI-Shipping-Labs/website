"""API tests for the single-plan ready email action (issue #1455)."""

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
    PLAN_READY_EMAIL_STATUS_FAILED,
    Plan,
    PlanReadyEmailLog,
    Sprint,
)
from plans.services import PLAN_READY_EMAIL_PUBLIC_ERROR

User = get_user_model()


@tag('core')
class PlanSendReadyEmailApiTest(TestCase):
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
            key='non-staff-plan-send-ready-token',
            user=cls.non_staff,
            name='legacy-member-token',
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.sprint = Sprint.objects.create(
            name='August 2026',
            slug='august-2026',
            start_date=datetime.date(2026, 8, 1),
        )

    def setUp(self):
        self.plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )

    def _url(self, plan_id=None):
        plan_id = self.plan.pk if plan_id is None else plan_id
        return f'/api/plans/{plan_id}/send-ready-email'

    def _post(self, payload=None, *, token=None, plan_id=None, raw=None):
        token = token or self.staff_token
        body = raw if raw is not None else json.dumps(payload or {})
        return self.client.post(
            self._url(plan_id),
            data=body,
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Token {token.key}',
        )

    def _no_side_effects(self):
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_non_staff_token_is_forbidden_without_side_effects(self):
        response = self._post(token=self.non_staff_token)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()['code'], 'forbidden_staff_only',
        )
        self._no_side_effects()

    def test_unknown_plan_returns_404(self):
        response = self._post(plan_id=self.plan.pk + 9999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'unknown_plan')
        self._no_side_effects()

    def test_non_boolean_dry_run_is_rejected_without_side_effects(self):
        response = self._post({'dry_run': 'yes'})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body['code'], 'validation_error')
        self.assertEqual(body['details']['field'], 'dry_run')
        self._no_side_effects()

    def test_dry_run_reports_eligible_with_zero_writes(self):
        with patch(
            'email_app.services.email_service.EmailService._send_ses',
        ) as mock_ses:
            response = self._post({'dry_run': True})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['plan_id'], self.plan.pk)
        self.assertEqual(body['member_id'], self.member.pk)
        self.assertEqual(body['member_email'], 'member@test.com')
        self.assertEqual(body['sprint_slug'], 'august-2026')
        self.assertIsNone(body['shared_at'])
        self.assertEqual(body['ready_email']['status'], 'eligible')
        self.assertTrue(body['ready_email']['dry_run'])
        self.assertFalse(mock_ses.called)
        self._no_side_effects()

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_live_send_creates_exactly_one_bell_email_and_log(self, mock_ses):
        mock_ses.return_value = 'msg-1'

        response = self._post()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['ready_email']['status'], 'sent')
        self.assertTrue(body['ready_email']['sent'])
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.shared_at)
        self.assertEqual(body['shared_at'], self.plan.shared_at.isoformat())
        self.assertEqual(self.plan.visibility, 'private')
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            1,
        )
        self.assertEqual(PlanReadyEmailLog.objects.filter(
            plan=self.plan,
        ).count(), 1)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_repeat_call_reports_already_sent_and_preserves_shared_at(
        self, mock_ses,
    ):
        mock_ses.return_value = 'msg-1'
        first = self._post().json()

        second = self._post().json()

        self.assertEqual(second['ready_email']['status'], 'already_sent')
        self.assertFalse(second['ready_email']['sent'])
        self.assertEqual(second['shared_at'], first['shared_at'])
        self.assertEqual(
            EmailLog.objects.filter(email_type='plan_shared').count(), 1,
        )
        self.assertEqual(
            Notification.objects.filter(
                notification_type='plan_shared',
            ).count(),
            1,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_historically_shared_plan_reports_already_shared(self, mock_ses):
        shared_at = timezone.now()
        self.plan.shared_at = shared_at
        self.plan.save(update_fields=['shared_at'])

        body = self._post().json()

        self.assertEqual(body['ready_email']['status'], 'already_shared')
        self.assertTrue(body['ready_email']['skipped_already_shared'])
        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.shared_at, shared_at)
        self.assertEqual(Notification.objects.count(), 0)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_failed_delivery_reports_retryable_and_leaves_plan_unshared(
        self, mock_ses,
    ):
        provider_detail = 'PROVIDER_SECRET_SENTINEL'
        mock_ses.side_effect = RuntimeError(provider_detail)

        response = self._post()
        body = response.json()

        self.assertEqual(body['ready_email']['status'], 'failed_retryable')
        self.assertTrue(body['ready_email']['retryable'])
        self.assertEqual(
            body['ready_email']['error'],
            PLAN_READY_EMAIL_PUBLIC_ERROR,
        )
        self.assertNotIn(provider_detail, response.content.decode())
        self.assertIsNone(body['shared_at'])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)
        log = PlanReadyEmailLog.objects.get(plan=self.plan)
        self.assertEqual(log.status, PLAN_READY_EMAIL_STATUS_FAILED)
        self.assertIn(provider_detail, log.last_error)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_action_never_touches_a_sibling_plan(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        sibling = Plan.objects.create(member=self.other, sprint=self.sprint)

        self._post()

        sibling.refresh_from_db()
        self.assertIsNone(sibling.shared_at)
        self.assertEqual(
            Notification.objects.filter(user=self.other).count(), 0,
        )

    def test_get_is_not_allowed(self):
        response = self.client.get(
            self._url(),
            HTTP_AUTHORIZATION=f'Token {self.staff_token.key}',
        )
        self.assertEqual(response.status_code, 405)
        self._no_side_effects()

    def test_openapi_documents_the_endpoint(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        spec = build_spec(urlpatterns)
        path = '/api/plans/{plan_id}/send-ready-email'
        self.assertIn(path, spec['paths'])
        operation = spec['paths'][path]['post']
        self.assertIn(
            'dry_run',
            operation['requestBody']['content']['application/json']['schema'][
                'properties'
            ],
        )
        for status in ('200', '403', '404', '422'):
            self.assertIn(status, operation['responses'])
        example = (
            operation['responses']['200']['content']['application/json'][
                'example'
            ]
        )
        self.assertEqual(example['ready_email']['status'], 'sent')
