"""Transactional campaign claim and operator reconciliation commands."""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from email_app.models import CampaignDelivery, CampaignWave, EmailCampaign


@dataclass(frozen=True)
class CampaignClaimResult:
    claimed: bool
    campaign: EmailCampaign
    task_id: str | None = None


class CampaignDeliveryConflict(Exception):
    pass


def claim_and_enqueue_campaign(
    campaign_id, *, source, actor=None, scheduled_due_by=None,
):
    """Commit the campaign claim and ORM-broker enqueue together."""
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if not _campaign_can_be_claimed(campaign, scheduled_due_by):
            return CampaignClaimResult(False, campaign)
        _mark_campaign_sending(campaign)
        task_id = _enqueue_campaign(campaign, actor=actor, source=source)
        return CampaignClaimResult(True, campaign, task_id)


def _campaign_can_be_claimed(campaign, scheduled_due_by):
    if campaign.status != 'draft':
        return False
    if scheduled_due_by is None:
        return True
    if campaign.scheduled_at is None:
        return False
    return campaign.scheduled_at <= scheduled_due_by


def _mark_campaign_sending(campaign):
    campaign.status = 'sending'
    campaign.sent_count = 0
    campaign.sent_at = None
    # Claiming consumes the schedule. Manual send beats the dispatcher.
    campaign.scheduled_at = None
    campaign.scheduled_timezone = None
    campaign.save(update_fields=[
        'status', 'sent_count', 'sent_at', 'scheduled_at',
        'scheduled_timezone',
    ])


def _enqueue_campaign(campaign, *, actor, source):
    from jobs.tasks import async_task, build_task_name

    task_kwargs = {"campaign_id": campaign.pk}
    if actor is not None:
        task_kwargs["released_by_id"] = actor.pk
    task_id = async_task(
        'email_app.tasks.send_campaign.send_campaign',
        **task_kwargs,
        task_name=build_task_name(
            'Send campaign',
            f'#{campaign.pk} {campaign.subject}',
            source,
        ),
    )
    if task_id:
        return str(task_id)
    return None


def retry_delivery(delivery_id, *, actor, source="Studio campaign reconciliation"):
    """Attribute and durably queue one explicit operator retry."""
    from jobs.tasks import async_task, build_task_name

    with transaction.atomic():
        # ``of=('self',)``: ``wave`` is nullable, so ``select_related`` is a
        # LEFT OUTER JOIN and PostgreSQL refuses FOR UPDATE on it.
        delivery = (
            CampaignDelivery.objects.select_for_update(of=('self',))
            .select_related('campaign', 'wave')
            .get(pk=delivery_id)
        )
        if delivery.state not in {
            CampaignDelivery.State.FAILED,
            CampaignDelivery.State.AMBIGUOUS,
        }:
            raise CampaignDeliveryConflict('Delivery is no longer retryable.')
        prior_state = delivery.state
        delivery.state = CampaignDelivery.State.PENDING
        delivery.claim_token = None
        delivery.claimed_at = None
        delivery.claim_expires_at = None
        delivery.completed_at = None
        delivery.resolution = CampaignDelivery.Resolution.RETRY
        delivery.resolved_at = timezone.now()
        delivery.resolved_by = actor
        delivery.last_error = (
            f'{delivery.last_error} [operator retry from {prior_state}]'
        )[:500]
        delivery.save(update_fields=[
            'state', 'claim_token', 'claimed_at', 'claim_expires_at',
            'completed_at', 'resolution', 'resolved_at', 'resolved_by',
            'last_error', 'updated_at',
        ])
        if delivery.wave_id:
            CampaignWave.objects.filter(pk=delivery.wave_id).update(
                state=CampaignWave.State.SENDING,
                monitoring_started_at=None,
                completed_at=None,
            )
        campaign = delivery.campaign
        campaign.status = 'sending'
        campaign.save(update_fields=['status'])
        task_id = async_task(
            'email_app.tasks.send_campaign.send_campaign_batch',
            campaign_id=campaign.pk,
            delivery_ids=[delivery.pk],
            task_name=build_task_name(
                'Retry campaign delivery',
                f'#{campaign.pk} delivery {delivery.pk}',
                source,
            ),
        )
        return delivery, str(task_id) if task_id else None


def assume_delivery_sent(delivery_id, *, actor):
    """Resolve an ambiguous outcome without another external side effect."""
    from email_app.tasks.send_campaign import refresh_campaign_status

    with transaction.atomic():
        delivery = (
            CampaignDelivery.objects.select_for_update()
            .select_related('campaign')
            .get(pk=delivery_id)
        )
        if delivery.state != CampaignDelivery.State.AMBIGUOUS:
            raise CampaignDeliveryConflict('Delivery is no longer ambiguous.')
        delivery.state = CampaignDelivery.State.ASSUMED_SENT
        delivery.resolution = CampaignDelivery.Resolution.ASSUME_SENT
        delivery.resolved_at = timezone.now()
        delivery.resolved_by = actor
        delivery.completed_at = timezone.now()
        delivery.save(update_fields=[
            'state', 'resolution', 'resolved_at', 'resolved_by',
            'completed_at', 'updated_at',
        ])
        refresh_campaign_status(delivery.campaign_id)
        return delivery
