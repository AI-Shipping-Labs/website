"""Fail-closed campaign fan-out and per-recipient delivery tasks."""

import logging

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from email_app.models import CampaignDelivery, CampaignWave, EmailCampaign, EmailLog
from email_app.services.campaign_pacing import (
    DEFAULT_SEND_DELAY,
    get_campaign_batch_interval_seconds,
)
from email_app.services.relay_campaign_progress import (
    begin_native_queue,
    mark_native_queue_accepted,
    record_native_queue_failure,
)
from email_app.services.relay_campaigns import (
    RelayCampaignApiError,
    alert_relay_campaign_failure,
    queue_native_campaign,
    retry_native_campaign_delivery,
    validate_native_campaign_recipient_counts,
)
from integrations.config import get_config

logger = logging.getLogger(__name__)

EDGE_BLOCK_MARKER = 'edge_blocked:'
DEFAULT_EDGE_BLOCK_COOLDOWN_SECONDS = 900
DEFAULT_BATCH_SIZE = 200
DEFAULT_MAX_DELIVERY_ATTEMPTS = 3
# Q tasks time out after 300 seconds and retry after 360 seconds. A claim that
# outlives the worker timeout but expires before the retry is fail-closed and
# recoverable without a second automatic transport call.
DELIVERY_CLAIM_SECONDS = 330
INACTIVE_AT_SEND = 'inactive_at_send'
PERMANENT_BOUNCE_AT_SEND = 'permanent_bounce_at_send'


def _get_batch_size():
    raw = get_config('EMAIL_BATCH_SIZE', DEFAULT_BATCH_SIZE)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        logger.warning('Invalid EMAIL_BATCH_SIZE; using default %s', DEFAULT_BATCH_SIZE)
        return DEFAULT_BATCH_SIZE
    return value


def _get_batch_interval_seconds(batch_size=None):
    if batch_size is None:
        batch_size = _get_batch_size()
    return get_campaign_batch_interval_seconds(batch_size)


def get_max_delivery_attempts():
    raw = get_config(
        'CAMPAIGN_DELIVERY_MAX_ATTEMPTS', DEFAULT_MAX_DELIVERY_ATTEMPTS,
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        logger.warning(
            'Invalid CAMPAIGN_DELIVERY_MAX_ATTEMPTS; using default %s',
            DEFAULT_MAX_DELIVERY_ATTEMPTS,
        )
        return DEFAULT_MAX_DELIVERY_ATTEMPTS
    return value


def _chunk(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _queue_native_campaign_batch(
    campaign, delivery_ids, *, wave_id=None, external_key=None,
):
    """Queue a frozen local snapshot through Relay's native campaign worker."""
    deliveries = CampaignDelivery.objects.filter(
        campaign=campaign,
        pk__in=delivery_ids,
        state=CampaignDelivery.State.PENDING,
    )
    if not external_key:
        external_key = f"aishl-campaign-{campaign.pk}"
    wave = None
    if wave_id:
        wave = CampaignWave.objects.get(pk=wave_id, campaign=campaign)
        external_key = f"{external_key}-wave-{wave.number}"
        wave.relay_campaign_key = external_key
        wave.save(update_fields=["relay_campaign_key"])

    begin_native_queue(campaign, external_key)

    try:
        result = queue_native_campaign(
            campaign,
            deliveries,
            external_key=external_key,
        )
        if not result.get("already_queued"):
            validate_native_campaign_recipient_counts(
                len(result.get("included_delivery_ids", [])),
                result.get("recipient_count"),
                result.get("skipped_count"),
                stage="queue",
                max_skipped=result.get("allowed_skipped_count", 0),
            )
        if result.get("already_queued"):
            for delivery in deliveries.filter(resolution=CampaignDelivery.Resolution.RETRY):
                retry_native_campaign_delivery(external_key, delivery.pk)
    except Exception as exc:
        record_native_queue_failure(campaign, exc)
        alert_relay_campaign_failure(campaign, "queue", exc)
        raise

    included_ids = set(result.get("included_delivery_ids", []))
    suppressed_emails = {str(email).strip().lower() for email in result.get("suppressed_emails", [])}
    now = timezone.now()
    for delivery in deliveries.select_related("user"):
        is_suppressed = delivery.recipient_email.strip().lower() in suppressed_emails
        if delivery.pk not in included_ids or is_suppressed:
            delivery.state = CampaignDelivery.State.SKIPPED
            if is_suppressed:
                delivery.skip_reason = "suppressed_at_relay"
            else:
                delivery.skip_reason = "ineligible_at_relay_queue"
            delivery.completed_at = now
            delivery.save(update_fields=["state", "skip_reason", "completed_at", "updated_at"])
            continue
        log, _created = EmailLog.objects.get_or_create(
            campaign=campaign,
            user_id=delivery.recipient_user_pk,
            defaults={
                "recipient_email": delivery.recipient_email,
                "email_type": "campaign",
                "subject": campaign.subject,
                "ses_message_id": "",
            },
        )
        delivery.state = CampaignDelivery.State.SENT
        delivery.email_log = log
        delivery.resolution = ""
        delivery.resolved_at = None
        delivery.completed_at = now
        delivery.save(update_fields=[
            "state", "email_log", "resolution", "resolved_at", "completed_at", "updated_at",
        ])

    mark_native_queue_accepted(campaign, already_queued=bool(result.get("already_queued")))
    refresh_campaign_status(campaign.pk)
    return {
        "campaign_id": campaign.pk,
        "batch_size": len(delivery_ids),
        "sent_count": len(included_ids),
        "skipped_count": len(delivery_ids) - len(included_ids),
        "relay_campaign_key": external_key,
        "relay_queued": bool(result.get("queued", True)),
    }


def _alert_legacy_quarantined_campaign_once(campaign_id):
    """Persist the quarantine error and alert once when a stale task sees it."""
    message = (
        "Legacy in-flight campaign was quarantined because its pre-Relay send "
        "state cannot be reconstructed. No recipients were sent by this task."
    )
    should_alert = False
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if (
            campaign.status == "needs_attention"
            and not campaign.relay_campaign_state
            and not campaign.relay_campaign_error
        ):
            campaign.relay_campaign_error = message
            campaign.save(update_fields=["relay_campaign_error"])
            should_alert = True
    if should_alert:
        alert_relay_campaign_failure(
            campaign,
            "legacy quarantine",
            RelayCampaignApiError(message),
        )
    return campaign


def send_campaign(campaign_id, batch_size=None, released_by_id=None):
    """Atomically freeze the audience and create all one-off batch schedules."""
    from django_q.models import Schedule

    from jobs.tasks import build_task_name

    if batch_size is None:
        batch_size = _get_batch_size()

    with transaction.atomic():
        try:
            campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        except EmailCampaign.DoesNotExist as exc:
            raise ValueError(f'Campaign {campaign_id} not found') from exc

        if campaign.audience_snapshotted_at is not None:
            total = campaign.deliveries.count()
            return {
                'campaign_id': campaign_id,
                'total': total,
                'batch_count': Schedule.objects.filter(
                    func='email_app.tasks.send_campaign.send_campaign_batch',
                    name__contains=f'#{campaign_id} ',
                ).count(),
                'status': campaign.status,
                'already_snapshotted': True,
            }

        if campaign.status == 'needs_attention':
            # Migration quarantine: the external state of a legacy in-flight
            # campaign cannot be reconstructed, so never manufacture work.
            _alert_legacy_quarantined_campaign_once(campaign_id)
            return {
                'campaign_id': campaign_id,
                'total': 0,
                'batch_count': 0,
                'status': campaign.status,
                'legacy_quarantined': True,
            }
        if campaign.status == 'draft':
            # Backward-compatible direct task invocation. Production entry
            # points claim before enqueue; the row lock still serializes any
            # duplicate parent tasks that predate that contract.
            campaign.status = 'sending'
            campaign.relay_campaign_state = 'pending'
            campaign.relay_campaign_progress_at = timezone.now()
            campaign.sent_count = 0
            campaign.sent_at = None
            campaign.save(update_fields=[
                'status', 'sent_count', 'sent_at', 'relay_campaign_state',
                'relay_campaign_progress_at',
            ])
        elif campaign.status != 'sending':
            raise ValueError(
                f"Campaign {campaign_id} has status '{campaign.status}', expected 'sending'"
            )
        if not campaign.relay_campaign_state:
            campaign.relay_campaign_state = 'pending'
            campaign.relay_campaign_progress_at = timezone.now()
            campaign.save(update_fields=['relay_campaign_state', 'relay_campaign_progress_at'])

        is_monitored = (
            campaign.audience_verification
            == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY
        )
        user_ordering = ('date_joined', 'pk') if is_monitored else ('pk',)
        users = list(
            campaign.get_eligible_recipients()
            .only('pk', 'email', 'date_joined')
            .order_by(*user_ordering)
        )
        existing_logs = {
            log.user_id: log
            for log in EmailLog.objects.filter(
                campaign=campaign,
                user_id__in=[user.pk for user in users],
            )
        }
        wave_for_user = {}
        waves = []
        if is_monitored:
            from email_app.services.campaign_waves import (
                FOLLOWUP_WAVE_SIZE,
                PILOT_WAVE_SIZE,
            )

            partitions = []
            remaining = users
            if campaign.verify_deadline_days:
                # Verify-or-delete: one send to everyone, no staged release.
                if remaining:
                    partitions.append(remaining)
                remaining = []
            if remaining:
                partitions.append(remaining[:PILOT_WAVE_SIZE])
                remaining = remaining[PILOT_WAVE_SIZE:]
            while remaining:
                partitions.append(remaining[:FOLLOWUP_WAVE_SIZE])
                remaining = remaining[FOLLOWUP_WAVE_SIZE:]
            CampaignWave.objects.bulk_create([
                CampaignWave(campaign=campaign, number=index)
                for index in range(1, len(partitions) + 1)
            ])
            waves = list(campaign.waves.order_by('number'))
            for wave, partition in zip(waves, partitions, strict=True):
                for user in partition:
                    wave_for_user[user.pk] = wave

        CampaignDelivery.objects.bulk_create([
            CampaignDelivery(
                campaign=campaign,
                wave=wave_for_user.get(user.pk),
                user=user,
                recipient_user_pk=user.pk,
                recipient_email=user.email,
                state=(
                    CampaignDelivery.State.SENT
                    if user.pk in existing_logs
                    else CampaignDelivery.State.PENDING
                ),
                attempt_count=1 if user.pk in existing_logs else 0,
                email_log=existing_logs.get(user.pk),
                ses_message_id=(
                    existing_logs[user.pk].ses_message_id
                    if user.pk in existing_logs
                    else ''
                ),
                completed_at=(
                    existing_logs[user.pk].sent_at
                    if user.pk in existing_logs
                    else None
                ),
            )
            for user in users
        ])
        delivery_ids = list(
            campaign.deliveries.order_by('recipient_user_pk')
            .values_list('pk', flat=True)
        )
        snapshot_time = timezone.now()
        campaign.audience_snapshotted_at = snapshot_time

        if not delivery_ids:
            campaign.status = 'sent'
            campaign.sent_count = 0
            campaign.sent_at = snapshot_time
            campaign.save(update_fields=[
                'audience_snapshotted_at', 'status', 'sent_count', 'sent_at',
            ])
            return {
                'campaign_id': campaign_id,
                'total': 0,
                'batch_count': 0,
                'status': 'sent',
            }

        if is_monitored:
            from email_app.services.campaign_waves import release_initial_wave

            campaign.relay_campaign_state = 'pending'
            campaign.save(update_fields=['audience_snapshotted_at', 'relay_campaign_state'])
            release_initial_wave(
                waves[0],
                actor_id=released_by_id,
                source='campaign fan-out',
                now=snapshot_time,
            )
            return {
                'campaign_id': campaign_id,
                'total': len(delivery_ids),
                'batch_count': 1,
                'wave_count': len(waves),
                'status': 'sending',
            }

        chunks = [delivery_ids]
        for index, delivery_chunk in enumerate(chunks):
            task_name = build_task_name(
                'Queue Relay campaign',
                f'#{campaign_id} {campaign.subject}',
                'campaign fan-out',
            )
            Schedule.objects.create(
                name=task_name,
                func='email_app.tasks.send_campaign.send_campaign_batch',
                schedule_type=Schedule.ONCE,
                repeats=1,
                next_run=snapshot_time,
                kwargs={
                    'campaign_id': campaign_id,
                    'delivery_ids': delivery_chunk,
                    'q_options': {'task_name': task_name},
                },
            )
        campaign.save(update_fields=['audience_snapshotted_at'])

    return {
        'campaign_id': campaign_id,
        'total': len(delivery_ids),
        'batch_count': len(chunks),
        'status': 'sending',
    }


def _append_diagnostic(existing, new):
    if not existing:
        return new[:500]
    return f'{existing} | {new}'[-500:]


def _expire_stale_claim(delivery_id, *, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        delivery = CampaignDelivery.objects.select_for_update().filter(
            pk=delivery_id,
        ).first()
        if delivery is None:
            return False
        if (
            delivery.state != CampaignDelivery.State.DISPATCHING
            or delivery.claim_expires_at is None
            or delivery.claim_expires_at > now
        ):
            return False
        delivery.state = CampaignDelivery.State.AMBIGUOUS
        delivery.last_error = _append_diagnostic(
            delivery.last_error,
            'Worker claim expired after the SES boundary may have begun.',
        )
        delivery.completed_at = now
        delivery.save(update_fields=[
            'state', 'last_error', 'completed_at', 'updated_at',
        ])
        return True


def send_campaign_batch(
    campaign_id,
    delivery_ids=None,
    user_ids=None,
    send_delay=None,
    wave_id=None,
    external_key=None,
):
    """Process durable deliveries; legacy user-id schedules fail closed."""
    if send_delay is None:
        send_delay = DEFAULT_SEND_DELAY
    try:
        campaign = EmailCampaign.objects.get(pk=campaign_id)
    except EmailCampaign.DoesNotExist as exc:
        raise ValueError(f'Campaign {campaign_id} not found') from exc

    if campaign.relay_campaign_state:
        return _queue_native_campaign_batch(
            campaign,
            delivery_ids or [],
            wave_id=wave_id,
            external_key=external_key,
        )
    if campaign.status == "needs_attention":
        _alert_legacy_quarantined_campaign_once(campaign_id)
        logger.error(
            "Refusing legacy campaign task without Relay campaign state campaign=%s",
            campaign_id,
        )
        return {
            "campaign_id": campaign_id,
            "batch_size": 0,
            "sent_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "ambiguous_count": 0,
            "legacy_quarantined": True,
        }
    error = RelayCampaignApiError(
        "Campaign task has no native Relay campaign state; refusing transactional fallback."
    )
    campaign.relay_campaign_state = "failed"
    campaign.relay_campaign_error = str(error)
    campaign.save(update_fields=["relay_campaign_state", "relay_campaign_error"])
    alert_relay_campaign_failure(campaign, "queue", error)
    raise error


def refresh_campaign_status(campaign_id):
    """Derive aggregate status strictly from the durable delivery ledger."""
    campaign = EmailCampaign.objects.only('audience_verification').get(pk=campaign_id)
    if (
        campaign.audience_verification
        == EmailCampaign.AUDIENCE_VERIFICATION_UNVERIFIED_ONLY
    ):
        from email_app.services.campaign_waves import refresh_campaign_waves

        refresh_campaign_waves(campaign_id)
        return {}
    with transaction.atomic():
        campaign = EmailCampaign.objects.select_for_update().get(pk=campaign_id)
        if (
            campaign.status == 'needs_attention'
            and campaign.audience_snapshotted_at is None
            and not campaign.deliveries.exists()
        ):
            return {}
        counts = {
            row['state']: row['count']
            for row in campaign.deliveries.values('state').annotate(count=Count('id'))
        }
        confirmed = campaign.deliveries.filter(
            state=CampaignDelivery.State.SENT,
            email_log__isnull=False,
        ).count()
        now = timezone.now()
        has_work = campaign.deliveries.filter(
            Q(state=CampaignDelivery.State.PENDING)
            | Q(
                state=CampaignDelivery.State.DISPATCHING,
                claim_expires_at__gt=now,
            )
            | Q(
                state=CampaignDelivery.State.DISPATCHING,
                claim_expires_at__isnull=True,
            )
        ).exists()
        has_attention = campaign.deliveries.filter(
            Q(state=CampaignDelivery.State.FAILED)
            | Q(state=CampaignDelivery.State.AMBIGUOUS)
            | Q(
                state=CampaignDelivery.State.DISPATCHING,
                claim_expires_at__lte=now,
            )
        ).exists()
        updates = []
        if campaign.sent_count != confirmed:
            campaign.sent_count = confirmed
            updates.append('sent_count')
        if has_work:
            target_status = 'sending'
        elif has_attention:
            target_status = 'needs_attention'
        else:
            target_status = 'sent'
        updates.extend(campaign.apply_derived_status(target_status, now))
        if updates:
            campaign.save(update_fields=updates)
        return counts


# Compatibility name retained for callers/tests that passed a campaign object.
def _refresh_campaign_status(campaign):
    return refresh_campaign_status(campaign.pk)
