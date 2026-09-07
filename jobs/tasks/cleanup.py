"""
Cleanup tasks for removing old data.
"""

import logging
from datetime import timedelta
from time import monotonic

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)

EXPIRED_SESSION_BATCH_SIZE = 1000
EXPIRED_SESSION_TIME_BUDGET_SECONDS = 240
SESSION_ACCOUNT_ID_BACKFILL_BATCH_SIZE = 1000


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
    ).exclude(service='calendly').delete()

    logger.info("Cleaned up %d processed webhook logs older than %d days", deleted_count, days)
    return {'deleted': deleted_count, 'cutoff_days': days}


def cleanup_calendly_webhook_logs():
    """Apply the Studio-configured retention window to processed Calendly PII."""
    from community.calendly_config import calendly_webhook_retention_days
    from integrations.models import WebhookLog

    days = calendly_webhook_retention_days()
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = WebhookLog.objects.filter(
        service='calendly', processed=True, received_at__lt=cutoff,
    ).delete()
    return {'deleted': deleted, 'cutoff_days': days}


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


def redact_old_maven_enrollment_pii(days=30):
    """Redact Maven occurrence email and legacy payload PII after ``days``."""
    from integrations.models import MavenEnrollmentEvent

    cutoff = timezone.now() - timedelta(days=days)
    redacted = MavenEnrollmentEvent.objects.filter(
        created_at__lt=cutoff,
        payload_redacted_at__isnull=True,
    ).update(email='', payload={}, payload_redacted_at=timezone.now())
    logger.info("Redacted %d Maven occurrences older than %d days", redacted, days)
    return {'redacted': redacted, 'cutoff_days': days}


def retry_maven_enrollment_steps(limit=100):
    """Retry incomplete Maven side effects, bounded by their persisted attempts."""
    from integrations.models import MavenEnrollmentEvent
    from integrations.services.maven import MAX_STEP_ATTEMPTS, run_occurrence_steps

    retryable = Q()
    for name in ("override", "notification", "slack", "welcome", "removal"):
        retryable |= Q(
            **{
                f"{name}_status__in": [
                    MavenEnrollmentEvent.STEP_PENDING,
                    MavenEnrollmentEvent.STEP_FAILED,
                    MavenEnrollmentEvent.STEP_RUNNING,
                ],
                f"{name}_attempts__lt": MAX_STEP_ATTEMPTS,
            }
        )
    occurrences = list(
        MavenEnrollmentEvent.objects.filter(retryable)
        .select_related("user")
        .order_by("updated_at")[:limit]
    )
    for occurrence in occurrences:
        run_occurrence_steps(occurrence)
    logger.info("Processed %d retryable Maven enrollment occurrences", len(occurrences))
    return {"processed": len(occurrences), "limit": limit}


def clear_expired_sessions():
    """Delete expired database sessions in bounded batches.

    Uses the `expire_date` index and never decodes `session_data`. Returns
    success with counts even when the wall-clock budget is hit so django-q
    does not retry an oversized pass.
    """
    from accounts.models import AccountSession
    from accounts.session_backend import account_id_from_session_data

    started = monotonic()
    now = timezone.now()
    deleted = 0
    while monotonic() - started < EXPIRED_SESSION_TIME_BUDGET_SECONDS:
        session_keys = list(
            AccountSession.objects.filter(expire_date__lt=now).values_list(
                "session_key",
                flat=True,
            )[:EXPIRED_SESSION_BATCH_SIZE]
        )
        if not session_keys:
            break
        batch_deleted, _ = AccountSession.objects.filter(
            session_key__in=session_keys,
        ).delete()
        deleted += batch_deleted
        if len(session_keys) < EXPIRED_SESSION_BATCH_SIZE:
            break

    remaining_expired = AccountSession.objects.filter(expire_date__lt=now).count()
    backfilled = 0
    if monotonic() - started < EXPIRED_SESSION_TIME_BUDGET_SECONDS:
        backfilled = _backfill_session_account_ids(
            now=now,
            limit=SESSION_ACCOUNT_ID_BACKFILL_BATCH_SIZE,
            account_id_from_session_data=account_id_from_session_data,
        )

    logger.info(
        "Cleared %d expired sessions (%d remaining expired, %d account_id backfilled)",
        deleted,
        remaining_expired,
        backfilled,
    )
    return {
        "deleted": deleted,
        "remaining_expired": remaining_expired,
        "backfilled": backfilled,
    }


def _backfill_session_account_ids(now, limit, account_id_from_session_data):
    from accounts.models import AccountSession

    rows = list(
        AccountSession.objects.filter(
            account_id__isnull=True,
            expire_date__gte=now,
        )[:limit]
    )
    updated = 0
    for session in rows:
        try:
            account_id = account_id_from_session_data(session.get_decoded())
        except Exception:
            continue
        if account_id is None:
            continue
        updated += AccountSession.objects.filter(
            session_key=session.session_key,
            account_id__isnull=True,
        ).update(account_id=account_id)
    return updated
