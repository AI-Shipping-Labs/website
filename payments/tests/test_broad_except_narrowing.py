"""Characterization tests for issue #605 broad-except narrowing.

This file pins the "log and swallow" behavior we explicitly preserved
when narrowing ``except Exception`` clauses in:

- ``payments/services/webhook_handlers.py``
- ``payments/services/community_hooks.py``

Each test forces a specific exception type that the narrowed (or
intentionally broad) catch must still swallow without propagating to the
webhook caller. If a future refactor accidentally re-broadens or
re-narrows the catch in a way that changes which errors get swallowed,
these tests fail loudly.
"""

from smtplib import SMTPException
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, tag

from accounts.models import User
from payments.models import Tier
from payments.services import _send_payment_notification_email
from payments.services.community_hooks import (
    _community_invite,
    _community_reactivate,
    _community_remove,
    _community_schedule_removal,
)
from payments.services.webhook_handlers import (
    _handle_course_purchase,
    handle_checkout_completed,
)


@tag('core')
class PaymentNotificationEmailNarrowedCatchTest(TestCase):
    """``_send_payment_notification_email`` swallows mail-transport errors only.

    Before #605 this helper caught ``Exception``. It now catches
    ``(BadHeaderError, OSError, SMTPException)`` — the same narrowed
    surface ``handle_invoice_payment_failed`` already uses. The test
    asserts the narrowed set still keeps the webhook caller alive.
    """

    def setUp(self):
        # Configure a recipient so the helper attempts to send.
        patcher = patch(
            'payments.services.get_config', return_value='ops@example.com',
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user = User.objects.create_user(email='buyer@test.com')
        self.tier = Tier.objects.get(slug='main')

    def _invoke(self):
        _send_payment_notification_email(
            event_id='cs_test_605',
            user=self.user,
            was_new_user=False,
            tier=self.tier,
            previous_tier=None,
            course=None,
            stripe_customer_id='cus_test_605',
        )

    @patch('payments.services.send_mail')
    def test_smtp_exception_is_swallowed(self, mock_send_mail):
        mock_send_mail.side_effect = SMTPException('smtp down')

        # Must not raise — the user already paid; a missed operator
        # notification is a logging concern.
        with patch('payments.services.logger') as mock_logger:
            self._invoke()
        mock_logger.warning.assert_called_once()

    @patch('payments.services.send_mail')
    def test_os_error_is_swallowed(self, mock_send_mail):
        # Connection-level failure surfaces as ``OSError`` (broken pipe,
        # DNS failure, etc.). Stays in the narrowed catch set.
        mock_send_mail.side_effect = OSError('connection reset')

        with patch('payments.services.logger') as mock_logger:
            self._invoke()
        mock_logger.warning.assert_called_once()


@tag('core')
class CommunityHookIntentionalBroadCatchTest(TestCase):
    """``community_hooks._community_*`` keep an intentional broad catch.

    Failure to enqueue a community task must NEVER propagate back into
    the webhook caller — the payment already happened. These hooks
    therefore stay broad and are documented as such; the tests pin that
    contract with a representative non-DB exception type (the django-q
    enqueue path can fail with broker / Redis / serialization errors).
    """

    def setUp(self):
        self.user = User.objects.create_user(email='community@test.com')

    @patch('payments.services.community_hooks.logger', create=True)
    @patch('jobs.tasks.async_task')
    def test_invite_swallows_runtime_error(self, mock_async_task, _logger):
        mock_async_task.side_effect = RuntimeError('redis unreachable')

        # Importing ``payments.services`` re-exports the logger; the
        # helper writes through the package namespace.
        with patch('payments.services.logger') as mock_logger:
            _community_invite(self.user)  # Must not raise.
        mock_logger.exception.assert_called_once()

    @patch('jobs.tasks.async_task')
    def test_reactivate_swallows_integrity_error(self, mock_async_task):
        mock_async_task.side_effect = IntegrityError('schedule clash')

        with patch('payments.services.logger') as mock_logger:
            _community_reactivate(self.user)
        mock_logger.exception.assert_called_once()

    @patch('jobs.tasks.async_task')
    def test_remove_swallows_runtime_error(self, mock_async_task):
        mock_async_task.side_effect = RuntimeError('broker offline')

        with patch('payments.services.logger') as mock_logger:
            _community_remove(self.user)
        mock_logger.exception.assert_called_once()

    @patch('jobs.tasks.async_task')
    def test_schedule_removal_swallows_runtime_error(self, mock_async_task):
        mock_async_task.side_effect = RuntimeError('serialization error')

        with patch('payments.services.logger') as mock_logger:
            _community_schedule_removal(self.user)
        mock_logger.exception.assert_called_once()


@tag('core')
class WebhookAttributionIntentionalBroadCatchTest(TestCase):
    """Attribution failures must never undo a tier/course update.

    ``handle_checkout_completed`` and ``_handle_course_purchase`` keep
    an intentional ``except Exception`` around
    ``_record_conversion_attribution`` for this reason. A DB-level
    failure inside the helper must NOT propagate, otherwise Stripe will
    retry the webhook and the already-committed tier change will keep
    re-triggering downstream effects.
    """

    def setUp(self):
        patchers = [
            patch('payments.services._get_subscription_period_end', return_value=None),
            patch('payments.services._get_subscription_price_id', return_value=''),
            patch('payments.services._tier_from_subscription', return_value=None),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.tier = Tier.objects.get(slug='main')

    @patch('payments.services._record_conversion_attribution')
    def test_handle_checkout_swallows_attribution_integrity_error(
        self, mock_record,
    ):
        mock_record.side_effect = IntegrityError(
            'duplicate stripe_session_id',
        )
        # Stub mail so the test doesn't hit the SMTP path.
        with patch('payments.services.send_mail'), \
             patch('payments.services.get_config', return_value=''):
            session_data = {
                'id': 'cs_test_attr',
                'customer': 'cus_attr',
                'subscription': 'sub_attr',
                'customer_details': {'email': 'attr@test.com'},
                'metadata': {'tier_slug': self.tier.slug},
            }
            # The webhook handler must complete without raising even
            # though attribution failed — the user gets the tier.
            handle_checkout_completed(session_data)

        user = User.objects.get(email='attr@test.com')
        self.assertEqual(user.tier, self.tier)

    @patch('payments.services._record_conversion_attribution')
    def test_course_purchase_swallows_attribution_integrity_error(
        self, mock_record,
    ):
        from content.models import Course, CourseAccess

        mock_record.side_effect = IntegrityError(
            'duplicate stripe_session_id',
        )
        user = User.objects.create_user(email='coursebuy@test.com')
        course = Course.objects.create(
            slug='broad-except-test-course',
            title='Broad except test course',
            individual_price_eur=42,
        )
        with patch('payments.services.send_mail'), \
             patch('payments.services.get_config', return_value=''):
            _handle_course_purchase(
                {
                    'id': 'cs_course_attr',
                    'customer': 'cus_course_attr',
                    'customer_details': {'email': user.email},
                    'metadata': {'course_id': course.pk},
                },
                course.pk,
            )

        # CourseAccess is the source of truth — must have been written
        # despite the attribution failure.
        self.assertTrue(
            CourseAccess.objects.filter(user=user, course=course).exists(),
        )
