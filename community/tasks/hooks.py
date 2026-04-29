"""Background tasks for community actions triggered by payment hooks.

These are thin wrappers that load the user and delegate to the
CommunityService. They are enqueued by payments/services.py when
tier changes occur.
"""

import logging

from accounts.models import User
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


def community_invite_task(user_id):
    """Invite a user to the community. Called on checkout completion for Main+."""
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("community_invite_task: user %s not found", user_id)
        return

    if _should_skip_slack_hook("community_invite_task", user):
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
        return

    service = get_community_service()
    service.remove(user)
