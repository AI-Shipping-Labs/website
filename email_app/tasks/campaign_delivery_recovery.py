"""Periodic recovery for snapshotted campaign deliveries."""

import ast
import logging

from django.db import transaction
from django.utils import timezone
from django_q.models import OrmQ, Schedule

from email_app.models import CampaignDelivery, EmailCampaign
from email_app.tasks.send_campaign import (
    _append_diagnostic,
    _chunk,
    _expire_stale_claim,
    _get_batch_size,
    get_max_delivery_attempts,
    refresh_campaign_status,
)
from jobs.tasks import build_task_name

logger = logging.getLogger(__name__)

BATCH_FUNC = 'email_app.tasks.send_campaign.send_campaign_batch'


def _parse_kwargs(raw):
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = ast.literal_eval(raw)
    except (ValueError, SyntaxError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _delivery_ids_from_kwargs(kwargs):
    ids = kwargs.get('delivery_ids') or []
    if not isinstance(ids, (list, tuple)):
        return []
    parsed = []
    for value in ids:
        try:
            parsed.append(int(value))
        except (TypeError, ValueError):
            continue
    return parsed


def live_batch_delivery_ids():
    """Return delivery IDs already covered by a live batch schedule or queue."""
    covered = set()
    for sched in Schedule.objects.filter(func=BATCH_FUNC).exclude(repeats=0):
        covered.update(_delivery_ids_from_kwargs(_parse_kwargs(sched.kwargs)))
    for row in OrmQ.objects.iterator():
        task = row.task
        if not isinstance(task, dict):
            continue
        if task.get('func') != BATCH_FUNC:
            continue
        covered.update(_delivery_ids_from_kwargs(task.get('kwargs') or {}))
    return covered


def _enqueue_delivery_chunks(campaign, delivery_ids, covered):
    orphans = [pk for pk in delivery_ids if pk not in covered]
    if not orphans:
        return 0
    batch_size = _get_batch_size()
    chunks = list(_chunk(orphans, batch_size))
    now = timezone.now()
    created = 0
    for index, chunk in enumerate(chunks):
        task_name = build_task_name(
            'Recover campaign batch',
            f'#{campaign.pk} {campaign.subject} recovery {index + 1}/{len(chunks)}',
            'campaign delivery recovery',
        )
        Schedule.objects.create(
            name=task_name,
            func=BATCH_FUNC,
            schedule_type=Schedule.ONCE,
            repeats=1,
            next_run=now,
            kwargs={
                'campaign_id': campaign.pk,
                'delivery_ids': chunk,
                'q_options': {'task_name': task_name},
            },
        )
        covered.update(chunk)
        created += 1
    return created


def _reset_failed_for_automatic_retry(delivery, *, max_attempts):
    if delivery.state != CampaignDelivery.State.FAILED:
        return False
    if delivery.attempt_count >= max_attempts:
        return False
    next_attempt = delivery.attempt_count + 1
    delivery.state = CampaignDelivery.State.PENDING
    delivery.claim_token = None
    delivery.claimed_at = None
    delivery.claim_expires_at = None
    delivery.completed_at = None
    delivery.last_error = _append_diagnostic(
        delivery.last_error,
        f'automatic retry {next_attempt}/{max_attempts}',
    )
    delivery.save(update_fields=[
        'state', 'claim_token', 'claimed_at', 'claim_expires_at',
        'completed_at', 'last_error', 'updated_at',
    ])
    return True


def recover_campaign_deliveries():
    """Expire stale claims, re-queue orphan pending work, and bound-retry failures."""
    now = timezone.now()
    touched_campaign_ids = set()
    expired = 0
    stale_ids = list(
        CampaignDelivery.objects.filter(
            state=CampaignDelivery.State.DISPATCHING,
            claim_expires_at__lte=now,
        ).values_list('pk', 'campaign_id')
    )
    for delivery_id, campaign_id in stale_ids:
        if _expire_stale_claim(delivery_id, now=now):
            expired += 1
            touched_campaign_ids.add(campaign_id)

    covered = live_batch_delivery_ids()
    pending_by_campaign = {}
    for campaign_id, delivery_id in (
        CampaignDelivery.objects.filter(state=CampaignDelivery.State.PENDING)
        .order_by('campaign_id', 'recipient_user_pk', 'pk')
        .values_list('campaign_id', 'pk')
    ):
        pending_by_campaign.setdefault(campaign_id, []).append(delivery_id)

    requeued = 0
    campaigns = EmailCampaign.objects.in_bulk(pending_by_campaign.keys())
    for campaign_id, delivery_ids in pending_by_campaign.items():
        campaign = campaigns.get(campaign_id)
        if campaign is None:
            continue
        created = _enqueue_delivery_chunks(campaign, delivery_ids, covered)
        if created:
            requeued += created
            touched_campaign_ids.add(campaign_id)

    max_attempts = get_max_delivery_attempts()
    auto_retried = 0
    failed_ids = list(
        CampaignDelivery.objects.filter(
            state=CampaignDelivery.State.FAILED,
            attempt_count__lt=max_attempts,
        ).order_by('campaign_id', 'recipient_user_pk', 'pk')
        .values_list('pk', flat=True)
    )
    retried_by_campaign = {}
    for delivery_id in failed_ids:
        with transaction.atomic():
            delivery = (
                CampaignDelivery.objects.select_for_update()
                .select_related('campaign')
                .filter(pk=delivery_id)
                .first()
            )
            if delivery is None:
                continue
            if not _reset_failed_for_automatic_retry(
                delivery, max_attempts=max_attempts,
            ):
                continue
            retried_by_campaign.setdefault(delivery.campaign, []).append(delivery.pk)
            auto_retried += 1
            touched_campaign_ids.add(delivery.campaign_id)

    for campaign, delivery_ids in retried_by_campaign.items():
        requeued += _enqueue_delivery_chunks(campaign, delivery_ids, covered)

    touched_campaign_ids.update(
        EmailCampaign.objects.filter(
            audience_snapshotted_at__isnull=False,
            status='sending',
        ).values_list('pk', flat=True)
    )
    for campaign_id in touched_campaign_ids:
        refresh_campaign_status(campaign_id)

    return {
        'expired_claims': expired,
        'requeued_batches': requeued,
        'automatic_retries': auto_retried,
        'campaigns': len(touched_campaign_ids),
    }
