"""
Cleanup tasks for removing old data.
"""

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def cleanup_old_webhook_logs(days=30):
    """
    Delete WebhookLog entries older than the given number of days.

    This is an example recurring job that can be scheduled to run daily
    to keep the webhook_log table from growing indefinitely.

    Args:
        days: Number of days to keep. Logs older than this are deleted.

    Returns:
        dict with count of deleted records.
    """
    from integrations.models import WebhookLog

    cutoff = timezone.now() - timedelta(days=days)
    deleted_count, _ = WebhookLog.objects.filter(
        received_at__lt=cutoff,
        processed=True,
    ).delete()

    logger.info("Cleaned up %d processed webhook logs older than %d days", deleted_count, days)
    return {'deleted': deleted_count, 'cutoff_days': days}
