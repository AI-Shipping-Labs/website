"""Fail-closed campaign fan-out and per-recipient delivery tasks."""

import logging
import time
import uuid
from datetime import timedelta

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q
from django.utils import timezone

from content.utils.markdown import render_email_markdown
from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from email_app.services.email_service import (
    UNSUBSCRIBED_AT_SEND,
    EmailService,
    EmailServiceError,
)
from integrations.config import get_config

logger = logging.getLogger(__name__)

DEFAULT_SEND_DELAY = 0.05
DEFAULT_BATCH_SIZE = 200
DEFAULT_BATCH_INTERVAL_SECONDS = 60
DEFAULT_MAX_DELIVERY_ATTEMPTS = 3
# Q tasks time out after 300 seconds and retry after 360 seconds. A claim that
# outlives the worker timeout but expires before the retry is fail-closed and
# recoverable without a second automatic transport call.
DELIVERY_CLAIM_SECONDS = 330
INACTIVE_AT_SEND = 'inactive_at_send'


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


def _get_batch_interval_seconds():
    raw = get_config(
        'CAMPAIGN_BATCH_INTERVAL_SECONDS', DEFAULT_BATCH_INTERVAL_SECONDS,
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BATCH_INTERVAL_SECONDS
    return max(value, 0)


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


def send_campaign(campaign_id, batch_size=None):
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
            campaign.sent_count = 0
            campaign.sent_at = None
            campaign.save(update_fields=['status', 'sent_count', 'sent_at'])
        elif campaign.status != 'sending':
            raise ValueError(
                f"Campaign {campaign_id} has status '{campaign.status}', expected 'sending'"
            )

        users = list(
            campaign.get_eligible_recipients()
            .only('pk', 'email')
            .order_by('pk')
        )
        existing_logs = {
            log.user_id: log
            for log in EmailLog.objects.filter(
                campaign=campaign,
                user_id__in=[user.pk for user in users],
            )
        }
        CampaignDelivery.objects.bulk_create([
            CampaignDelivery(
                campaign=campaign,
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

        chunks = list(_chunk(delivery_ids, batch_size))
        interval = _get_batch_interval_seconds()
        for index, delivery_chunk in enumerate(chunks):
            chunk_user_ids = list(
                CampaignDelivery.objects.filter(pk__in=delivery_chunk)
                .order_by('recipient_user_pk')
                .values_list('recipient_user_pk', flat=True)
            )
            task_name = build_task_name(
                'Send campaign batch',
                f'#{campaign_id} {campaign.subject} batch {index + 1}/{len(chunks)}',
                'campaign fan-out',
            )
            Schedule.objects.create(
                name=task_name,
                func='email_app.tasks.send_campaign.send_campaign_batch',
                schedule_type=Schedule.ONCE,
                repeats=1,
                next_run=snapshot_time + timedelta(seconds=index * interval),
                kwargs={
                    'campaign_id': campaign_id,
                    'delivery_ids': delivery_chunk,
                    # Retained for task-history readability and rolling-deploy
                    # compatibility; delivery_ids is authoritative.
                    'user_ids': chunk_user_ids,
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


def _mark_pending_terminal(delivery_id, *, state, reason='', error=''):
    now = timezone.now()
    with transaction.atomic():
        delivery = CampaignDelivery.objects.select_for_update().get(pk=delivery_id)
        if delivery.state != CampaignDelivery.State.PENDING:
            return False
        delivery.state = state
        delivery.skip_reason = reason[:100]
        if error:
            delivery.last_error = _append_diagnostic(delivery.last_error, error)
        delivery.completed_at = now
        delivery.save(update_fields=[
            'state', 'skip_reason', 'last_error', 'completed_at', 'updated_at',
        ])
        return True


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


def _claim_delivery(delivery_id, *, recipient_email):
    now = timezone.now()
    token = uuid.uuid4()
    updated = CampaignDelivery.objects.filter(
        pk=delivery_id,
        state=CampaignDelivery.State.PENDING,
    ).update(
        recipient_email=recipient_email,
        state=CampaignDelivery.State.DISPATCHING,
        claim_token=token,
        claimed_at=now,
        claim_expires_at=now + timedelta(seconds=DELIVERY_CLAIM_SECONDS),
        attempt_count=F('attempt_count') + 1,
        completed_at=None,
        updated_at=now,
    )
    return token if updated == 1 else None


def _mark_transport_outcome(delivery_id, claim_token, *, state, error):
    now = timezone.now()
    with transaction.atomic():
        delivery = CampaignDelivery.objects.select_for_update().get(pk=delivery_id)
        if (
            delivery.state != CampaignDelivery.State.DISPATCHING
            or delivery.claim_token != claim_token
        ):
            return False
        delivery.state = state
        delivery.last_error = _append_diagnostic(delivery.last_error, error)
        delivery.completed_at = now
        delivery.save(update_fields=[
            'state', 'last_error', 'completed_at', 'updated_at',
        ])
        return True


def _finalize_delivery_sent(delivery_id, claim_token, message_id):
    """Finalize only the exact live claim; log and state commit together."""
    with transaction.atomic():
        delivery = (
            CampaignDelivery.objects.select_for_update(of=('self',))
            .select_related('campaign', 'user')
            .get(pk=delivery_id)
        )
        if (
            delivery.state != CampaignDelivery.State.DISPATCHING
            or delivery.claim_token != claim_token
        ):
            return False
        try:
            # Keep an integrity failure inside a savepoint so the outer
            # transaction remains usable for the defensive lookup.
            with transaction.atomic():
                log = EmailLog.objects.create(
                    campaign=delivery.campaign,
                    user=delivery.user,
                    recipient_email=delivery.recipient_email,
                    email_type='campaign',
                    subject=delivery.campaign.subject,
                    ses_message_id=message_id,
                )
        except IntegrityError:
            log = EmailLog.objects.get(
                campaign=delivery.campaign,
                user_id=delivery.recipient_user_pk,
            )
            if log.ses_message_id != message_id:
                raise RuntimeError(
                    'A conflicting confirmed send log already exists.',
                )
        now = timezone.now()
        delivery.state = CampaignDelivery.State.SENT
        delivery.ses_message_id = message_id
        delivery.email_log = log
        delivery.completed_at = now
        delivery.save(update_fields=[
            'state', 'ses_message_id', 'email_log', 'completed_at', 'updated_at',
        ])
        return True


def _transport_failure_state(exc):
    """Only a structured SES ClientError proves a definitive rejection."""
    if isinstance(exc.__cause__, ClientError):
        return CampaignDelivery.State.FAILED, 'SES definitively rejected the request.'
    return (
        CampaignDelivery.State.AMBIGUOUS,
        'SES transport outcome was indeterminate; automatic retry suppressed.',
    )


def send_campaign_batch(
    campaign_id,
    delivery_ids=None,
    user_ids=None,
    send_delay=None,
):
    """Process durable deliveries; legacy user-id schedules fail closed."""
    if send_delay is None:
        send_delay = DEFAULT_SEND_DELAY
    try:
        campaign = EmailCampaign.objects.get(pk=campaign_id)
    except EmailCampaign.DoesNotExist as exc:
        raise ValueError(f'Campaign {campaign_id} not found') from exc

    if delivery_ids is None:
        delivery_ids = []
        already_sent_count = 0
        if user_ids and campaign.status != 'needs_attention':
            # Rolling-deploy/direct-call compatibility: establish the durable
            # ledger before crossing SES. Migration-quarantined legacy sends
            # are refused above by their needs_attention state.
            User = get_user_model()
            with transaction.atomic():
                existing_logs = {
                    log.user_id: log
                    for log in EmailLog.objects.filter(
                        campaign=campaign,
                        user_id__in=user_ids,
                    )
                }
                users_by_id = {
                    user.pk: user for user in User.objects.filter(pk__in=user_ids)
                }
                for user_id in user_ids:
                    user = users_by_id.get(user_id)
                    if user is None:
                        continue
                    existing_log = existing_logs.get(user.pk)
                    delivery, _ = CampaignDelivery.objects.get_or_create(
                        campaign=campaign,
                        recipient_user_pk=user.pk,
                        defaults={
                            'user': user,
                            'recipient_email': user.email,
                            'state': (
                                CampaignDelivery.State.SENT
                                if existing_log
                                else CampaignDelivery.State.PENDING
                            ),
                            'attempt_count': 1 if existing_log else 0,
                            'email_log': existing_log,
                            'ses_message_id': (
                                existing_log.ses_message_id if existing_log else ''
                            ),
                            'completed_at': (
                                existing_log.sent_at if existing_log else None
                            ),
                        },
                    )
                    delivery_ids.append(delivery.pk)
                    if existing_log:
                        already_sent_count += 1
        elif user_ids:
            logger.warning(
                'Suppressing legacy campaign batch campaign=%s recipients=%s',
                campaign_id,
                len(user_ids),
            )
    else:
        already_sent_count = 0

    if (
        campaign.status == 'needs_attention'
        and campaign.audience_snapshotted_at is None
        and not campaign.deliveries.exists()
    ):
        # A pre-deploy task may still be present in the ORM broker. Its SES
        # outcome is unknowable, so preserve the migration quarantine.
        return {
            'campaign_id': campaign_id,
            'batch_size': 0,
            'sent_count': 0,
            'skipped_count': 0,
            'unsubscribed_at_send_count': 0,
            'failed_count': 0,
            'ambiguous_count': 0,
            'legacy_quarantined': True,
        }

    service = EmailService()
    body_html = None
    sent_count = 0
    skipped_count = already_sent_count
    failed_count = 0
    ambiguous_count = 0

    User = get_user_model()
    for delivery_id in delivery_ids:
        if _expire_stale_claim(delivery_id):
            ambiguous_count += 1
            continue
        delivery = CampaignDelivery.objects.filter(
            pk=delivery_id,
            campaign_id=campaign_id,
        ).first()
        if delivery is None or delivery.state != CampaignDelivery.State.PENDING:
            continue
        # ``recipient_user_pk`` is the immutable snapshotted identity. Account
        # merge may repoint ``delivery.user`` to the canonical account for
        # history, but that must not turn the retired recipient into a send to
        # the active canonical user.
        user = User.objects.filter(pk=delivery.recipient_user_pk).first()
        if user is None:
            if _mark_pending_terminal(
                delivery_id,
                state=CampaignDelivery.State.SKIPPED,
                reason='user_missing_at_send',
            ):
                skipped_count += 1
            continue
        if not user.is_active:
            if _mark_pending_terminal(
                delivery_id,
                state=CampaignDelivery.State.SKIPPED,
                reason=INACTIVE_AT_SEND,
            ):
                skipped_count += 1
            continue

        if body_html is None:
            body_html = render_email_markdown(campaign.body)

        try:
            prepared = service.prepare_rendered(
                user,
                campaign.subject,
                body_html,
                email_type='campaign',
                campaign_id=campaign_id,
            )
        except ObjectDoesNotExist:
            if _mark_pending_terminal(
                delivery_id,
                state=CampaignDelivery.State.SKIPPED,
                reason='user_missing_at_send',
            ):
                skipped_count += 1
            continue
        except Exception as exc:
            if _mark_pending_terminal(
                delivery_id,
                state=CampaignDelivery.State.FAILED,
                error=f'Local preparation failed: {type(exc).__name__}',
            ):
                failed_count += 1
            continue
        if prepared.skip_reason is not None:
            if _mark_pending_terminal(
                delivery_id,
                state=CampaignDelivery.State.SKIPPED,
                reason=prepared.skip_reason,
            ):
                if prepared.skip_reason != UNSUBSCRIBED_AT_SEND:
                    skipped_count += 1
            continue

        claim_token = _claim_delivery(
            delivery_id,
            recipient_email=prepared.to_email,
        )
        if claim_token is None:
            continue
        try:
            message_id = service.send_prepared(prepared)
        except EmailServiceError as exc:
            state, diagnostic = _transport_failure_state(exc)
            logger.error(
                'Failed to send campaign %s to %s: %s',
                campaign_id,
                prepared.to_email,
                diagnostic,
            )
            if _mark_transport_outcome(
                delivery_id,
                claim_token,
                state=state,
                error=diagnostic,
            ):
                if state == CampaignDelivery.State.FAILED:
                    failed_count += 1
                else:
                    ambiguous_count += 1
            continue
        except Exception:
            if _mark_transport_outcome(
                delivery_id,
                claim_token,
                state=CampaignDelivery.State.AMBIGUOUS,
                error='Unexpected post-claim failure; automatic retry suppressed.',
            ):
                ambiguous_count += 1
            continue

        if not message_id:
            if _mark_transport_outcome(
                delivery_id,
                claim_token,
                state=CampaignDelivery.State.AMBIGUOUS,
                error='SES returned no message ID; automatic retry suppressed.',
            ):
                ambiguous_count += 1
            continue

        if _finalize_delivery_sent(delivery_id, claim_token, message_id):
            sent_count += 1
        if send_delay > 0:
            time.sleep(send_delay)

    refresh_campaign_status(campaign_id)
    return {
        'campaign_id': campaign_id,
        'batch_size': len(delivery_ids),
        'sent_count': sent_count,
        'skipped_count': skipped_count,
        'unsubscribed_at_send_count': CampaignDelivery.objects.filter(
            pk__in=delivery_ids,
            skip_reason=UNSUBSCRIBED_AT_SEND,
        ).count(),
        'failed_count': failed_count,
        'ambiguous_count': ambiguous_count,
    }


def refresh_campaign_status(campaign_id):
    """Derive aggregate status strictly from the durable delivery ledger."""
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
        if campaign.status != target_status:
            campaign.status = target_status
            updates.append('status')
        if target_status == 'sent' and campaign.sent_at is None:
            campaign.sent_at = timezone.now()
            updates.append('sent_at')
        elif target_status != 'sent' and campaign.sent_at is not None:
            campaign.sent_at = None
            updates.append('sent_at')
        if updates:
            campaign.save(update_fields=updates)
        return counts


# Compatibility name retained for callers/tests that passed a campaign object.
def _refresh_campaign_status(campaign):
    return refresh_campaign_status(campaign.pk)
