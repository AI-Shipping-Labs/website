"""Tests for NotificationService.create_plan_shared() (issue #732).

A1.2 slice 4: the email goes out as a durable ``EmailDelivery`` through
``send_package_mail``; the worker mints the plan link from the saved plan
(issue #1613) and writes the ``EmailLog`` audit row after provider
acceptance. Tests drain pending deliveries with
``email_app.testing.deliver_pending_mail`` and assert on the rows and the
rendered payload captured by the stub SES client.
"""

import datetime
from unittest.mock import patch

from community_base.mail.models import EmailDelivery
from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import EmailLog
from email_app.testing import StubSESClient, deliver_pending_mail
from notifications.models import Notification
from notifications.services.notification_service import NotificationService
from plans.models import Plan, Sprint

User = get_user_model()


def _drain_with_stub():
    """Run pending deliveries through a fresh stub client; return it."""

    stub = StubSESClient()
    with patch(
        'community_base.mail.backends.ses_local.configured_client',
        return_value=stub,
    ):
        deliver_pending_mail()
    return stub


def _rendered_html(stub):
    return stub.calls[0]['Content']['Simple']['Body']['Html']['Data']


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

    def test_creates_notification_with_plan_shared_type(self):
        result = NotificationService.create_plan_shared(self.plan)
        self.assertIsNotNone(result)
        self.assertEqual(result.notification_type, 'plan_shared')
        self.assertEqual(result.user, self.member)

    def test_notification_url_points_to_my_plan_detail(self):
        """The bell URL must deep-link to the OWNER workspace
        (``my_plan_detail`` at ``/sprints/<slug>/plan/<id>``), NOT
        the cohort-board sibling (``member_plan_detail`` at
        ``/sprints/<slug>/plans/<id>``).
        """
        notification = NotificationService.create_plan_shared(self.plan)
        expected = f'/sprints/may-2026/plan/{self.plan.pk}'
        self.assertEqual(notification.url, expected)
        # Explicitly check we did NOT use the read-only sibling.
        self.assertNotIn('/plans/', notification.url)

    def test_title_mentions_sprint_name(self):
        notification = NotificationService.create_plan_shared(self.plan)
        self.assertIn('May 2026', notification.title)

    def test_queues_durable_plan_shared_delivery(self):
        NotificationService.create_plan_shared(self.plan)

        delivery = EmailDelivery.objects.get(
            recipient_user=self.member, purpose='plan_shared',
        )
        # Issue #1613: the stored context is empty; the worker rebuilds
        # sprint name and the plan link from the related plan.
        self.assertEqual(delivery.context_data, {})
        self.assertEqual(delivery.related_object_type, 'plans.plan')
        self.assertEqual(
            str(delivery.related_object_id), str(self.plan.pk),
        )
        self.assertEqual(delivery.state, EmailDelivery.State.PENDING)

        deliver_pending_mail()
        log = EmailLog.objects.get(user=self.member, email_type='plan_shared')
        self.assertEqual(log.email_type, 'plan_shared')
        self.assertEqual(log.dedupe_key, delivery.idempotency_key)

    def test_plan_shared_email_copy_mentions_review_and_edit(self):
        from integrations.config import site_base_url

        NotificationService.create_plan_shared(self.plan)
        stub = _drain_with_stub()

        self.assertEqual(len(stub.calls), 1)
        body_html = _rendered_html(stub)

        self.assertIn('ready for you to review and edit', body_html)
        self.assertIn(
            f'{site_base_url()}/sprints/may-2026/plan/{self.plan.pk}',
            body_html,
        )
        self.assertIn('Review and edit your plan', body_html)

    def test_reshare_creates_second_notification_and_email(self):
        """Re-share is allowed: each call creates a NEW bell + a NEW
        delivery with its own idempotency key. There is NO dedup row
        (unlike create_event_reminder)."""
        NotificationService.create_plan_shared(self.plan)
        NotificationService.create_plan_shared(self.plan)
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            2,
        )
        deliveries = list(
            EmailDelivery.objects.filter(
                recipient_user=self.member, purpose='plan_shared',
            )
        )
        self.assertEqual(len(deliveries), 2)
        # Unique per-call keys are what keep the re-share re-sending; a
        # deterministic key would collapse the second share into the first.
        self.assertNotEqual(
            deliveries[0].idempotency_key,
            deliveries[1].idempotency_key,
        )

        deliver_pending_mail()
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            2,
        )

    @patch('notifications.services.notification_service.logger.exception')
    @patch(
        'notifications.services.notification_service.send_package_mail',
    )
    def test_send_refusal_does_not_unwind_bell(
        self, mock_send, mock_log_exc,
    ):
        """Local refusals must NOT roll back the Notification row, must NOT
        propagate to the caller, and MUST be logged via logger.exception."""
        mock_send.side_effect = Exception('SES is down')

        notification = NotificationService.create_plan_shared(self.plan)

        # Bell row persisted despite the refused send.
        self.assertIsNotNone(notification)
        self.assertEqual(
            Notification.objects.filter(
                user=self.member, notification_type='plan_shared',
            ).count(),
            1,
        )
        # No delivery and no email log were created (the send raised).
        self.assertEqual(
            EmailDelivery.objects.filter(
                recipient_user=self.member, purpose='plan_shared',
            ).count(),
            0,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.member, email_type='plan_shared',
            ).count(),
            0,
        )
        # logger.exception WAS called so ops can chase the refusal.
        self.assertTrue(mock_log_exc.called)

    def test_unsubscribed_user_still_receives_transactional(self):
        """``plan_shared`` is transactional: the recipient's ``unsubscribed``
        flag does NOT skip the send (same policy as event_reminder)."""
        self.member.unsubscribed = True
        self.member.save(update_fields=['unsubscribed'])
        NotificationService.create_plan_shared(self.plan)
        self.assertEqual(
            EmailDelivery.objects.filter(
                recipient_user=self.member, purpose='plan_shared',
            ).count(),
            1,
        )
        deliver_pending_mail()
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
