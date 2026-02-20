"""
Tests for the example task functions (cleanup, health check).
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from integrations.models import WebhookLog
from jobs.tasks.cleanup import cleanup_old_webhook_logs
from jobs.tasks.healthcheck import health_check


class HealthCheckTaskTest(TestCase):
    """Tests for the health_check task."""

    def test_health_check_returns_ok(self):
        """health_check returns status ok."""
        result = health_check()
        self.assertEqual(result['status'], 'ok')
        self.assertIn('timestamp', result)

    def test_health_check_returns_timestamp(self):
        """health_check returns a valid ISO timestamp."""
        result = health_check()
        # Should be parseable as ISO format
        self.assertIsInstance(result['timestamp'], str)
        self.assertIn('T', result['timestamp'])


class CleanupOldWebhookLogsTaskTest(TestCase):
    """Tests for the cleanup_old_webhook_logs task."""

    def test_deletes_old_processed_logs(self):
        """Old processed logs are deleted."""
        old_time = timezone.now() - timedelta(days=31)
        log = WebhookLog.objects.create(
            service='stripe',
            event_type='payment.success',
            processed=True,
        )
        # Manually update received_at since it's auto_now_add
        WebhookLog.objects.filter(pk=log.pk).update(received_at=old_time)

        result = cleanup_old_webhook_logs(days=30)
        self.assertEqual(result['deleted'], 1)
        self.assertFalse(WebhookLog.objects.filter(pk=log.pk).exists())

    def test_keeps_recent_processed_logs(self):
        """Recent processed logs are not deleted."""
        log = WebhookLog.objects.create(
            service='stripe',
            event_type='payment.success',
            processed=True,
        )
        result = cleanup_old_webhook_logs(days=30)
        self.assertEqual(result['deleted'], 0)
        self.assertTrue(WebhookLog.objects.filter(pk=log.pk).exists())

    def test_keeps_old_unprocessed_logs(self):
        """Old unprocessed logs are not deleted (they may still need processing)."""
        old_time = timezone.now() - timedelta(days=31)
        log = WebhookLog.objects.create(
            service='stripe',
            event_type='payment.success',
            processed=False,
        )
        WebhookLog.objects.filter(pk=log.pk).update(received_at=old_time)

        result = cleanup_old_webhook_logs(days=30)
        self.assertEqual(result['deleted'], 0)
        self.assertTrue(WebhookLog.objects.filter(pk=log.pk).exists())

    def test_custom_days_parameter(self):
        """Cleanup respects custom days parameter."""
        old_time = timezone.now() - timedelta(days=8)
        log = WebhookLog.objects.create(
            service='zoom',
            event_type='recording.completed',
            processed=True,
        )
        WebhookLog.objects.filter(pk=log.pk).update(received_at=old_time)

        # With days=7, this log should be deleted
        result = cleanup_old_webhook_logs(days=7)
        self.assertEqual(result['deleted'], 1)

    def test_returns_count_and_cutoff(self):
        """Cleanup returns both deleted count and cutoff days."""
        result = cleanup_old_webhook_logs(days=14)
        self.assertEqual(result['deleted'], 0)
        self.assertEqual(result['cutoff_days'], 14)

    def test_deletes_multiple_old_logs(self):
        """Multiple old processed logs are all deleted."""
        old_time = timezone.now() - timedelta(days=60)
        for i in range(5):
            log = WebhookLog.objects.create(
                service='stripe',
                event_type=f'event.{i}',
                processed=True,
            )
            WebhookLog.objects.filter(pk=log.pk).update(received_at=old_time)

        # Also create some recent logs that should be kept
        for i in range(3):
            WebhookLog.objects.create(
                service='stripe',
                event_type=f'recent.{i}',
                processed=True,
            )

        result = cleanup_old_webhook_logs(days=30)
        self.assertEqual(result['deleted'], 5)
        self.assertEqual(WebhookLog.objects.count(), 3)
