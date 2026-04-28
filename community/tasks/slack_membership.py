"""Background job: refresh Slack workspace membership per user.

Runs every 30 minutes. For users whose ``slack_checked_at`` is NULL or
older than ``SLACK_MEMBERSHIP_REFRESH_DAYS``, calls Slack's
``users.lookupByEmail`` and updates the canonical ``slack_member`` flag
plus ``slack_checked_at`` timestamp.

Issue #358: ``slack_user_id`` is a poor proxy for "is in the workspace"
(OAuth populates it without a join), so we maintain an explicit verified
``slack_member`` boolean. Used by the dashboard CTA and campaign
targeting.
"""

import json
import logging
import time

from django.utils import timezone

from accounts.models import User
from community.models import CommunityAuditLog
from community.services import get_community_service

logger = logging.getLogger(__name__)

# Re-check workspace membership at most once a week to keep API spend low
# while still picking up users who joined Slack after signup.
SLACK_MEMBERSHIP_REFRESH_DAYS = 7

# Per-run cap. Slack Tier 4 limit is 50 RPM (~3000/hour). With 1.5s
# inter-call sleep that gives ~40 RPM, so 1000 users takes about 25 min,
# well under the 30-min schedule cadence.
SLACK_MEMBERSHIP_BATCH_SIZE = 1000

# Sleep between API calls to stay under Slack's Tier 4 rate limit.
# 50 RPM = 1.2s minimum gap; 1.5s gives headroom.
SLACK_MEMBERSHIP_SLEEP_SECONDS = 1.5

# Tag name mirrored on User.tags when issue #354 ships the tags primitive.
SLACK_MEMBER_TAG = "slack-member"


def _has_tags_field():
    """Detect whether issue #354 has shipped the ``User.tags`` field.

    The auto-tag mirror is forward-compatible: if the field doesn't
    exist yet we silently skip the tag write.
    """
    try:
        User._meta.get_field('tags')
        return True
    except Exception:
        return False


def _set_slack_member_tag(user, present):
    """Mirror the boolean as a ``slack-member`` tag on User.tags.

    No-op if #354 hasn't shipped or the tags field shape is unexpected.
    Failures are swallowed — the tag is a convenience mirror, not the
    canonical signal.
    """
    if not _has_tags_field():
        return
    try:
        tags = list(getattr(user, 'tags', []) or [])
        if present and SLACK_MEMBER_TAG not in tags:
            tags.append(SLACK_MEMBER_TAG)
            user.tags = tags
            user.save(update_fields=['tags'])
        elif not present and SLACK_MEMBER_TAG in tags:
            tags = [t for t in tags if t != SLACK_MEMBER_TAG]
            user.tags = tags
            user.save(update_fields=['tags'])
    except Exception:
        logger.warning(
            "Failed to mirror slack_member to tag for %s",
            getattr(user, 'email', '<unknown>'),
            exc_info=True,
        )


def _log_check_transition(user, previous_member, new_member):
    """Write a CommunityAuditLog row for a state transition.

    Caller is responsible for skipping no-op re-checks (same value as
    last time) so we don't flood the audit table.
    """
    CommunityAuditLog.objects.create(
        user=user,
        action="check",
        details=json.dumps({
            "previous": previous_member,
            "new": new_member,
            "source": "slack_membership_task",
        }),
    )


def refresh_slack_membership(
    *,
    batch_size=None,
    refresh_days=None,
    sleep_seconds=None,
):
    """Refresh ``slack_member`` for users with stale or missing checks.

    Selects up to ``batch_size`` users where ``slack_checked_at`` is
    NULL or older than ``refresh_days``, ordered NULLs first. For each
    user calls ``service.check_workspace_membership(email)``:

    - ``("member", uid)``: set ``slack_member=True``, fill
      ``slack_user_id`` if empty, set ``slack_checked_at=now()``.
    - ``("not_member", None)``: set ``slack_member=False``,
      ``slack_checked_at=now()``.
    - ``("unknown", None)``: leave fields alone — retry next cycle.

    Self-throttles to stay under Slack's Tier 4 rate limit. If the
    integration is unconfigured (no token), ``check_workspace_membership``
    returns ``unknown`` for everyone and this function becomes a safe
    no-op.

    Returns:
        dict: counts keyed by ``members``, ``not_members``, ``unknown``,
        ``total_checked``, ``transitions``.
    """
    batch_size = batch_size or SLACK_MEMBERSHIP_BATCH_SIZE
    refresh_days = refresh_days or SLACK_MEMBERSHIP_REFRESH_DAYS
    if sleep_seconds is None:
        sleep_seconds = SLACK_MEMBERSHIP_SLEEP_SECONDS

    service = get_community_service()
    cutoff = timezone.now() - timezone.timedelta(days=refresh_days)

    # NULLs first so brand-new users are picked up before stale ones.
    users = list(
        User.objects.filter(
            models_q_null_or_old(cutoff)
        )
        .order_by('slack_checked_at')[:batch_size]
    )

    members = 0
    not_members = 0
    unknown = 0
    transitions = 0

    for index, user in enumerate(users):
        # Self-throttle BETWEEN calls (not before the first one).
        if index > 0 and sleep_seconds:
            time.sleep(sleep_seconds)

        try:
            outcome, uid = service.check_workspace_membership(user.email)
        except Exception:
            logger.exception(
                "Unexpected error checking Slack membership for %s",
                user.email,
            )
            unknown += 1
            continue

        if outcome == "unknown":
            unknown += 1
            continue

        # Treat NULL slack_checked_at as a first check; both the
        # state flip member<->not_member and the first-ever check
        # are worth recording. Stale re-checks that return the same
        # value are skipped to keep the audit table small.
        is_first_check = user.slack_checked_at is None
        previous_member = bool(user.slack_member)
        now = timezone.now()

        if outcome == "member":
            update_fields = ['slack_member', 'slack_checked_at']
            user.slack_member = True
            user.slack_checked_at = now
            if uid and not user.slack_user_id:
                user.slack_user_id = uid
                update_fields.append('slack_user_id')
            user.save(update_fields=update_fields)
            _set_slack_member_tag(user, True)
            members += 1
            if is_first_check or previous_member is not True:
                transitions += 1
                _log_check_transition(user, previous_member, True)
        elif outcome == "not_member":
            user.slack_member = False
            user.slack_checked_at = now
            user.save(update_fields=['slack_member', 'slack_checked_at'])
            _set_slack_member_tag(user, False)
            not_members += 1
            if is_first_check or previous_member is not False:
                transitions += 1
                _log_check_transition(user, previous_member, False)

    summary = {
        "total_checked": len(users),
        "members": members,
        "not_members": not_members,
        "unknown": unknown,
        "transitions": transitions,
    }
    logger.info("Slack membership refresh complete: %s", summary)
    return summary


def models_q_null_or_old(cutoff):
    """Q object: ``slack_checked_at IS NULL OR slack_checked_at < cutoff``.

    Extracted so tests can introspect / re-use the predicate.
    """
    from django.db.models import Q
    return Q(slack_checked_at__isnull=True) | Q(slack_checked_at__lt=cutoff)
