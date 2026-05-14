"""Background tasks for community actions triggered by payment hooks.

These are thin wrappers that load the user and delegate to the
CommunityService. They are enqueued by payments/services.py when
tier changes occur.
"""

import json
import logging

from accounts.models import User
from community.models import CommunityAuditLog
from community.services import get_community_service
from community.slack_config import (
    COMMUNITY_CHANNEL_KEYS,
    get_slack_community_channel_ids,
    get_slack_environment,
    slack_api_enabled,
)
from integrations.config import get_config, is_enabled

logger = logging.getLogger(__name__)


def _slack_skip_reasons():
    """Return missing Slack config keys that make hook tasks a no-op."""
    missing = []
    if not is_enabled("SLACK_ENABLED"):
        missing.append("SLACK_ENABLED=true")
    if not str(get_config("SLACK_BOT_TOKEN", "")).strip():
        missing.append("SLACK_BOT_TOKEN")

    slack_environment = get_slack_environment()
    channel_key = COMMUNITY_CHANNEL_KEYS[slack_environment]
    if not get_slack_community_channel_ids():
        missing.append(channel_key)

    if missing or not slack_api_enabled():
        return missing, slack_environment, channel_key
    return [], slack_environment, channel_key


def _should_skip_slack_hook(task_name, user):
    missing, slack_environment, channel_key = _slack_skip_reasons()
    if not missing:
        return False

    logger.warning(
        "%s skipped for user_id=%s email=%s because Slack community "
        "integration is not configured (%s). Configure Slack by setting "
        "SLACK_ENABLED=true, SLACK_BOT_TOKEN, and %s for "
        "SLACK_ENVIRONMENT=%s, then restart web and worker processes.",
        task_name,
        user.pk,
        user.email,
        ", ".join(missing),
        channel_key,
        slack_environment,
    )
    return True


def _send_invite_email_only(action, user):
    """Send the Slack invite email without calling the Slack API.

    Used when the Slack API is gated off (SLACK_ENABLED=false or other
    missing config) but SLACK_INVITE_URL is set -- the email body only
    needs the invite URL and never reads the bot token or channel list,
    so we instantiate SlackCommunityService with inert config purely to
    reuse the existing email helper. This keeps the email contract
    authoritative on the service and avoids duplicating the body.

    Writes a CommunityAuditLog row with status="email_sent" and
    reason="slack_api_disabled" to distinguish this fallback from the
    in-service slack_user_not_found path.
    """
    # Local import to limit blast radius if community.services.slack ever
    # grows a dependency that pulls community.tasks back in. The email
    # helper only calls django.core.mail.send_mail, so the bot_token=""
    # / channel_ids=[] constructor args are inert.
    from community.services.slack import SlackCommunityService  # noqa: PLC0415

    service = SlackCommunityService(bot_token="", channel_ids=[])
    service._send_invite_email(user)
    CommunityAuditLog.objects.create(
        user=user,
        action=action,
        details=json.dumps({
            "status": "email_sent",
            "reason": "slack_api_disabled",
        }),
    )
    logger.info(
        "Sent Slack invite email to user %s (action=%s) -- Slack API disabled",
        user.email, action,
    )


def community_invite_task(user_id):
    """Invite a user to the community. Called on checkout completion for Main+."""
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("community_invite_task: user %s not found", user_id)
        return

    if _should_skip_slack_hook("community_invite_task", user):
        # Slack API is gated off, but the invite email only needs
        # SLACK_INVITE_URL. Fall back to email-only when it is set.
        if str(get_config("SLACK_INVITE_URL", "")).strip():
            _send_invite_email_only("invite", user)
        return

    service = get_community_service()
    service.invite(user)


def community_reactivate_task(user_id):
    """Reactivate a user in the community. Called on re-subscribe to Main+."""
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("community_reactivate_task: user %s not found", user_id)
        return

    if _should_skip_slack_hook("community_reactivate_task", user):
        # Slack API is gated off, but the invite email only needs
        # SLACK_INVITE_URL. Fall back to email-only when it is set.
        if str(get_config("SLACK_INVITE_URL", "")).strip():
            _send_invite_email_only("reactivate", user)
        return

    service = get_community_service()
    service.reactivate(user)


def community_remove_task(user_id):
    """Remove a user from the community. Called on subscription deletion."""
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("community_remove_task: user %s not found", user_id)
        return

    if _should_skip_slack_hook("community_remove_task", user):
        # No email fallback here: SlackCommunityService.remove never calls
        # _send_invite_email, so there is no email path to preserve when
        # the Slack API is gated off.
        return

    service = get_community_service()
    service.remove(user)
