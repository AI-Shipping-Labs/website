"""Background delivery for onboarding completion side effects (#821)."""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from questionnaires.models import OnboardingTurnAttempt

logger = logging.getLogger(__name__)
NOTIFICATION_LEASE_SECONDS = 300


def send_onboarding_staff_notification(attempt_id):
    """Deliver once per successful final attempt, with retryable failure state."""
    with transaction.atomic():
        attempt = (
            OnboardingTurnAttempt.objects.select_for_update()
            .select_related('conversation__response__respondent')
            .filter(pk=attempt_id)
            .first()
        )
        if attempt is None:
            return {'status': 'skipped', 'reason': 'missing_attempt'}
        if attempt.notification_status == 'succeeded':
            return {'status': 'skipped', 'reason': 'already_succeeded'}
        now = timezone.now()
        if (
            attempt.notification_status == 'processing'
            and attempt.notification_lease_expires_at
            and attempt.notification_lease_expires_at > now
        ):
            return {'status': 'skipped', 'reason': 'already_processing'}
        if attempt.status != 'succeeded' or attempt.outcome != 'final':
            return {'status': 'skipped', 'reason': 'turn_not_final'}
        attempt.notification_status = 'processing'
        attempt.notification_attempt_count += 1
        attempt.notification_lease_expires_at = now + timedelta(
            seconds=NOTIFICATION_LEASE_SECONDS,
        )
        attempt.notification_last_error = ''
        attempt.save(update_fields=[
            'notification_status', 'notification_attempt_count',
            'notification_lease_expires_at', 'notification_last_error',
            'updated_at',
        ])

    try:
        from crm.services.onboarding_notify import (  # noqa: PLC0415
            notify_staff_onboarding_submitted,
        )
        notify_staff_onboarding_submitted(
            attempt.conversation.response.respondent,
        )
    except Exception as exc:
        OnboardingTurnAttempt.objects.filter(pk=attempt_id).update(
            notification_status='failed',
            notification_lease_expires_at=None,
            notification_last_error=type(exc).__name__[:120],
        )
        raise

    OnboardingTurnAttempt.objects.filter(pk=attempt_id).update(
        notification_status='succeeded',
        notification_lease_expires_at=None,
        notification_last_error='',
    )
    return {'status': 'succeeded', 'attempt_id': attempt_id}


def reconcile_onboarding_staff_notifications(limit=100):
    """Retry durable pending/failed rows and reclaim expired worker leases."""
    now = timezone.now()
    attempt_ids = list(
        OnboardingTurnAttempt.objects.filter(
            status='succeeded',
            outcome='final',
        ).filter(
            Q(notification_status__in={'pending', 'failed'})
            | Q(
                notification_status='processing',
                notification_lease_expires_at__lte=now,
            )
            | Q(
                notification_status='processing',
                notification_lease_expires_at__isnull=True,
            )
        ).order_by('completed_at').values_list('pk', flat=True)[:limit]
    )
    delivered = 0
    failed = 0
    for attempt_id in attempt_ids:
        try:
            result = send_onboarding_staff_notification(attempt_id)
            delivered += result.get('status') == 'succeeded'
        except Exception:  # noqa: BLE001 - next scheduled sweep retries it
            failed += 1
            logger.exception(
                'Onboarding staff notification reconciliation failed',
                extra={'onboarding_attempt_id': attempt_id},
            )
    return {
        'eligible': len(attempt_ids),
        'delivered': delivered,
        'failed': failed,
    }
