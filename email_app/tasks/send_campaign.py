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

import markdown as md
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone

from email_app.models import EmailCampaign, EmailLog
from email_app.services.email_classification import EMAIL_KIND_PROMOTIONAL
from email_app.services.email_service import EmailService, EmailServiceError

logger = logging.getLogger(__name__)

# Delay between emails in seconds to respect SES sending rate limits.
DEFAULT_SEND_DELAY = 0.05  # 50ms

# Default chunk size if EMAIL_BATCH_SIZE is not configured in settings.
DEFAULT_BATCH_SIZE = 200


def _get_batch_size():
    """Return the configured EMAIL_BATCH_SIZE, falling back to default."""
    return int(getattr(settings, 'EMAIL_BATCH_SIZE', DEFAULT_BATCH_SIZE))


def _chunk(items, size):
    """Yield successive ``size``-length slices of ``items``."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


def send_campaign(campaign_id, batch_size=None):
    """Fan-out task: split a campaign's recipients into chunks and enqueue
    one ``send_campaign_batch`` task per chunk.

    Validates the campaign is in 'draft' status, transitions it to
    'sending', resolves the eligible recipient list to user IDs, splits
    into chunks of ``batch_size`` (default ``settings.EMAIL_BATCH_SIZE``),
    and enqueues one ``send_campaign_batch`` task per chunk. Returns
    immediately after enqueuing.

    Args:
        campaign_id: Primary key of the EmailCampaign to send.
        batch_size: Override for chunk size. Defaults to
            ``settings.EMAIL_BATCH_SIZE`` (or 200).

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
    # side-effects at import time, and tests patch async_task by path.
    from jobs.tasks import async_task, build_task_name

    for index, chunk_user_ids in enumerate(chunks, start=1):
        async_task(
            'email_app.tasks.send_campaign.send_campaign_batch',
            campaign_id=campaign_id,
            user_ids=chunk_user_ids,
            task_name=build_task_name(
                'Send campaign batch',
                f'#{campaign_id} {campaign.subject} batch {index}/{len(chunks)}',
                'campaign fan-out',
            ),
        )

    logger.info(
        "Campaign %s ('%s') fanned out: %d recipients across %d batches "
        "(batch_size=%d)",
        campaign_id, campaign.subject, total, len(chunks), batch_size,
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
        dict with campaign_id, batch_size, sent_count, skipped_count.

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

    users = list(User.objects.filter(pk__in=pending_ids))

    logger.info(
        "Campaign %s batch starting: %d to send, %d skipped (already sent)",
        campaign_id, len(users), skipped,
    )

    service = EmailService()
    sent_count = 0
    # Pre-render markdown body once per batch — it does not change
    # across recipients within a campaign.
    body_html = md.markdown(campaign.body, extensions=['extra'])

    for user in users:
        try:
            unsubscribe_url = service._build_unsubscribe_url(user)

            # Issue #450: per-recipient verify-email footer CTA. The
            # ``users`` list is freshly fetched from the DB above
            # (``User.objects.filter(pk__in=pending_ids)``) so the
            # ``email_verified`` flag reflects the SEND-time value, not
            # whatever it was when the campaign was enqueued. This
            # mirrors how ``unsubscribed`` is handled — the latest DB
            # state wins, even if the user verified after enqueue.
            verify_email_url = None
            if service._should_include_verify_footer(user, 'campaign'):
                verify_email_url = service._build_verify_email_url(user)

            full_html = render_to_string('email_app/base_email.html', {
                'subject': campaign.subject,
                'body_html': body_html,
                'unsubscribe_url': unsubscribe_url,
                'verify_email_url': verify_email_url,
            })

            ses_message_id = service._send_ses(
                user.email,
                campaign.subject,
                full_html,
                email_kind=EMAIL_KIND_PROMOTIONAL,
                unsubscribe_url=unsubscribe_url,
            )

            try:
                with transaction.atomic():
                    EmailLog.objects.create(
                        campaign=campaign,
                        user=user,
                        email_type='campaign',
                        ses_message_id=ses_message_id,
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
        "Campaign %s batch complete: %d sent, %d skipped",
        campaign_id, sent_count, skipped,
    )

    return {
        'campaign_id': campaign_id,
        'batch_size': len(user_ids),
        'sent_count': sent_count,
        'skipped_count': skipped,
    }


def _refresh_campaign_status(campaign):
    """Recompute aggregate sent_count and flip status to 'sent' when
    every eligible recipient has an EmailLog (or, more precisely, when
    no eligible recipient is still pending).

    Called after each chunk finishes; the last chunk to finish is the
    one that flips the status. Safe to call concurrently — the
    eligible-but-unlogged check is monotonic.
    """
    eligible_ids = set(
        campaign.get_eligible_recipients().values_list('pk', flat=True)
    )
    logged_ids = set(
        EmailLog.objects.filter(
            campaign=campaign, user_id__in=eligible_ids,
        ).values_list('user_id', flat=True)
    )

    sent_total = len(logged_ids)
    pending = eligible_ids - logged_ids

    update_fields = []
    # Refresh from DB to avoid stomping on a parallel update.
    campaign.refresh_from_db(fields=['status', 'sent_count'])

    if campaign.sent_count != sent_total:
        campaign.sent_count = sent_total
        update_fields.append('sent_count')

    if not pending and campaign.status == 'sending':
        campaign.status = 'sent'
        campaign.sent_at = timezone.now()
        update_fields.extend(['status', 'sent_at'])
        logger.info(
            "Campaign %s complete: %d/%d eligible recipients sent",
            campaign.pk, sent_total, len(eligible_ids),
        )

    if update_fields:
        campaign.save(update_fields=update_fields)
