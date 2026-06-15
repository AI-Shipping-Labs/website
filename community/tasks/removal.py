"""Background job: scheduled community removal.

Enqueued when a user downgrades below Main tier or cancels. Runs at
billing_period_end to remove the user from community channels.

Usage:
    from jobs.tasks import async_task
    async_task(
        'community.tasks.removal.scheduled_community_removal',
        user_id=user.pk,
    )
"""

import logging

from accounts.models import User
from community.services import get_community_service
from content.access import get_user_level

logger = logging.getLogger(__name__)

# Minimum tier level for community access (Main tier = level 20)
COMMUNITY_TIER_LEVEL = 20


def scheduled_community_removal(user_id):
    """Remove a user from community channels if they no longer qualify.

    Called as a background job at billing_period_end. Checks whether
    the user's current tier still qualifies for community access
    (in case they re-subscribed before the removal ran).

    Args:
        user_id: Primary key of the User to remove.
    """
    try:
        user = User.objects.select_related("tier").get(pk=user_id)
    except User.DoesNotExist:
        logger.error(
            "Scheduled removal: user %s not found", user_id,
        )
        return

    # Check if the user still qualifies for community access by their
    # EFFECTIVE tier level. ``get_user_level`` returns
    # ``max(base_level, active non-expired override level)`` (and staff /
    # superuser get LEVEL_PREMIUM), matching the membership-reconcile
    # predicate (``slack_membership.main_plus_q``) and the email Main+
    # audience. An active, non-expired Main+ TierOverride therefore keeps
    # the member; an EXPIRED or deactivated override does not raise the
    # effective level, so those users are still removed.
    if get_user_level(user) >= COMMUNITY_TIER_LEVEL:
        logger.info(
            "Scheduled removal: user %s still qualifies for community "
            "access (effective level >= %s), skipping removal",
            user.email, COMMUNITY_TIER_LEVEL,
        )
        return

    service = get_community_service()
    service.remove(user)
    logger.info(
        "Scheduled removal: removed user %s from community channels",
        user.email,
    )
