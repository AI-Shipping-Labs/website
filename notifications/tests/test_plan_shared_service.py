"""Tests for NotificationService.create_plan_shared() (issue #732)."""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import EmailLog
from notifications.models import Notification
from notifications.services.notification_service import NotificationService
from plans.models import Plan, Sprint

User = get_user_model()


@tag('core')
class CreatePlanSharedTest(TestCase):
    """Helper creates ONE bell row + ONE email per call, with no dedup."""

    @classmethod
    def setUpTestData(cls):
        cls.member = User.objects.create_user(
            email='member@test.com', password='pw',
        )
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_creates_notification_with_plan_shared_type(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        result = NotificationService.create_plan_shared(self.plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.notification_type, 'plan_shared')
        self.assertEqual(result.user, self.member)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_notification_url_points_to_my_plan_detail(self, mock_ses):
        """The bell URL must deep-link to the OWNER workspace
        (``my_plan_detail`` at ``/sprints/<slug>/plan/<id>``), NOT
        the cohort-board sibling (``member_plan_detail`` at
        ``/sprints/<slug>/plans/<id>``).
        """
        mock_ses.return_value = 'msg-1'
        notification = NotificationService.create_plan_shared(self.plan)
        expected = f'/sprints/may-2026/plan/{self.plan.pk}'
        self.assertEqual(notification.url, expected)
        # Explicitly check we did NOT use the read-only sibling.
        self.assertNotIn('/plans/', notification.url)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_title_mentions_sprint_name(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        notification = NotificationService.create_plan_shared(self.plan)
        self.assertIn('May 2026', notification.title)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_sends_plan_shared_email(self, mock_ses):
        mock_ses.return_value = 'msg-1'
        NotificationService.create_plan_shared(self.plan)
        log = EmailLog.objects.get(user=self.member, email_type='plan_shared')
        self.assertEqual(log.email_type, 'plan_shared')

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_reshare_creates_second_notification_and_email(self, mock_ses):
        """Re-share is allowed: each call creates a NEW bell + a NEW email
        log. There is NO dedup row (unlike create_event_reminder)."""
        mock_ses.return_value = 'msg-1'
        NotificationService.create_plan_shared(self.plan)
        NotificationService.create_plan_shared(self.plan)
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            2,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            2,
        )

    @patch('notifications.services.notification_service.logger.exception')
    @patch('email_app.services.email_service.EmailService.send')
    def test_ses_exception_does_not_unwind_bell(self, mock_send, mock_log_exc):
        """SES failures must NOT roll back the Notification row, must NOT
        propagate to the caller, and MUST be logged via logger.exception."""
        mock_send.side_effect = Exception('SES is down')

        notification = NotificationService.create_plan_shared(self.plan)

        # Bell row persisted despite the SES blow-up.
        self.assertIsNotNone(notification)
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        # No email log was created (the send raised before EmailLog row).
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            0,
        )
        # logger.exception WAS called so ops can chase the SES failure.
        self.assertTrue(mock_log_exc.called)

    @patch('email_app.services.email_service.EmailService._send_ses')
    def test_unsubscribed_user_still_receives_transactional(self, mock_ses):
        """``plan_shared`` is transactional: the recipient's ``unsubscribed``
        flag does NOT skip the send (same policy as event_reminder)."""
        mock_ses.return_value = 'msg-1'
        self.member.unsubscribed = True
        self.member.save(update_fields=['unsubscribed'])
        NotificationService.create_plan_shared(self.plan)
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            1,
        )


@tag('core')
class PlanSharedClassificationTest(TestCase):
    """``plan_shared`` must be transactional."""

    def test_plan_shared_in_transactional_set(self):
        from email_app.services.email_classification import (
            TRANSACTIONAL_EMAIL_TYPES,
        )

        self.assertIn('plan_shared', TRANSACTIONAL_EMAIL_TYPES)

    def test_classify_email_type_returns_transactional(self):
        from email_app.services.email_classification import (
            EMAIL_KIND_TRANSACTIONAL,
            classify_email_type,
        )

        self.assertEqual(
            classify_email_type('plan_shared'),
            EMAIL_KIND_TRANSACTIONAL,
        )
