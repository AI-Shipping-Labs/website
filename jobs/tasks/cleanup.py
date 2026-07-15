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


def cleanup_old_webhook_deliveries(days=30):
    """Delete terminal outbound jobs and attempt rows older than ``days``.

    Sibling of :func:`cleanup_old_webhook_logs` (issue #1070). Reuses the
    same scheduled-job wiring rather than a new bespoke cron. The outbound
    delivery log is observability data, so old rows are pruned on the same
    daily cadence as the inbound webhook log.

    Args:
        days: Number of days to keep. Deliveries older than this are deleted.

    Returns:
        dict with count of deleted records.
    """
    from triggers.models import WebhookDelivery, WebhookDeliveryJob

    cutoff = timezone.now() - timedelta(days=days)
    # Terminal durable jobs contain the snapshotted PII envelope and encrypted
    # signing key. Delete them first so their attempt rows cascade; preserve
    # pending/running/paused jobs until they reach a terminal state.
    terminal_jobs = WebhookDeliveryJob.objects.filter(
        status__in=[
            WebhookDeliveryJob.STATUS_SUCCEEDED,
            WebhookDeliveryJob.STATUS_FAILED,
        ],
        updated_at__lt=cutoff,
    )
    terminal_count = terminal_jobs.count()
    _, cascaded = terminal_jobs.delete()
    cascaded_deliveries = cascaded.get("triggers.WebhookDelivery", 0)
    legacy_deleted, _ = WebhookDelivery.objects.filter(
        job__isnull=True,
        created_at__lt=cutoff,
    ).delete()
    deleted_count = cascaded_deliveries + legacy_deleted

    logger.info(
        "Cleaned up %d webhook deliveries older than %d days",
        deleted_count,
        days,
    )
    return {
        'deleted': deleted_count,
        'deleted_jobs': terminal_count,
        'cutoff_days': days,
    }
