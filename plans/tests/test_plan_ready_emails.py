"""Tests for bulk plan-ready email service (issue #1055).

A1.2 slice 4: the plan-shared email is a durable ``EmailDelivery``; the
worker writes the ``EmailLog`` audit row after provider acceptance, so
tests drain pending deliveries with ``deliver_pending_mail`` and the
``PlanReadyEmailLog`` links the send through its ``email_delivery`` FK.
"""

import datetime
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailLog
from email_app.testing import deliver_pending_mail
from notifications.models import Notification
from plans.models import (
    PLAN_READY_EMAIL_STATUS_FAILED,
    PLAN_READY_EMAIL_STATUS_SENT,
    Plan,
    PlanReadyEmailLog,
    Sprint,
)
from plans.services import (
    PLAN_READY_EMAIL_PUBLIC_ERROR,
    preview_plan_ready_emails,
    send_plan_ready_email_for_plan,
    send_plan_ready_emails,
)

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

    def test_send_creates_side_effects_and_stamps_shared_at(self):
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
        self.assertIsNotNone(log.email_delivery)
        self.assertEqual(log.notification.notification_type, 'plan_shared')
        self.assertEqual(
            Notification.objects.filter(
                user=plan.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        # The audit EmailLog row lands from the worker under the
        # delivery's idempotency key; the legacy email_log FK stays null.
        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(user=plan.member, email_type='plan_shared').count(),
            1,
        )
        log.refresh_from_db()
        self.assertIsNone(log.email_log)

    def test_individual_send_creates_side_effects_and_result(self):
        plan = self._member_plan('solo@test.com')

        result = send_plan_ready_email_for_plan(plan, actor=self.staff)

        self.assertEqual(
            result,
            {
                'requested': True,
                'sent': True,
                'skipped_already_sent': False,
                'failed': False,
                'error': '',
            },
        )
        plan.refresh_from_db()
        self.assertIsNotNone(plan.shared_at)
        log = PlanReadyEmailLog.objects.get(plan=plan)
        self.assertEqual(log.status, PLAN_READY_EMAIL_STATUS_SENT)
        self.assertEqual(log.triggered_by, self.staff)
        self.assertEqual(log.notification.notification_type, 'plan_shared')
        self.assertIsNotNone(log.email_delivery)
        self.assertEqual(
            Notification.objects.filter(
                user=plan.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(
                user=plan.member, email_type='plan_shared',
            ).count(),
            1,
        )

    def test_individual_send_is_idempotent_for_successful_plan(self):
        plan = self._member_plan('solo@test.com')

        first = send_plan_ready_email_for_plan(plan, actor=self.staff)
        second = send_plan_ready_email_for_plan(plan, actor=self.staff)

        self.assertTrue(first['sent'])
        self.assertEqual(
            second,
            {
                'requested': True,
                'sent': False,
                'skipped_already_sent': True,
                'failed': False,
                'error': '',
            },
        )
        self.assertEqual(PlanReadyEmailLog.objects.filter(plan=plan).count(), 1)
        # Exactly one durable delivery was queued for the plan.
        self.assertEqual(
            EmailDelivery.objects.filter(
                recipient_user=plan.member, purpose='plan_shared',
            ).count(),
            1,
        )
        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(user=plan.member, email_type='plan_shared').count(),
            1,
        )

    @patch(
        'notifications.services.notification_service.send_package_mail',
    )
    def test_individual_failure_records_failed_log_and_keeps_plan_unshared(
        self,
        mock_send,
    ):
        mock_send.side_effect = RuntimeError('SES rejected solo@test.com')
        plan = self._member_plan('solo@test.com')

        result = send_plan_ready_email_for_plan(plan, actor=self.staff)

        self.assertTrue(result['requested'])
        self.assertFalse(result['sent'])
        self.assertTrue(result['failed'])
        self.assertEqual(result['error'], PLAN_READY_EMAIL_PUBLIC_ERROR)
        plan.refresh_from_db()
        self.assertIsNone(plan.shared_at)
        log = PlanReadyEmailLog.objects.get(plan=plan)
        self.assertEqual(log.status, PLAN_READY_EMAIL_STATUS_FAILED)
        self.assertIn('SES rejected solo@test.com', log.last_error)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)

    def test_individual_failed_log_remains_eligible_for_bulk_retry(self):
        from email_app.package_mail import send_package_mail

        plan = self._member_plan('solo@test.com')
        calls = []

        def flaky_send(user, template_name, context=None, **kwargs):
            calls.append(user.email)
            if len(calls) == 1:
                raise RuntimeError('SES down')
            return send_package_mail(user, template_name, context, **kwargs)

        with patch(
            'notifications.services.notification_service.send_package_mail',
            side_effect=flaky_send,
        ):
            failed = send_plan_ready_email_for_plan(plan, actor=self.staff)
        self.assertEqual(
            Notification.objects.filter(
                user=plan.member, notification_type='plan_shared',
            ).count(),
            0,
        )
        retried = send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        self.assertTrue(failed['failed'])
        self.assertEqual(retried['sent_count'], 1)
        plan.refresh_from_db()
        self.assertIsNotNone(plan.shared_at)
        self.assertEqual(
            PlanReadyEmailLog.objects.get(plan=plan).status,
            PLAN_READY_EMAIL_STATUS_SENT,
        )
        self.assertEqual(
            Notification.objects.filter(
                user=plan.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(
                user=plan.member, email_type='plan_shared',
            ).count(),
            1,
        )

    def test_second_send_skips_already_successful_plan(self):
        plan = self._member_plan('member@test.com')
        send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        second = send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        self.assertEqual(second['sent_count'], 0)
        self.assertEqual(second['skipped_already_sent_count'], 1)
        self.assertEqual(PlanReadyEmailLog.objects.filter(plan=plan).count(), 1)
        self.assertEqual(
            EmailDelivery.objects.filter(
                recipient_user=plan.member, purpose='plan_shared',
            ).count(),
            1,
        )
        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(user=plan.member, email_type='plan_shared').count(),
            1,
        )

    def test_existing_shared_at_is_not_moved_backward_or_forward(self):
        original = timezone.now() - datetime.timedelta(days=3)
        plan = self._member_plan('shared@test.com', shared_at=original)

        send_plan_ready_emails(sprint=self.sprint, actor=self.staff)

        plan.refresh_from_db()
        self.assertEqual(plan.shared_at, original)

    def test_one_failure_does_not_stop_remaining_recipients(self):
        from email_app.package_mail import send_package_mail

        good = self._member_plan('good@test.com')
        bad = self._member_plan('bad@test.com')
        other = self._member_plan('other@test.com')

        def send_side_effect(user, template_name, context=None, **kwargs):
            if user.email == 'bad@test.com':
                raise RuntimeError('SES rejected bad@test.com')
            return send_package_mail(user, template_name, context, **kwargs)

        with patch(
            'notifications.services.notification_service.send_package_mail',
            side_effect=send_side_effect,
        ):
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
        self.assertEqual(
            summary['failed'][0]['last_error'],
            PLAN_READY_EMAIL_PUBLIC_ERROR,
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
        self.assertFalse(
            Notification.objects.filter(user=bad.member).exists(),
        )

    @patch(
        'notifications.services.notification_service.send_package_mail',
    )
    def test_dry_run_has_no_side_effects(self, mock_send):
        plan = self._member_plan('member@test.com')

        summary = send_plan_ready_emails(
            sprint=self.sprint,
            actor=self.staff,
            dry_run=True,
        )

        self.assertEqual(summary['eligible_count'], 1)
        self.assertEqual(mock_send.call_count, 0)
        self.assertEqual(PlanReadyEmailLog.objects.count(), 0)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EmailDelivery.objects.count(), 0)
        self.assertEqual(EmailLog.objects.count(), 0)
        plan.refresh_from_db()
        self.assertIsNone(plan.shared_at)
