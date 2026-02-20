"""Background task for sending email campaigns.

Usage:
    from jobs.tasks import async_task
    async_task('email_app.tasks.send_campaign.send_campaign', campaign_id=42)
"""

import logging
import time

import markdown as md
from django.template.loader import render_to_string
from django.utils import timezone

from email_app.models import EmailCampaign, EmailLog
from email_app.services.email_service import EmailService, EmailServiceError

logger = logging.getLogger(__name__)

# Delay between emails in seconds to respect SES sending rate limits.
DEFAULT_SEND_DELAY = 0.05  # 50ms


def send_campaign(campaign_id, send_delay=None):
    """Send an email campaign to all eligible recipients.

    Queries users where tier.level >= campaign.target_min_level,
    unsubscribed = False, and email_verified = True. Sends each email
    via EmailService with rate limiting and creates an EmailLog per send.

    Args:
        campaign_id: Primary key of the EmailCampaign to send.
        send_delay: Delay in seconds between emails (default 0.05s).
            Set to 0 in tests for speed.

    Returns:
        dict with campaign_id, sent_count, and status.

    Raises:
        ValueError: If campaign not found or not in 'draft' status.
    """
    if send_delay is None:
        send_delay = DEFAULT_SEND_DELAY

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

    # Transition to sending
    campaign.status = 'sending'
    campaign.save(update_fields=['status'])

    service = EmailService()
    recipients = campaign.get_eligible_recipients()
    total = recipients.count()
    sent_count = 0

    logger.info(
        "Starting campaign %s ('%s') to %d recipients",
        campaign_id, campaign.subject, total,
    )

    for user in recipients.iterator():
        try:
            # Render the campaign body as HTML for direct sending
            body_html = md.markdown(campaign.body, extensions=['extra'])
            unsubscribe_url = service._build_unsubscribe_url(user)

            full_html = render_to_string('email_app/base_email.html', {
                'subject': campaign.subject,
                'body_html': body_html,
                'unsubscribe_url': unsubscribe_url,
            })

            ses_message_id = service._send_ses(
                user.email, campaign.subject, full_html,
            )

            EmailLog.objects.create(
                campaign=campaign,
                user=user,
                email_type='campaign',
                ses_message_id=ses_message_id,
            )

            sent_count += 1

            # Update sent_count incrementally
            campaign.sent_count = sent_count
            campaign.save(update_fields=['sent_count'])

        except EmailServiceError:
            logger.exception(
                "Failed to send campaign %s to %s",
                campaign_id, user.email,
            )
            # Continue sending to remaining recipients
            continue

        # Rate limiting: delay between sends
        if send_delay > 0:
            time.sleep(send_delay)

    # Transition to sent
    campaign.status = 'sent'
    campaign.sent_at = timezone.now()
    campaign.sent_count = sent_count
    campaign.save(update_fields=['status', 'sent_at', 'sent_count'])

    logger.info(
        "Campaign %s complete: %d/%d emails sent",
        campaign_id, sent_count, total,
    )

    return {
        'campaign_id': campaign_id,
        'sent_count': sent_count,
        'total': total,
        'status': 'sent',
    }
