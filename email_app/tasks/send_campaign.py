"""Background tasks for sending email campaigns.

Architecture: a single send_campaign() task fans out into N
send_campaign_batch() tasks, each handling EMAIL_BATCH_SIZE recipients.
Splitting work across chunks keeps individual tasks well below the
django-q ``Q_CLUSTER['timeout']`` (300s) ceiling, lets multiple workers
send in parallel, and isolates failures: if one chunk dies, the rest
finish independently and only the failed chunk needs retry.

Per-recipient idempotency is enforced two ways:
- A partial unique constraint on EmailLog(campaign, user) where
  campaign IS NOT NULL makes accidental double-sends a database error.
- Each chunk skips users with an existing EmailLog for the campaign,
  so a retried chunk does not even attempt to send to recipients that
  earlier attempts already reached.

Usage:
    from jobs.tasks import async_task
    async_task('email_app.tasks.send_campaign.send_campaign', campaign_id=42)
"""

import logging
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from content.utils.markdown import render_email_markdown
from email_app.models import EmailCampaign, EmailLog
from email_app.services.email_service import (
    UNSUBSCRIBED_AT_SEND,
    EmailService,
    EmailServiceError,
)
from integrations.config import get_config

logger = logging.getLogger(__name__)

# Delay between emails in seconds to respect SES sending rate limits.
DEFAULT_SEND_DELAY = 0.05  # 50ms

# Default chunk size if EMAIL_BATCH_SIZE is not configured at runtime.
DEFAULT_BATCH_SIZE = 200

# Default spacing between fan-out batches in seconds. Batches are
# scheduled at now + index * interval so they do not all fire at once
# and burst past the SES send-rate limit (issue #922). Tunable in
# Studio settings via CAMPAIGN_BATCH_INTERVAL_SECONDS.
DEFAULT_BATCH_INTERVAL_SECONDS = 60


def _get_batch_size():
    """Return a positive runtime batch size, falling back safely."""
    raw = get_config('EMAIL_BATCH_SIZE', DEFAULT_BATCH_SIZE)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        logger.warning(
            'Invalid EMAIL_BATCH_SIZE; using default %s', DEFAULT_BATCH_SIZE,
        )
        return DEFAULT_BATCH_SIZE
    return value


def _get_batch_interval_seconds():
    """Return the configured stagger between fan-out batches in seconds.

    Resolves CAMPAIGN_BATCH_INTERVAL_SECONDS through ``get_config`` (DB
    override -> env -> Django settings -> default), so an operator can
    tune the SES burst protection from Studio without a redeploy. The
    value arrives as a string from env / DB overrides, so we coerce
    defensively. A negative value is clamped to 0 (all batches fire
    immediately); 0 is a valid value meaning "no stagger".
    """
    raw = get_config(
        'CAMPAIGN_BATCH_INTERVAL_SECONDS', DEFAULT_BATCH_INTERVAL_SECONDS,
    )
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BATCH_INTERVAL_SECONDS
    return max(value, 0)


def _chunk(items, size):
    """Yield successive ``size``-length slices of ``items``."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def send_campaign(campaign_id, batch_size=None):
    """Fan-out task: split a campaign's recipients into chunks and enqueue
    one ``send_campaign_batch`` task per chunk.

    Validates the campaign is in 'draft' status, transitions it to
    'sending', resolves the eligible recipient list to user IDs, splits
    into chunks of ``batch_size`` (default runtime ``EMAIL_BATCH_SIZE``),
    and enqueues one ``send_campaign_batch`` task per chunk. Returns
    immediately after enqueuing.

    Args:
        campaign_id: Primary key of the EmailCampaign to send.
        batch_size: Override for chunk size. Defaults to
            runtime ``EMAIL_BATCH_SIZE`` (or 200).

    Returns:
        dict with campaign_id, total recipients, batch_count, and status.

    Raises:
        ValueError: If campaign not found or not in 'draft' status.
    """
    try:
        campaign = EmailCampaign.objects.get(pk=campaign_id)
    except EmailCampaign.DoesNotExist:
        logger.error("Campaign %s not found", campaign_id)
        raise ValueError(f"Campaign {campaign_id} not found")

    if campaign.status != 'draft':
        logger.error(
            "Campaign %s has status '%s', expected 'draft'",
            campaign_id, campaign.status,
        )
        raise ValueError(
            f"Campaign {campaign_id} has status '{campaign.status}', "
            f"expected 'draft'"
        )

    if batch_size is None:
        batch_size = _get_batch_size()

    # Materialize the recipient ID list so chunks have a stable view of
    # the audience even if users are added/changed mid-send.
    user_ids = list(
        campaign.get_eligible_recipients().values_list('pk', flat=True)
    )
    total = len(user_ids)

    # Transition to sending before enqueuing chunks. This is the
    # single place where status moves from draft -> sending.
    campaign.status = 'sending'
    # Reset sent_count so a retried draft starts from a clean baseline
    # (idempotency on EmailLog still prevents double-sends).
    campaign.sent_count = 0
    campaign.save(update_fields=['status', 'sent_count'])

    if total == 0:
        # No recipients: mark sent immediately and bail.
        campaign.status = 'sent'
        campaign.sent_at = timezone.now()
        campaign.save(update_fields=['status', 'sent_at'])
        logger.info(
            "Campaign %s has no eligible recipients; marked sent",
            campaign_id,
        )
        return {
            'campaign_id': campaign_id,
            'total': 0,
            'batch_count': 0,
            'status': 'sent',
        }

    chunks = list(_chunk(user_ids, batch_size))
    # Imported lazily: jobs.tasks pulls in django-q, which has heavy
    # side-effects at import time, and tests patch by path.
    from django_q.models import Schedule

    from jobs.tasks import build_task_name

    # Stagger the batches so they do not all fire at once and burst past
    # the SES send-rate limit (issue #922). Batch i is scheduled to run
    # at now + i * interval; the first batch (i=0) runs immediately. Each
    # batch is a one-off (Schedule.ONCE) future-dated Schedule row, which
    # shows up as a pending future task in Studio. We mirror
    # jobs.tasks.helpers.schedule() / queue_imported_welcome_emails() by
    # injecting q_options.task_name so each fired Task lands a descriptive
    # name (relates to #920) instead of a Django-Q random codename.
    interval = _get_batch_interval_seconds()
    now = timezone.now()

    for index, chunk_user_ids in enumerate(chunks):
        task_name = build_task_name(
            'Send campaign batch',
            f'#{campaign_id} {campaign.subject} '
            f'batch {index + 1}/{len(chunks)}',
            'campaign fan-out',
        )
        Schedule.objects.create(
            name=task_name,
            func='email_app.tasks.send_campaign.send_campaign_batch',
            schedule_type=Schedule.ONCE,
            repeats=1,
            next_run=now + timedelta(seconds=index * interval),
            kwargs={
                'campaign_id': campaign_id,
                'user_ids': chunk_user_ids,
                'q_options': {'task_name': task_name},
            },
        )

    logger.info(
        "Campaign %s ('%s') fanned out: %d recipients across %d batches "
        "(batch_size=%d, interval=%ds)",
        campaign_id, campaign.subject, total, len(chunks), batch_size,
        interval,
    )

    return {
        'campaign_id': campaign_id,
        'total': total,
        'batch_count': len(chunks),
        'status': 'sending',
    }


def send_campaign_batch(campaign_id, user_ids, send_delay=None):
    """Send a single chunk of a campaign to the given user IDs.

    Skips users that already have an EmailLog for this campaign
    (idempotency for retries). After processing, checks whether the
    campaign's total successful sends covers the entire eligible
    audience and, if so, transitions the campaign to 'sent'.

    Args:
        campaign_id: Primary key of the EmailCampaign to send.
        user_ids: List of user PKs to attempt to send to in this batch.
        send_delay: Delay in seconds between sends. Defaults to
            ``DEFAULT_SEND_DELAY`` (0.05s). Set to 0 in tests for speed.

    Returns:
        dict with campaign_id, batch_size, sent_count, skipped_count, and
        unsubscribed_at_send_count. ``skipped_count`` retains its historical
        meaning of already-sent idempotency skips.

    Raises:
        ValueError: If campaign not found.
    """
    if send_delay is None:
        send_delay = DEFAULT_SEND_DELAY

    try:
        campaign = EmailCampaign.objects.get(pk=campaign_id)
    except EmailCampaign.DoesNotExist:
        logger.error("Campaign %s not found", campaign_id)
        raise ValueError(f"Campaign {campaign_id} not found")

    User = get_user_model()

    # Find users in this batch that have NOT already received this
    # campaign — the idempotency check that lets us safely re-run a
    # failed chunk without double-sending.
    already_sent_ids = set(
        EmailLog.objects.filter(
            campaign=campaign, user_id__in=user_ids,
        ).values_list('user_id', flat=True)
    )
    pending_ids = [uid for uid in user_ids if uid not in already_sent_ids]
    skipped = len(already_sent_ids)

    logger.info(
        "Campaign %s batch starting: %d to send, %d skipped (already sent)",
        campaign_id, len(pending_ids), skipped,
    )

    service = EmailService()
    sent_count = 0
    unsubscribed_at_send_count = 0
    # Pre-render markdown body once per batch — it does not change
    # across recipients within a campaign.
    body_html = render_email_markdown(campaign.body)

    for user_id in pending_ids:
        # Fetch one recipient at a time. Loading the full batch here would
        # create a stale consent window for recipients later in the loop.
        user = User.objects.filter(pk=user_id).first()
        if user is None:
            continue

        try:
            result = service.send_rendered(
                user,
                campaign.subject,
                body_html,
                email_type='campaign',
                campaign_id=campaign_id,
            )
            if result.skip_reason is not None:
                if result.skip_reason == UNSUBSCRIBED_AT_SEND:
                    unsubscribed_at_send_count += 1
                continue

            try:
                with transaction.atomic():
                    EmailLog.objects.create(
                        campaign=campaign,
                        user=user,
                        recipient_email=user.email,
                        email_type='campaign',
                        subject=campaign.subject,
                        ses_message_id=result.ses_message_id,
                    )
            except IntegrityError:
                # A concurrent task already created the log for this
                # (campaign, user). Treat as a no-op rather than a
                # failure so the chunk continues cleanly.
                logger.warning(
                    "Duplicate EmailLog for campaign %s user %s; skipping",
                    campaign_id, user.pk,
                )
                continue

            sent_count += 1

        except EmailServiceError:
            logger.exception(
                "Failed to send campaign %s to %s",
                campaign_id, user.email,
            )
            # Continue sending to remaining recipients in this batch.
            continue

        if send_delay > 0:
            time.sleep(send_delay)

    # Update aggregate sent_count and check for completion. We
    # recompute from EmailLog (the source of truth) instead of
    # incrementing a counter, so concurrent batches converge correctly.
    _refresh_campaign_status(campaign)

    logger.info(
        "Campaign %s batch complete: %d sent, %d already sent, "
        "%d unsubscribed at send",
        campaign_id, sent_count, skipped, unsubscribed_at_send_count,
    )

    return {
        'campaign_id': campaign_id,
        'batch_size': len(user_ids),
        'sent_count': sent_count,
        'skipped_count': skipped,
        'unsubscribed_at_send_count': unsubscribed_at_send_count,
    }


def _refresh_campaign_status(campaign):
    """Recompute aggregate sent_count and flip status to 'sent' when
    every eligible recipient has an EmailLog (or, more precisely, when
    no eligible recipient is still pending).

    Called after each chunk finishes; the last chunk to finish is the
    one that flips the status. The campaign row lock serializes parallel
    refreshes so a stale worker cannot overwrite a newer aggregate count.
    """
    with transaction.atomic():
        locked_campaign = EmailCampaign.objects.select_for_update().get(
            pk=campaign.pk,
        )
        eligible_ids = set(
            locked_campaign.get_eligible_recipients().values_list(
                'pk', flat=True,
            )
        )
        campaign_logs = EmailLog.objects.filter(campaign=locked_campaign)
        logged_ids = set(campaign_logs.values_list('user_id', flat=True))

        sent_total = campaign_logs.count()
        pending = eligible_ids - logged_ids
        update_fields = []

        if locked_campaign.sent_count != sent_total:
            locked_campaign.sent_count = sent_total
            update_fields.append('sent_count')

        if not pending and locked_campaign.status == 'sending':
            locked_campaign.status = 'sent'
            locked_campaign.sent_at = timezone.now()
            update_fields.extend(['status', 'sent_at'])
            logger.info(
                "Campaign %s complete: %d/%d eligible recipients sent",
                locked_campaign.pk,
                sent_total,
                len(eligible_ids),
            )

        if update_fields:
            locked_campaign.save(update_fields=update_fields)
