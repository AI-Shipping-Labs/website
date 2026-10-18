"""Daily runtime for announcing newly stuck study-group proposals."""

import logging

from django.utils import timezone

from community.slack_config import slack_api_enabled
from notifications.services.staff_slack import post_staff_slack_message, staff_alert_channel_id
from pods.models import MEETING_STATUS_PROPOSED, PodMeeting
from pods.services import config as pods_config
from pods.services.pod_alert_lines import proposals_label
from pods.services.pod_alert_payload import build_pods_alert_message
from pods.services.stuck_proposals import stuck_proposals

logger = logging.getLogger(__name__)


def _new_stuck_proposals(now):
    hours = pods_config.stuck_proposal_hours()
    stuck = stuck_proposals(now=now, hours=hours)
    new_proposals = []
    for wait in stuck:
        if wait.head.stuck_alerted_at is None:
            new_proposals.append(wait)
    if not new_proposals:
        logger.info("Stuck pod proposal alert: nothing new (%s still stuck)", len(stuck))
    return new_proposals, len(stuck) - len(new_proposals), hours


def _mark_proposals_announced(new_proposals, now):
    for wait in new_proposals:
        PodMeeting.objects.filter(
            pk=wait.head.pk,
            status=MEETING_STATUS_PROPOSED,
            moved_at=wait.head.moved_at,
            stuck_alerted_at__isnull=True,
        ).update(stuck_alerted_at=now)


def _delivery_target(subject):
    if not slack_api_enabled():
        logger.info("Stuck pod proposal alert skipped for %s: Slack API disabled", subject)
        return None
    channel_id = staff_alert_channel_id()
    if not channel_id:
        logger.info(
            "Stuck pod proposal alert skipped for %s: no "
            "STAFF_COMMENT_NOTIFY_CHANNEL_ID or STAFF_SIGNUP_NOTIFY_CHANNEL_ID",
            subject,
        )
    return channel_id


def _post(channel_id, subject, message):
    result = post_staff_slack_message(channel_id, message)
    if result.exception is not None:
        logger.error(
            "Stuck pod proposal Slack alert failed for %s (channel=%s)",
            subject,
            channel_id,
            exc_info=result.exception,
        )
        return False
    if not result.ok:
        logger.error(
            "Stuck pod proposal Slack alert rejected for %s (channel=%s): %s",
            subject,
            channel_id,
            result.error,
        )
        return False
    return True


def _send_stale_request_alert(*, now=None):
    if not pods_config.stuck_proposal_alert_enabled():
        logger.info("Stuck pod proposal alert skipped: PODS_STUCK_PROPOSAL_ALERT_ENABLED is off")
        return 0
    now = now or timezone.now()
    new_proposals, earlier_proposals, hours = _new_stuck_proposals(now)
    if not new_proposals:
        return 0
    subject = proposals_label(len(new_proposals))
    channel_id = _delivery_target(subject)
    if not channel_id:
        return 0
    message = build_pods_alert_message(
        [],
        0,
        new_proposals,
        earlier_proposals,
        stale_days=0,
        stuck_hours=hours,
        now=now,
    )
    if not _post(channel_id, subject, message):
        return 0
    _mark_proposals_announced(new_proposals, now)
    logger.info("Posted stuck pod proposal Slack alert for %s to channel=%s", subject, channel_id)
    return len(new_proposals)


def send_stale_request_alert(*, now=None):
    """Post the daily proposal alert without leaking an exception to the scheduler."""
    try:
        return _send_stale_request_alert(now=now)
    except Exception:
        logger.exception("Stale pod request Slack alert failed")
        return 0
