"""One-plan ready-delivery outcome layer (issue #1455)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import (
    PLAN_READY_EMAIL_STATUS_FAILED,
    PLAN_READY_EMAIL_STATUS_SENDING,
    PLAN_READY_EMAIL_STATUS_SENT,
    Plan,
    PlanReadyEmailLog,
    Sprint,
)
from plans.services import PLAN_READY_EMAIL_PUBLIC_ERROR, run_plan_ready_action

User = get_user_model()


@tag('core')
class RunPlanReadyActionTest(TestCase):
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
        cls.sprint = Sprint.objects.create(
            name='August 2026',
            slug='august-2026',
            start_date=datetime.date(2026, 8, 1),
        )

    def setUp(self):
        self.plan = Plan.objects.create(
            member=self.member, sprint=self.sprint,
        )

    def _run(self, **kwargs):
        return run_plan_ready_action(self.plan, actor=self.staff, **kwargs)

    def test_dry_run_reports_eligible_without_any_write(self):
        with patch(
            'email_app.services.email_service.EmailService._send_ses',
        ) as mock_ses:
            result = self._run(dry_run=True)

        self.assertFalse(mock_ses.called)
        self.assertEqual(result['plan_id'], self.plan.pk)
        self.assertEqual(result['member_id'], self.member.pk)
        self.assertEqual(result['member_email'], 'member@test.com')
        self.assertEqual(result['sprint_slug'], 'august-2026')
        self.assertIsNone(result['shared_at'])
        ready = result['ready_email']
        self.assertEqual(ready['status'], 'eligible')
        self.assertTrue(ready['dry_run'])
        self.assertTrue(ready['eligible'])
        self.assertFalse(ready['requested'])
        self.assertFalse(ready['sent'])
        self.assertIsNone(ready['sent_at'])
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_first_live_call_sends_once_and_stamps_shared_at(self, mock_ses):
        mock_ses.return_value = 'msg-1'

        result = self._run()

        ready = result['ready_email']
        self.assertEqual(ready['status'], 'sent')
        self.assertTrue(ready['sent'])
        self.assertTrue(ready['requested'])
        self.assertFalse(ready['dry_run'])
        self.assertFalse(ready['eligible'])
        self.assertFalse(ready['failed'])
        self.assertIsNotNone(ready['sent_at'])
        self.plan.refresh_from_db()
        self.assertIsNotNone(self.plan.shared_at)
        self.assertEqual(result['shared_at'], self.plan.shared_at.isoformat())
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
        log = PlanReadyEmailLog.objects.get(plan=self.plan)
        self.assertEqual(log.status, PLAN_READY_EMAIL_STATUS_SENT)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_repeat_after_success_reports_already_sent_without_duplicates(
        self, mock_ses,
    ):
        mock_ses.return_value = 'msg-1'
        first = self._run()
        first_shared_at = first['shared_at']

        second = self._run()

        ready = second['ready_email']
        self.assertEqual(ready['status'], 'already_sent')
        self.assertTrue(ready['skipped_already_sent'])
        self.assertFalse(ready['sent'])
        self.assertIsNotNone(ready['sent_at'])
        self.assertEqual(second['shared_at'], first_shared_at)
        self.assertEqual(
            Notification.objects.filter(
                notification_type='plan_shared',
            ).count(),
            1,
        )
        self.assertEqual(
            EmailLog.objects.filter(email_type='plan_shared').count(), 1,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_historically_shared_plan_reports_already_shared(self, mock_ses):
        shared_at = timezone.now()
        self.plan.shared_at = shared_at
        self.plan.save(update_fields=['shared_at'])

        result = self._run()

        ready = result['ready_email']
        self.assertEqual(ready['status'], 'already_shared')
        self.assertTrue(ready['skipped_already_shared'])
        self.assertFalse(ready['sent'])
        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.shared_at, shared_at)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_in_progress_claim_is_reported_and_not_resent(self):
        PlanReadyEmailLog.objects.create(
            plan=self.plan,
            sprint=self.sprint,
            member=self.member,
            status=PLAN_READY_EMAIL_STATUS_SENDING,
        )

        with patch(
            'email_app.services.email_service.EmailService._send_ses',
        ) as mock_ses:
            result = self._run()

        self.assertEqual(result['ready_email']['status'], 'in_progress')
        self.assertFalse(mock_ses.called)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)

    @patch(
        'notifications.services.notification_service.'
        'NotificationService.create_plan_shared_delivery'
    )
    def test_failed_delivery_is_retryable_and_leaves_plan_unshared(
        self, mock_delivery,
    ):
        mock_delivery.side_effect = Exception('ses exploded')

        result = self._run()

        ready = result['ready_email']
        self.assertEqual(ready['status'], 'failed_retryable')
        self.assertTrue(ready['failed'])
        self.assertTrue(ready['retryable'])
        self.assertEqual(ready['error'], PLAN_READY_EMAIL_PUBLIC_ERROR)
        self.plan.refresh_from_db()
        self.assertIsNone(self.plan.shared_at)
        self.assertIsNone(result['shared_at'])
        log = PlanReadyEmailLog.objects.get(plan=self.plan)
        self.assertEqual(log.status, PLAN_READY_EMAIL_STATUS_FAILED)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_retry_after_failure_completes_only_the_requested_plan(
        self, mock_ses,
    ):
        sibling = Plan.objects.create(member=self.other, sprint=self.sprint)
        PlanReadyEmailLog.objects.create(
            plan=self.plan,
            sprint=self.sprint,
            member=self.member,
            status=PLAN_READY_EMAIL_STATUS_FAILED,
            last_error='ses exploded',
        )
        mock_ses.return_value = 'msg-1'

        result = self._run()

        self.assertEqual(result['ready_email']['status'], 'sent')
        sibling.refresh_from_db()
        self.assertIsNone(sibling.shared_at)
        self.assertFalse(
            PlanReadyEmailLog.objects.filter(plan=sibling).exists(),
        )
        self.assertEqual(
            Notification.objects.filter(user=self.other).count(), 0,
        )

    def test_dry_run_after_failure_reports_eligible_again(self):
        PlanReadyEmailLog.objects.create(
            plan=self.plan,
            sprint=self.sprint,
            member=self.member,
            status=PLAN_READY_EMAIL_STATUS_FAILED,
            last_error='ses exploded',
        )

        result = self._run(dry_run=True)

        self.assertEqual(result['ready_email']['status'], 'eligible')
        self.assertTrue(result['ready_email']['eligible'])

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_dry_run_after_success_reports_already_sent(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        self._run()

        result = self._run(dry_run=True)

        ready = result['ready_email']
        self.assertEqual(ready['status'], 'already_sent')
        self.assertTrue(ready['dry_run'])
        self.assertFalse(ready['requested'])
        self.assertEqual(
            EmailLog.objects.filter(email_type='plan_shared').count(), 1,
        )
