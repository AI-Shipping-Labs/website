"""Operator alerts for Relay campaign failures.

Slack is the primary route because these alerts usually fire while Relay
itself is failing, and the dev-mailbox fallback email travels through Relay.
"""

import logging

from django.core.cache import caches
from django.core.mail import send_mail

from community.slack_config import slack_api_enabled
from notifications.services.staff_slack import (
    escape_slack_text,
    message_payload,
    mrkdwn,
    post_staff_slack_message,
    staff_alert_channel_id,
)

logger = logging.getLogger(__name__)

RELAY_ALERT_RECIPIENT = "dev@dtcdev.click"
RELAY_ALERT_COOLDOWN_SECONDS = 60 * 60
RELAY_ALERT_CACHE_ALIAS = "django_q"


def _claim_cooldown(campaign, operation):
    """Return False while this campaign and operation are in their cooldown."""
    # Cache keys must not contain spaces; operations are short phrases.
    operation_slug = operation.replace(" ", "-")
    cache_key = f"relay-campaign-alert:{campaign.pk}:{operation_slug}"
    try:
        return caches[RELAY_ALERT_CACHE_ALIAS].add(
            cache_key, 1, RELAY_ALERT_COOLDOWN_SECONDS,
        )
    except Exception:
        # Broad on purpose: a cache outage must not suppress an alert.
        logger.exception(
            "Relay campaign alert cooldown unavailable campaign=%s operation=%s",
            campaign.pk,
            operation,
        )
        return True


def _send_alert_email(campaign, operation, subject, body):
    try:
        delivered = send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[RELAY_ALERT_RECIPIENT],
            fail_silently=False,
        )
    except Exception:
        # Broad on purpose: any backend failure must reach the Slack fallback.
        logger.exception(
            "Could not send dev alert for AISHL campaign Relay %s failure campaign=%s",
            operation,
            campaign.pk,
        )
        return False
    if delivered:
        return True
    logger.error(
        "Dev alert for AISHL campaign Relay %s failure was not accepted campaign=%s",
        operation,
        campaign.pk,
    )
    return False


def _post_alert_to_slack(campaign, operation, subject, body):
    if not slack_api_enabled():
        logger.error(
            "Slack fallback unavailable for Relay %s alert campaign=%s: Slack API disabled",
            operation,
            campaign.pk,
        )
        return False
    channel_id = staff_alert_channel_id()
    if not channel_id:
        logger.error(
            "Slack fallback unavailable for Relay %s alert campaign=%s: no staff channel",
            operation,
            campaign.pk,
        )
        return False
    text = f"*{escape_slack_text(subject)}*\n{escape_slack_text(body)}"
    message = message_payload(text, [{"type": "section", "text": mrkdwn(text)}])
    result = post_staff_slack_message(channel_id, message)
    if result.ok:
        return True
    logger.error(
        "Slack fallback rejected Relay %s alert campaign=%s channel=%s: %s",
        operation,
        campaign.pk,
        channel_id,
        result.error or type(result.exception).__name__,
    )
    return False


def _deliver_alert(campaign, operation, error, relay_key):
    subject = f"AISHL campaign {operation} failed: {campaign.pk}"
    body = (
        f"AISHL campaign {campaign.pk} Relay {operation} failed.\n"
        f"Relay key: {relay_key or campaign.relay_campaign_key or '(not assigned)'}\n"
        f"Error: {type(error).__name__}: {error}"
    )
    if _post_alert_to_slack(campaign, operation, subject, body):
        return True
    if _send_alert_email(campaign, operation, subject, body):
        return True
    logger.error(
        "Every alert route failed for AISHL campaign Relay %s failure campaign=%s",
        operation,
        campaign.pk,
    )
    return False


def alert_relay_campaign_failure(
    campaign, operation, error, *, relay_key=None, bypass_cooldown=False,
):
    """Alert operators on staff Slack, falling back to email; never raises.

    Returns True when at least one route accepted the alert. A call suppressed
    by the 60-minute per-campaign, per-operation cooldown delivers nothing and
    returns False. The stall watchdog passes ``bypass_cooldown`` because it
    deduplicates per Relay key on the campaign row instead.
    """
    try:
        if not bypass_cooldown and not _claim_cooldown(campaign, operation):
            return False
        return _deliver_alert(campaign, operation, error, relay_key)
    except Exception:
        # Broad on purpose: an alert failure must never mask the original error.
        logger.exception(
            "Relay campaign alert failed unexpectedly campaign=%s operation=%s",
            getattr(campaign, "pk", None),
            operation,
        )
        return False
