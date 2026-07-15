from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from integrations.models import WebhookLog
from integrations.services.calendly_delivery import process_calendly_delivery

User = get_user_model()


@tag('core')
class CalendlyDeliveryClaimTest(TestCase):
    def test_processing_claims_row_and_processed_replay_is_noop(self):
        log = WebhookLog.objects.create(
            service='calendly', event_type='ignored.test', payload={},
        )
        original = WebhookLog.objects.select_for_update
        with patch.object(
            WebhookLog.objects, 'select_for_update', wraps=original,
        ) as claim, patch(
            'integrations.services.calendly_delivery.process_webhook',
        ) as dispatch:
            self.assertEqual(process_calendly_delivery(log.pk), 'processed')
            self.assertEqual(
                process_calendly_delivery(log.pk), 'already_processed',
            )
        self.assertEqual(claim.call_count, 2)
        dispatch.assert_called_once_with({})

    def test_failure_persists_only_safe_category_without_exception_detail(self):
        secret_marker = 'member@example.test?token=private-secret'
        log = WebhookLog.objects.create(
            service='calendly', event_type='invitee.created', payload={},
        )
        with patch(
            'integrations.services.calendly_delivery.process_webhook',
            side_effect=RuntimeError(secret_marker),
        ):
            with self.assertRaises(RuntimeError):
                process_calendly_delivery(log.pk)
        log.refresh_from_db()
        self.assertEqual(
            log.error_message,
            'Delivery processing failed (processing_error). Retry is safe.',
        )
        self.assertNotIn(secret_marker, log.error_message)

    def test_admin_renders_safe_category_without_exception_detail(self):
        secret_marker = 'member@example.test?token=private-secret'
        staff = User.objects.create_superuser(
            email='calendly-admin@test.com', password='pw',
        )
        log = WebhookLog.objects.create(
            service='calendly', event_type='invitee.created', payload={},
        )
        with patch(
            'integrations.services.calendly_delivery.process_webhook',
            side_effect=RuntimeError(secret_marker),
        ):
            with self.assertRaises(RuntimeError):
                process_calendly_delivery(log.pk)
        self.client.force_login(staff)
        response = self.client.get(reverse(
            'admin:integrations_webhooklog_change', args=[log.pk],
        ))
        self.assertContains(response, 'processing_error')
        self.assertNotContains(response, secret_marker)
