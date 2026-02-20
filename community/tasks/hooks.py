"""Background tasks for community actions triggered by payment hooks.

These are thin wrappers that load the user and delegate to the
CommunityService. They are enqueued by payments/services.py when
tier changes occur.
"""

import logging

from accounts.models import User
from community.services import get_community_service

logger = logging.getLogger(__name__)


def community_invite_task(user_id):
    """Invite a user to the community. Called on checkout completion for Main+."""
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("community_invite_task: user %s not found", user_id)
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

    service = get_community_service()
    service.reactivate(user)


def community_remove_task(user_id):
    """Remove a user from the community. Called on subscription deletion."""
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error("community_remove_task: user %s not found", user_id)
        return

    service = get_community_service()
    service.remove(user)
