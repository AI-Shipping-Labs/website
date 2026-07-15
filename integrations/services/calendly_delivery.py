"""Durable processing/replay for accepted Calendly deliveries."""

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from community.services.calendly import process_webhook
from integrations.models import WebhookLog

SAFE_ERROR_MESSAGES = {
    'database_error': 'Delivery processing failed (database_error). Retry is safe.',
    'invalid_delivery': 'Delivery processing failed (invalid_delivery). Review the provider payload.',
    'processing_error': 'Delivery processing failed (processing_error). Retry is safe.',
}


def safe_error_category(exc):
    """Return a bounded operator category without retaining exception detail."""
    if isinstance(exc, DatabaseError):
        return 'database_error'
    if isinstance(exc, (KeyError, TypeError, ValueError, ValidationError)):
        return 'invalid_delivery'
    return 'processing_error'


def process_calendly_delivery(log_id):
    failure = None
    with transaction.atomic():
        # Claim one delivery at a time across provider retries, the scheduled
        # retry worker, and manual replay.
        log = WebhookLog.objects.select_for_update().get(
            pk=log_id, service='calendly',
        )
        if log.processed:
            return 'already_processed'
        log.attempts += 1
        try:
            process_webhook(log.payload)
        except Exception as exc:
            failure = exc
            log.error_message = SAFE_ERROR_MESSAGES[safe_error_category(exc)]
            log.save(update_fields=['attempts', 'error_message'])
        else:
            log.processed = True
            log.processed_at = timezone.now()
            log.error_message = ''
            log.save(update_fields=[
                'attempts', 'processed', 'processed_at', 'error_message',
            ])
    if failure is not None:
        raise failure
    return 'processed'


def retry_failed_calendly_deliveries(*, limit=100):
    ids = list(
        WebhookLog.objects.filter(service='calendly', processed=False)
        .order_by('received_at').values_list('pk', flat=True)[:limit]
    )
    processed = failed = 0
    for log_id in ids:
        try:
            process_calendly_delivery(log_id)
            processed += 1
        except Exception:  # worker records the durable per-delivery error
            failed += 1
    return {'processed': processed, 'failed': failed}
