"""Tests for bulk plan-ready email service (issue #1055)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from notifications.models import Notification
from plans.models import (
    PLAN_READY_EMAIL_STATUS_FAILED,
    PLAN_READY_EMAIL_STATUS_SENT,
    Plan,
    PlanReadyEmailLog,
    Sprint,
)
from plans.services import preview_plan_ready_emails, send_plan_ready_emails

User = get_user_model()


@tag('core')
class PlanReadyEmailServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026',
            slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )

    def _member_plan(self, email, *, shared_at=None):
        member = User.objects.create_user(email=email, password='pw')
        return Plan.objects.create(
            member=member,
            sprint=self.sprint,
            shared_at=shared_at,
        )

    def test_preview_counts_eligible_sent_and_failed_previous_attempts(self):
        self._member_plan('eligible@test.com')
        sent = self._member_plan('sent@test.com')
        failed = self._member_plan('failed@test.com')
        PlanReadyEmailLog.objects.create(
            plan=sent,
            sprint=self.sprint,
            member=sent.member,
            status=PLAN_READY_EMAIL_STATUS_SENT,
            sent_at=timezone.now(),
        )
        PlanReadyEmailLog.objects.create(
            plan=failed,
            sprint=self.sprint,
            member=failed.member,
            status=PLAN_READY_EMAIL_STATUS_FAILED,
            last_error='SES timeout',
        )

        summary = preview_plan_ready_emails(self.sprint)

        self.assertTrue(summary['dry_run'])
        self.assertEqual(summary['total_plans'], 3)
        self.assertEqual(summary['eligible_count'], 2)
        self.assertEqual(summary['already_sent_count'], 1)
        self.assertEqual(summary['failed_previous_attempts_count'], 1)
        self.assertEqual(
            {row['member_email'] for row in summary['eligible']},
            {'eligible@test.com', 'failed@test.com'},
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_send_creates_side_effects_and_stamps_shared_at(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        plan = self._member_plan('member@test.com')

        summary = send_plan_ready_emails(
            sprint=self.sprint,
            actor=self.staff,
        )

        self.assertEqual(summary['sent_count'], 1)
        self.assertEqual(summary['failed_count'], 0)
        plan.refresh_from_db()
        self.assertIsNotNone(plan.shared_at)
        log = PlanReadyEmailLog.objects.get(plan=plan)
        self.assertEqual(log.status, PLAN_READY_EMAIL_STATUS_SENT)
        self.assertEqual(log.triggered_by, self.staff)
        self.assertIsNotNone(log.sent_at)
        self.assertIsNotNone(log.notification)
        self.assertIsNotNone(log.email_log)
        self.assertEqual(log.notification.notification_type, 'plan_shared')
        self.assertEqual(log.email_log.email_type, 'plan_shared')
        self.assertEqual(
            Notification.objects.filter(
                user=plan.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        self.assertEqual(
            EmailLog.objects.filter(user=plan.member, email_type='plan_shared').count(),
            1,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_second_send_skips_already_successful_plan(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        plan = self._member_plan('member@test.com')
        send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        second = send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        self.assertEqual(second['sent_count'], 0)
        self.assertEqual(second['skipped_already_sent_count'], 1)
        self.assertEqual(PlanReadyEmailLog.objects.filter(plan=plan).count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(user=plan.member, email_type='plan_shared').count(),
            1,
        )
        self.assertEqual(mock_ses.call_count, 1)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_existing_shared_at_is_not_moved_backward_or_forward(self, mock_ses):
        mock_ses.return_value = 'ses-1'
        original = timezone.now() - datetime.timedelta(days=3)
        plan = self._member_plan('shared@test.com', shared_at=original)

        send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        plan.refresh_from_db()
        self.assertEqual(plan.shared_at, original)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_one_failure_does_not_stop_remaining_recipients(self, mock_ses):
        good = self._member_plan('good@test.com')
        bad = self._member_plan('bad@test.com')
        other = self._member_plan('other@test.com')

        def send_side_effect(to_email, *args, **kwargs):
            if to_email == 'bad@test.com':
                raise RuntimeError('SES rejected bad@test.com')
            return f'ses-{to_email}'

        mock_ses.side_effect = send_side_effect

        summary = send_plan_ready_emails(
            sprint=self.sprint,
            actor=self.staff,
        )

        self.assertEqual(summary['sent_count'], 2)
        self.assertEqual(summary['failed_count'], 1)
        self.assertEqual(
            {row['member_email'] for row in summary['failed']},
            {'bad@test.com'},
        )
        for plan in (good, other):
            plan.refresh_from_db()
            self.assertIsNotNone(plan.shared_at)
            self.assertEqual(
                PlanReadyEmailLog.objects.get(plan=plan).status,
                PLAN_READY_EMAIL_STATUS_SENT,
            )
        bad.refresh_from_db()
        self.assertIsNone(bad.shared_at)
        failed_log = PlanReadyEmailLog.objects.get(plan=bad)
        self.assertEqual(failed_log.status, PLAN_READY_EMAIL_STATUS_FAILED)
        self.assertIn('SES rejected bad@test.com', failed_log.last_error)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_dry_run_has_no_side_effects(self, mock_ses):
        plan = self._member_plan('member@test.com')

        summary = send_plan_ready_emails(
            sprint=self.sprint,
            actor=self.staff,
            dry_run=True,
        )

        self.assertEqual(summary['eligible_count'], 1)
        self.assertEqual(mock_ses.call_count, 0)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)
        plan.refresh_from_db()
        self.assertIsNone(plan.shared_at)
