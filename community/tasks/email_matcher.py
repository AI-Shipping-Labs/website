"""Background job: email matcher for community members.

Runs hourly. Finds users with community access (tier level >= 2) but no
slack_user_id, queries Slack API by email, and links matches.

Usage:
    # One-off execution
    from jobs.tasks import async_task
    async_task('community.tasks.email_matcher.match_community_emails')

    # Recurring schedule (registered in management command or startup)
    from jobs.tasks import schedule
    schedule('community.tasks.email_matcher.match_community_emails', cron='0 * * * *')
"""

import json
import logging

from accounts.models import User
from community.models import CommunityAuditLog
from community.services import get_community_service

logger = logging.getLogger(__name__)

# Minimum tier level for community access (Main tier = level 20)
COMMUNITY_TIER_LEVEL = 20


def match_community_emails():
    """Find community-eligible users without slack_user_id and match them.

    For each user with tier.level >= 20 and no slack_user_id:
    1. Look up their email in Slack via users.lookupByEmail
    2. If found: set slack_user_id, add to community channels
    3. Log the action

    Returns:
        dict: Summary with counts of matched, not_found, and errors.
    """
    service = get_community_service()

    # Find users with community-level tiers but no Slack ID
    users = User.objects.filter(
        tier__level__gte=COMMUNITY_TIER_LEVEL,
        slack_user_id="",
    ).select_related("tier")

    matched = 0
    not_found = 0
    errors = 0

    for user in users:
        try:
            slack_user_id = service.lookup_user_by_email(user.email)

            if slack_user_id:
                user.slack_user_id = slack_user_id
                user.save(update_fields=["slack_user_id"])

                # Add to community channels
                results = service.add_to_channels(slack_user_id)

                CommunityAuditLog.objects.create(
                    user=user,
                    action="link",
                    details=json.dumps({
                        "slack_user_id": slack_user_id,
                        "channels": results,
                        "source": "email_matcher",
                    }),
                )
                matched += 1
                logger.info(
                    "Email matcher: linked user %s to Slack ID %s",
                    user.email, slack_user_id,
                )
            else:
                not_found += 1
                logger.debug(
                    "Email matcher: user %s not found in Slack",
                    user.email,
                )
        except Exception:
            errors += 1
            logger.exception(
                "Email matcher: error processing user %s", user.email,
            )

    summary = {
        "total_checked": len(users),
        "matched": matched,
        "not_found": not_found,
        "errors": errors,
    }
    logger.info("Email matcher completed: %s", summary)
    return summary
