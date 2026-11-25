"""Shared recording pipeline staff Slack alert with existing dev mailbox fallback."""

import logging

from django.core.mail import send_mail

logger = logging.getLogger(__name__)
RECORDING_STALL_DEV_RECIPIENT = 'dev@dtcdev.click'


def deliver_pipeline_alert(event, subject, body):
    if _post_stall_alert_to_slack(event, subject, body):
        return True
    if _email_stall_alert(event, subject, body):
        return True
    logger.error(
        'Every alert route failed for the recording pipeline stall of event %s; '
        'retrying on the next pass',
        event.pk,
    )
    return False


def _post_stall_alert_to_slack(event, subject, body):
    from community.slack_config import slack_api_enabled
    from notifications.services.staff_slack import (
        escape_slack_text,
        message_payload,
        mrkdwn,
        post_staff_slack_message,
        staff_alert_channel_id,
    )

    if not slack_api_enabled():
        logger.error('Recording stall alert for event %s: Slack API disabled', event.pk)
        return False
    channel_id = staff_alert_channel_id()
    if not channel_id:
        logger.error('Recording stall alert for event %s: no staff channel', event.pk)
        return False
    text = f':rotating_light: *{escape_slack_text(subject)}*\n{escape_slack_text(body)}'
    message = message_payload(text, [{'type': 'section', 'text': mrkdwn(text)}])
    result = post_staff_slack_message(channel_id, message)
    if result.ok:
        return True
    logger.error(
        'Slack rejected the recording stall alert for event %s: %s',
        event.pk,
        result.error or type(result.exception).__name__,
    )
    return False


def _email_stall_alert(event, subject, body):
    try:
        delivered = send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[RECORDING_STALL_DEV_RECIPIENT],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Could not email the recording stall alert for event %s', event.pk)
        return False
    return bool(delivered)
